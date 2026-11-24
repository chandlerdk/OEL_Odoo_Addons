"""Tests for the native MCP write tools.

Exercises the write-side tool layer wired onto ``mcp.mixin`` and served through
``POST /mcp``:

* ``create_record`` / ``update_record`` / ``delete_record`` round-trip on a
  write-enabled model; a write-disabled model is blocked by the MCP per-op gate
  (``isError`` carrying the ``via MCP`` message); a low-privilege user is blocked
  by Odoo's own ACL (``AccessError`` surfaced as ``isError``, *not* the MCP gate).
* ``call_model_method`` is denied while ``allow_method_calls`` is off;
  once the flag is set a genuine business method (``message_post``, write-gated)
  runs, while a private (underscore-prefixed) method, an ORM CRUD method
  (``read``) and a generic ORM-surface method (``update``, on ``BaseModel``) are
  all rejected (the per-operation gates and the business-method-only boundary
  stay the real CRUD boundary).
* Each ``tools/call`` writes an ``mcp.log`` audit row -- a ``model_access`` row
  for a success (on the request cursor) and an ``error`` row for a failure (on
  the controller's independent committed cursor) -- which this suite asserts
  directly (audit failures are swallowed, so we query the rows rather than rely
  on an exception).
* The in-memory rate-limit primitives trip at the configured threshold.

Mirrors the HttpCase + ``_generate("rpc", ...)`` key-minting pattern of
``test_mcp_read_tools.py``; every request rides ``self.url_open`` so the HttpCase
test-cursor cookie travels with it -- required for the audit's independent cursor
(a ``TestCursor`` under HttpCase) to open and commit.

Note: ``mcp.log`` has no test-mode skip guard, so rows are logged whenever
``mcp_server.enable_logging`` is on; here we assert on the persisted rows.
"""

import json
import time
from unittest.mock import MagicMock, patch

from odoo import api
from odoo.exceptions import UserError
from odoo.tests import common, tagged
from odoo.tools import mute_logger

from ..controllers import error_sanitizer, mcp, rate_limiting, utils
from .test_helpers import create_test_user, grant_mcp_access, users_groups_field


@tagged("much_unit", "post_install", "-at_install")
class TestMcpWriteTools(common.HttpCase):
    """Native MCP write tools + method calls + audit/rate-limit."""

    def setUp(self):
        super().setUp()
        utils.clear_mcp_caches()
        rate_limiting._api_limiter.clear()
        # The error-audit independent-cursor write is capped per IP; the many
        # refused calls this suite issues would otherwise exhaust the budget
        # and silently drop the error / permission_denied rows asserted below.
        mcp._audit_write_limiter.clear()

        unique_id = str(int(time.time() * 1000))[-6:]

        groups_field = users_groups_field(self.env)

        # Key-owner user: internal user + Contact Creation, so it can fully
        # CRUD res.partner (the bare internal group is read-only on it), but
        # NOT ir.mail_server which is group_system only.
        self.mcp_user = create_test_user(
            self.env,
            "MCP Write User",
            f"mcp_write_user_{unique_id}",
            email=f"mcp_write_{unique_id}@example.com",
            **{
                groups_field: [
                    (
                        6,
                        0,
                        [
                            self.env.ref("base.group_user").id,
                            self.env.ref("base.group_partner_manager").id,
                        ],
                    )
                ]
            },
        )
        grant_mcp_access(self.mcp_user)
        self.api_key = self._mint_key(self.mcp_user, "Write Tools Key")

        # Low-privilege user pinned to the bare internal-user group: blocked by
        # Odoo ACL on an admin-only model even when MCP allows the operation.
        self.low_priv_user = create_test_user(
            self.env,
            "MCP Low Priv Write User",
            f"mcp_lowpriv_write_{unique_id}",
            email=f"mcp_lowpriv_write_{unique_id}@example.com",
            **{groups_field: [(6, 0, [self.env.ref("base.group_user").id])]},
        )
        # The door requires the MCP group; the user stays "low priv" for the
        # Odoo ACL assertions (no extra functional groups).
        grant_mcp_access(self.low_priv_user)
        self.low_priv_key = self._mint_key(self.low_priv_user, "Low Priv Write Key")

        # res.partner: fully write-enabled, method calls OFF (toggled per-test).
        self.partner_model = self._enable_model(
            "base.model_res_partner",
            allow_read=True,
            allow_create=True,
            allow_write=True,
            allow_unlink=True,
            allow_method_calls=False,
        )
        # res.country: read-only via MCP -> create is blocked by the per-op gate.
        self._enable_model("base.model_res_country", allow_read=True)
        # ir.mail_server: MCP create allowed, but admin-only in Odoo -> the
        # low-priv user is blocked by the ORM (AccessError, not the MCP gate).
        self._enable_model(
            "base.model_ir_mail_server", allow_read=True, allow_create=True
        )
        # res.partner.category: ``name`` is required with no default -> a
        # deterministic fixture for batch atomicity (one bad entry, zero rows).
        self._enable_model(
            "base.model_res_partner_category",
            allow_read=True,
            allow_create=True,
            allow_write=True,
        )

        # A target record for call_model_method.
        self.partner = self.env["res.partner"].create(
            {"name": f"MCP Method Target {unique_id}"}
        )

        params = self.env["ir.config_parameter"].sudo()
        params.set_param("mcp_server.enabled", "True")
        params.set_param("mcp_server.enable_logging", "True")
        utils.clear_mcp_caches()

    # ------------------------------------------------------------------
    # Fixture helpers
    # ------------------------------------------------------------------
    def _mint_key(self, user, name):
        """Mint an ``rpc``-scope API key for ``user``."""
        return self.env(user=user)["res.users.apikeys"]._generate("rpc", name)

    def _enable_model(self, model_xmlid, **perms):
        """Find-or-create an ``mcp.enabled.model`` row for ``model_xmlid``."""
        model_id = self.env.ref(model_xmlid).id
        record = (
            self.env["mcp.enabled.model"]
            .sudo()
            .with_context(active_test=False)
            .search([("model_id", "=", model_id)], limit=1)
        )
        vals = {"active": True, **perms}
        if record:
            record.write(vals)
        else:
            record = (
                self.env["mcp.enabled.model"]
                .sudo()
                .create({"model_id": model_id, **vals})
            )
        return record

    def _set_method_calls(self, enabled):
        """Toggle ``allow_method_calls`` on res.partner and refresh caches."""
        self.partner_model.allow_method_calls = enabled
        self.env.flush_all()
        utils.clear_mcp_caches()

    # ------------------------------------------------------------------
    # RPC helpers
    # ------------------------------------------------------------------
    _DEFAULT_KEY = object()

    def _post_rpc(self, body, api_key=_DEFAULT_KEY):
        """POST a JSON-RPC ``body`` dict to ``/mcp`` with bearer auth."""
        headers = {"Content-Type": "application/json"}
        if api_key is self._DEFAULT_KEY:
            api_key = self.api_key
        if api_key is not None:
            headers["Authorization"] = f"Bearer {api_key}"
        return self.url_open("/mcp", data=json.dumps(body), headers=headers)

    def _call_tool(self, name, arguments=None, api_key=_DEFAULT_KEY):
        """Invoke ``tools/call`` and return the tool-result dict."""
        response = self._post_rpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            },
            api_key=api_key,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("error", payload, msg=payload.get("error"))
        return payload["result"]

    # ------------------------------------------------------------------
    # Concurrency-conflict retry (service.model.retrying)
    # ------------------------------------------------------------------
    def test_serialization_failure_propagates_not_swallowed(self):
        """A concurrency conflict propagates as a JSON-RPC error, not an isError.

        A tool raising psycopg2 ``SerializationFailure`` (serialization failure /
        deadlock) must NOT be swallowed by the generic ``except`` into an
        ``isError`` tool result -- that robs Odoo's ``service.model.retrying`` of
        the exception it needs to retry, turning a transient conflict into a
        spurious internal error. Assert it surfaces as a top-level JSON-RPC error
        with no tool ``result`` (the buggy path returned
        ``{"result": {"isError": true}}`` here). The retry itself is core's job
        once the exception reaches it; it is not re-exercised under the HttpCase
        test cursor (a synthetic error carries no ``pgcode`` to loop on).
        """
        from psycopg2.errors import SerializationFailure

        mixin_cls = type(self.env["mcp.mixin"])
        boom = MagicMock(side_effect=SerializationFailure("could not serialize access"))
        with mute_logger(
            "odoo.addons.mcp_server.controllers.mcp_dispatcher",
            "odoo.http",
            "odoo.service.model",
        ), patch.object(mixin_cls, "_resolve_model", boom):
            response = self._post_rpc(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {
                        "name": "create_record",
                        "arguments": {
                            "model": "res.partner",
                            "values": {"name": "conflict"},
                        },
                    },
                }
            )
        payload = response.json()
        # Propagated as a JSON-RPC error, not swallowed into a tool result.
        self.assertIsNone(payload.get("result"), msg=payload)
        self.assertIn("error", payload)
        # The tool actually ran and hit the injected conflict.
        self.assertTrue(boom.called)

    # ------------------------------------------------------------------
    # create / update / delete round-trip
    # ------------------------------------------------------------------
    def test_create_update_delete_roundtrip(self):
        """create -> update -> delete a record on a write-enabled model."""
        create_res = self._call_tool(
            "create_record",
            {"model": "res.partner", "values": {"name": "MCP Created"}},
        )
        self.assertFalse(create_res["isError"], msg=create_res)
        new_id = create_res["structuredContent"]["record"]["id"]
        self.assertTrue(new_id)
        url = create_res["structuredContent"]["url"]
        self.assertTrue(
            url.endswith(f"/web#id={new_id}&model=res.partner&view_type=form"), url
        )

        self.env.invalidate_all()
        partner = self.env["res.partner"].browse(new_id)
        self.assertTrue(partner.exists())
        self.assertEqual(partner.name, "MCP Created")

        update_res = self._call_tool(
            "update_record",
            {
                "model": "res.partner",
                "record_id": new_id,
                "values": {"name": "MCP Updated"},
            },
        )
        self.assertFalse(update_res["isError"], msg=update_res)
        self.env.invalidate_all()
        self.assertEqual(self.env["res.partner"].browse(new_id).name, "MCP Updated")

        delete_res = self._call_tool(
            "delete_record", {"model": "res.partner", "record_id": new_id}
        )
        self.assertFalse(delete_res["isError"], msg=delete_res)
        self.env.invalidate_all()
        self.assertFalse(self.env["res.partner"].browse(new_id).exists())

    def test_create_on_write_disabled_model_is_blocked(self):
        """A create on a read-only-via-MCP model is refused by the per-op gate."""
        result = self._call_tool(
            "create_record",
            {"model": "res.country", "values": {"name": "MCP Testland"}},
        )
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        self.assertIn("not allowed", text.lower())
        self.assertIn("via MCP", text)  # the MCP gate, not an ORM error
        self.assertNotIn("Traceback", text)

    def test_low_privilege_user_blocked_by_acl(self):
        """A low-priv user creating an admin-only model is blocked by Odoo ACL."""
        result = self._call_tool(
            "create_record",
            {"model": "ir.mail_server", "values": {"name": "MCP SMTP"}},
            api_key=self.low_priv_key,
        )
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        self.assertTrue(text.strip())
        self.assertNotIn("Traceback", text)
        # The MCP gate allowed create here; the refusal comes from Odoo's ACL.
        self.assertNotIn("via MCP", text)

    # ------------------------------------------------------------------
    # call_model_method
    # ------------------------------------------------------------------
    def test_call_model_method_denied_when_flag_off(self):
        """With allow_method_calls off, call_model_method is denied."""
        result = self._call_tool(
            "call_model_method",
            {
                "model": "res.partner",
                "method": "read",
                "record_ids": [self.partner.id],
                "args": [["name"]],
            },
        )
        self.assertTrue(result["isError"])
        self.assertIn("not enabled", result["content"][0]["text"].lower())

    def test_call_model_method_caps_record_ids(self):
        """A record_ids list longer than max_limit is refused before browsing.

        Unlike the read tools, this path has no built-in size cap; an unbounded
        id list is a large ``IN(...)`` + unbounded result serialization. The cap
        reuses ``max_limit`` and trips regardless of whether the ids exist.
        """
        self._set_method_calls(True)
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.max_limit", "5")

        result = self._call_tool(
            "call_model_method",
            {
                "model": "res.partner",
                "method": "message_post",
                "record_ids": [1, 2, 3, 4, 5, 6],
            },
        )
        self.assertTrue(result["isError"], msg=result)
        self.assertIn("too many", result["content"][0]["text"].lower())

    def test_call_model_method_rejects_non_list_record_ids(self):
        """A non-list record_ids (e.g. the string "12") is rejected, not iterated.

        ``"12"`` is truthy and iterable, so ``[int(c) for c in "12"]`` would
        silently expand to ids ``[1, 2]`` and act on the wrong records. Like
        ``args``/``kwargs``, ``record_ids`` must be type-checked so the client
        gets a clean error instead of a silent misinterpretation.
        """
        self._set_method_calls(True)
        result = self._call_tool(
            "call_model_method",
            {
                "model": "res.partner",
                "method": "message_post",
                "record_ids": "12",
            },
        )
        self.assertTrue(result["isError"], msg=result)
        self.assertIn("must be a list", result["content"][0]["text"].lower())

    def test_call_model_method_own_kwargs_check_raises_its_message(self):
        """The tool's own ``kwargs`` check raises its message, not a crash.

        Odoo's ``_()`` reads a caller local named ``kwargs`` as a dict, so the
        check must not keep the client value under that name.
        """
        self._set_method_calls(True)
        # The gate helpers read the master switch off ``request.env``; bind a
        # request-like object, since the tool is called without the HTTP layer.
        mock_request = MagicMock()
        mock_request.env = self.env
        with (
            patch("odoo.addons.mcp_server.controllers.utils.request", mock_request),
            self.assertRaisesRegex(UserError, "'kwargs' must be an object"),
        ):
            self.env["mcp.mixin"].call_model_method(
                "res.partner", "default_get", kwargs="x"
            )

    def test_wrong_typed_arguments_refused_before_the_tool_runs(self):
        """Wrong-typed builtin arguments get a precise isError, not a crash.

        Each used to reach the tool and fail as an unhandled Python error
        ("Internal server error"); the schema check names the argument and
        nothing runs (no message is posted, no attachment is created).
        """
        self._set_method_calls(True)
        messages = self.env["mail.message"].sudo()
        attachments = self.env["ir.attachment"].sudo()
        before = (messages.search_count([]), attachments.search_count([]))
        pid = self.partner.id
        refused = {
            "kwargs list": (
                "call_model_method",
                {"model": "res.partner", "method": "message_post", "kwargs": ["x"]},
                "Invalid argument 'kwargs': must be an object, got a list.",
            ),
            "kwargs string": (
                "call_model_method",
                {"model": "res.partner", "method": "message_post", "kwargs": "x"},
                "Invalid argument 'kwargs': must be an object, got a string.",
            ),
            "subtype": (
                "post_message",
                {"model": "res.partner", "record_id": pid, "body": "b", "subtype": 5},
                "Invalid argument 'subtype': must be a string, got an integer.",
            ),
            "mimetype": (
                "upload_attachment",
                {"name": "a.txt", "data": "aGk=", "mimetype": 5},
                "Invalid argument 'mimetype': must be a string or null, got an "
                "integer.",
            ),
        }
        for label, (tool, arguments, message) in refused.items():
            with self.subTest(label):
                result = self._call_tool(tool, arguments)
                self.assertTrue(result["isError"], msg=result)
                self.assertEqual(result["content"][0]["text"], message)
        self.assertEqual(
            (messages.search_count([]), attachments.search_count([])), before
        )

    def test_in_tool_type_checks_hold_without_the_schema_check(self):
        """Called directly (no /mcp schema check), the tools refuse bad types cleanly.

        Odoo's ``_()`` reads a local named ``kwargs`` as a dict holding a
        ``context``, so a client ``kwargs`` of ``"x"`` or ``{"context": "x"}``
        crashed every message of call_model_method with an AttributeError; a
        non-string subtype / mimetype crashed on ``.strip()`` or at create.
        """
        self._set_method_calls(True)
        # The gates read the master switch off ``request.env``; bind a
        # request-like object to the test env (no HTTP layer here).
        patcher = patch(
            "odoo.addons.mcp_server.controllers.utils.request", MagicMock(env=self.env)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        mixin = self.env["mcp.mixin"].with_user(self.mcp_user)
        pid = self.partner.id
        cases = {
            "kwargs string": (
                lambda: mixin.call_model_method(
                    "res.partner", "message_post", record_ids=[pid], kwargs="x"
                ),
                "'kwargs' must be an object.",
            ),
            "kwargs with a string context, early message": (
                lambda: mixin.call_model_method(
                    "res.partner", " ", kwargs={"context": "x"}
                ),
                "No method name provided.",
            ),
            "kwargs with a string context": (
                lambda: mixin.call_model_method(
                    "res.partner", "message_post", args=5, kwargs={"context": "x"}
                ),
                "'args' must be a list.",
            ),
            "subtype": (
                lambda: mixin.post_message("res.partner", pid, "b", subtype=5),
                "Invalid subtype '5'",
            ),
            "mimetype": (
                lambda: mixin.upload_attachment("a.txt", "aGk=", mimetype=5),
                "'mimetype' must be a string.",
            ),
        }
        for label, (call, message) in cases.items():
            with self.subTest(label):
                with self.assertRaises(UserError) as cm:
                    call()
                self.assertIn(message, str(cm.exception))

    def test_call_model_method_rejects_partial_missing_ids(self):
        """A batch with any non-existent record_id is refused, not silently run.

        ``[valid, 999999999]`` must not run on the valid subset and report
        success -- the missing id is surfaced as an error (like the single-record
        tools), and the valid record is left untouched.
        """
        self._set_method_calls(True)
        result = self._call_tool(
            "call_model_method",
            {
                "model": "res.partner",
                "method": "message_post",
                "record_ids": [self.partner.id, 999999999],
                "kwargs": {"body": "should not post"},
            },
        )
        self.assertTrue(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertIn("not found", text.lower())
        self.assertIn("999999999", text)
        # The whole call was rejected before running -> the valid record was not
        # posted to (no silent partial side effect).
        self.env.invalidate_all()
        posted = self.env["mail.message"].search_count(
            [
                ("model", "=", "res.partner"),
                ("res_id", "=", self.partner.id),
                ("body", "like", "should not post"),
            ]
        )
        self.assertEqual(posted, 0)

    def test_call_model_method_public_runs_when_enabled(self):
        """With allow_method_calls on, a genuine business method runs.

        Uses ``message_post`` -- a real business method contributed by
        ``mail.thread`` (NOT an attribute of ``BaseModel``), mapped to the
        'write' operation. With ``allow_method_calls`` AND ``allow_write`` both
        on (allow_write is set in setUp) it runs and actually posts a chatter
        message, exercising the business-method allow-path through the per-op
        gate.
        """
        self._set_method_calls(True)
        before = self.env["mail.message"].search_count(
            [("model", "=", "res.partner"), ("res_id", "=", self.partner.id)]
        )
        result = self._call_tool(
            "call_model_method",
            {
                "model": "res.partner",
                "method": "message_post",
                "record_ids": [self.partner.id],
                "kwargs": {"body": "business method ran"},
            },
        )
        self.assertFalse(result["isError"], msg=result)
        self.assertTrue(result["structuredContent"]["success"])
        # The business method really executed -> a new chatter message exists.
        self.env.invalidate_all()
        after = self.env["mail.message"].search_count(
            [("model", "=", "res.partner"), ("res_id", "=", self.partner.id)]
        )
        self.assertEqual(after, before + 1)

    def test_json_safe_caps_returned_recordset(self):
        """A recordset return is bounded so the output size cannot blow up.

        ``call_model_method`` caps its INPUT (record_ids) but a method can still
        return a huge recordset; ``_json_safe`` serializes one ``display_name``
        per record, so it caps the recordset and appends a truncation marker
        rather than dumping it unbounded.
        """
        from ..models.mcp_tools_write import _json_safe

        partners = self.env["res.partner"].create(
            [{"name": f"Cap Partner {i}"} for i in range(3)]
        )
        serialized = _json_safe(partners, max_records=2)
        # First two records as {id, display_name}, then a truncation marker.
        self.assertEqual(len(serialized), 3)
        self.assertEqual(serialized[0]["id"], partners[0].id)
        self.assertEqual(serialized[1]["id"], partners[1].id)
        self.assertIsInstance(serialized[2], str)
        self.assertIn("truncated", serialized[2])
        self.assertIn("2 of 3", serialized[2])
        # Within the cap: no marker appended.
        self.assertEqual(len(_json_safe(partners, max_records=5)), 3)

    def test_call_model_method_response_truncates_large_recordset(self):
        """A method returning a recordset > MAX_LIMIT is truncated in the RESPONSE.

        The isolated ``_json_safe`` test above pins the helper; this drives the
        whole ``call_model_method`` path end-to-end and asserts the cap + marker
        actually reach the tool result (structuredContent and text), not just the
        helper. A public business method is patched onto res.partner to return a
        recordset one larger than the serialization cap.
        """
        from ..models.mcp_tools_read import MAX_LIMIT

        self._set_method_calls(True)
        self.env["res.partner"].create(
            [{"name": f"Big Recordset Partner {i}"} for i in range(MAX_LIMIT + 1)]
        )

        def fake_big(records_self):
            # Return a recordset larger than the _json_safe cap; resolved in the
            # request's own env (shared test cursor) so no cross-env recordset.
            return records_self.search([("name", "=like", "Big Recordset Partner%")])

        partner_cls = type(self.env["res.partner"])
        with patch.object(partner_cls, "action_mcp_test_big", fake_big, create=True):
            result = self._call_tool(
                "call_model_method",
                {"model": "res.partner", "method": "action_mcp_test_big"},
            )

        self.assertFalse(result["isError"], msg=result)
        returned = result["structuredContent"]["result"]
        # MAX_LIMIT records serialized + a trailing truncation-marker string.
        self.assertEqual(len(returned), MAX_LIMIT + 1)
        self.assertIsInstance(returned[-1], str)
        self.assertIn("truncated", returned[-1])
        self.assertIn("%d of %d" % (MAX_LIMIT, MAX_LIMIT + 1), returned[-1])
        # The marker also reaches the human-readable text block.
        self.assertIn("truncated", result["content"][0]["text"])

    def test_call_model_method_generic_orm_method_blocked(self):
        """A generic ORM method (``update``) is refused via MCP.

        ``update`` is a public ``BaseModel`` method that mutates records, yet it
        is NOT in the CRUD denylist nor the per-op map -- only the generic-ORM
        -surface block (``hasattr(BaseModel, method)``) stops it. With
        ``allow_method_calls`` AND ``allow_write`` both on it must STILL be
        refused (it is not a model business method), and the record left
        unchanged. Guards the principled allow-business-methods-only boundary.
        """
        self._set_method_calls(True)  # allow_write already on from setUp
        original_name = self.partner.name
        result = self._call_tool(
            "call_model_method",
            {
                "model": "res.partner",
                "method": "update",
                "record_ids": [self.partner.id],
                "args": [{"name": "HACKED VIA UPDATE"}],
            },
        )
        self.assertTrue(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertIn("generic orm", text.lower())
        self.assertNotIn("Traceback", text)
        # The blocked update must never have mutated the record.
        self.env.invalidate_all()
        self.assertEqual(
            self.env["res.partner"].browse(self.partner.id).name, original_name
        )

    def test_call_model_method_crud_method_blocked(self):
        """An ORM CRUD method (read) is refused even when method calls are on.

        ``allow_method_calls`` must not become a CRUD backdoor: the per-op MCP
        flags (allow_read/create/write/unlink) stay the real boundary.
        """
        self._set_method_calls(True)
        result = self._call_tool(
            "call_model_method",
            {
                "model": "res.partner",
                "method": "read",
                "record_ids": [self.partner.id],
                "args": [["name"]],
            },
        )
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"].lower()
        self.assertIn("crud", text)
        self.assertNotIn("Traceback", result["content"][0]["text"])

    def test_call_model_method_web_save_blocked(self):
        """The public ``web_save`` (CRUD via the web addon) is blocked.

        ``web_save`` writes/creates and would bypass allow_create/allow_write,
        so call_model_method must refuse the whole ``web_*`` family even when
        ``allow_method_calls`` is on -- the per-op gates stay the CRUD boundary.
        """
        self._set_method_calls(True)
        result = self._call_tool(
            "call_model_method",
            {
                "model": "res.partner",
                "method": "web_save",
                "record_ids": [self.partner.id],
                "args": [{"ref": "HACKED"}, []],
            },
        )
        self.assertTrue(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertIn("data-access", text.lower())
        self.assertNotIn("Traceback", text)
        # The blocked web_save must never have mutated the record.
        self.env.invalidate_all()
        self.assertNotEqual(
            self.env["res.partner"].browse(self.partner.id).ref, "HACKED"
        )

    def test_call_model_method_base_registry_method_blocked(self):
        """``get_views`` (generic API on the ``base`` registry model) is blocked.

        ``get_views``/``get_view`` are contributed to the abstract ``base`` model
        by web+base, so they are NOT ``BaseModel`` Python attributes and slip past
        the ``hasattr(BaseModel, method)`` backstop. They return field/view
        metadata (like the hard-blocked ``fields_get``), so call_model_method must
        refuse them even with ``allow_method_calls`` on -- via the ``base``
        registry-model check.
        """
        self._set_method_calls(True)
        result = self._call_tool(
            "call_model_method",
            {
                "model": "res.partner",
                "method": "get_views",
                "args": [[[False, "form"]]],
            },
        )
        self.assertTrue(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertIn("generic orm", text.lower())
        self.assertNotIn("Traceback", text)

    def test_call_model_method_private_rejected(self):
        """A private (underscore-prefixed) method is rejected even when enabled."""
        self._set_method_calls(True)
        result = self._call_tool(
            "call_model_method",
            {
                "model": "res.partner",
                "method": "_compute_display_name",
                "record_ids": [self.partner.id],
            },
        )
        self.assertTrue(result["isError"])
        self.assertIn("private", result["content"][0]["text"].lower())

    def _patch_method(self, cls, name, func):
        patcher = patch.object(cls, name, func, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _call_partner_method(self, method):
        return self._call_tool(
            "call_model_method",
            {"model": "res.partner", "method": method, "record_ids": [self.partner.id]},
        )

    def test_call_model_method_api_private_rejected(self):
        """A public-named method marked @api.private is refused like a ``_`` one.

        The marking counts at any level of the MRO: a parent class marks the
        method, the registry class redefines it unmarked, and the call is still
        refused, as core's RPC refuses it. An unmarked public method still runs.
        """
        if not hasattr(api, "private"):
            self.skipTest("this Odoo build has no api.private")
        partner_cls = type(self.env["res.partner"])
        parent_cls = partner_cls.mro()[1]
        self._patch_method(partner_cls, "mcp_probe_leaf", api.private(lambda self: 1))
        self._patch_method(parent_cls, "mcp_probe_parent", api.private(lambda self: 1))
        self._patch_method(partner_cls, "mcp_probe_parent", lambda self: 1)
        self._patch_method(partner_cls, "mcp_probe_public", lambda self: 1)

        # The opt-in gate still answers first.
        result = self._call_partner_method("mcp_probe_leaf")
        self.assertTrue(result["isError"])
        self.assertIn("not enabled", result["content"][0]["text"])

        self._set_method_calls(True)
        for method in ("mcp_probe_leaf", "mcp_probe_parent"):
            with self.subTest(method=method):
                result = self._call_partner_method(method)
                self.assertTrue(result["isError"], msg=result)
                self.assertEqual(
                    result["content"][0]["text"],
                    f"Private methods cannot be called via MCP: {method}",
                )
        result = self._call_partner_method("mcp_probe_public")
        self.assertFalse(result["isError"], msg=result)

    def test_call_model_method_core_private_method_rejected(self):
        """Core's private signup_get_auth_param (auth_signup) is refused."""
        partner_cls = type(self.env["res.partner"])
        if not any(
            getattr(getattr(cls, "signup_get_auth_param", None), "_api_private", False)
            for cls in partner_cls.mro()
        ):
            self.skipTest("auth_signup is not installed or does not mark it private")
        self._set_method_calls(True)
        result = self._call_partner_method("signup_get_auth_param")
        self.assertTrue(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertIn("Private methods cannot be called via MCP", text)
        self.assertNotIn("auth_signup_token", json.dumps(result))

    def test_gate_refusals_are_audited_as_denials(self):
        """The write gate and the private-method rule write permission_denied rows."""
        self.env["mcp.enabled.model"].sudo().with_context(active_test=False).search(
            [("model_id.model", "=", "res.partner.bank")]
        ).write({"active": False})
        utils.clear_mcp_caches()
        result = self._call_tool(
            "create_record",
            {
                "model": "res.partner",
                "values": {"name": "Audit", "bank_ids": [[0, 0, {"acc_number": "A1"}]]},
            },
        )
        self.assertTrue(result["isError"], msg=result)
        self._patch_method(
            type(self.env["res.partner"]),
            "mcp_probe_audit",
            api.private(lambda self: 1),
        )
        self._set_method_calls(True)
        result = self._call_partner_method("mcp_probe_audit")
        self.assertTrue(result["isError"], msg=result)
        # A name that starts with "_" is refused as private too.
        result = self._call_partner_method("_mcp_probe_hidden")
        self.assertTrue(result["isError"], msg=result)
        rows = (
            self.env["mcp.log"]
            .sudo()
            .search(
                [
                    ("event_type", "=", "permission_denied"),
                    ("user_id", "=", self.mcp_user.id),
                    ("model_name", "=", "res.partner"),
                ]
            )
        )
        self.assertEqual(
            sorted(rows.mapped("tool_name")),
            ["call_model_method", "call_model_method", "create_record"],
        )

    def test_call_model_method_refuses_non_instance_methods(self):
        """A classmethod, staticmethod or property is refused, and no property runs."""
        partner_cls = type(self.env["res.partner"])
        runs = []
        self._patch_method(partner_cls, "mcp_probe_cls", classmethod(lambda cls: 1))
        self._patch_method(partner_cls, "mcp_probe_static", staticmethod(lambda: 1))
        self._patch_method(
            partner_cls, "mcp_probe_prop", property(lambda rec: runs.append(1) or 1)
        )
        self._set_method_calls(True)
        for method in ("mcp_probe_cls", "mcp_probe_static", "mcp_probe_prop"):
            with self.subTest(method=method):
                result = self._call_partner_method(method)
                self.assertTrue(result["isError"], msg=result)
        self.assertEqual(runs, [], "the property must not run")

    def test_post_message_enum_value_reaches_the_tool_as_declared(self):
        """An enum argument matched ignoring case arrives as the declared option."""
        result = self._call_tool(
            "post_message",
            {
                "model": "res.partner",
                "record_id": self.partner.id,
                "body": "enum probe",
                "message_type": " Comment ",
            },
        )
        self.assertFalse(result["isError"], msg=result)
        message = (
            self.env["mail.message"]
            .sudo()
            .browse(result["structuredContent"]["message_id"])
        )
        self.assertEqual(message.message_type, "comment")
        # An exact match wins over a match that ignores case.
        self.assertEqual(mcp._canonical_enum("m", ["M", "m"]), "m")
        self.assertEqual(mcp._canonical_enum(" m ", ["M", "m"]), "M")

    def test_update_accepts_a_name_that_fields_get_offers(self):
        """A name that fields_get offers without being a field is not refused."""
        partner_cls = type(self.env["res.partner"])
        original = partner_cls.fields_get

        def fields_get(rec, allfields=None, attributes=None):
            result = original(rec, allfields, attributes)
            result["mcp_virtual_flag"] = {"type": "boolean"}
            return result

        self._patch_method(partner_cls, "fields_get", fields_get)
        tools = self.env["mcp.mixin"]
        tools._check_write_fields(self.env["res.partner"], {"mcp_virtual_flag": True})
        with self.assertRaisesRegex(UserError, "Invalid field 'no_such_field'"):
            tools._check_write_fields(self.env["res.partner"], {"no_such_field": 1})

    def test_call_model_method_per_op_gate_authoritative(self):
        """A KNOWN data-access method honours its per-op flag.

        ``message_post`` maps to the 'write' operation. With ``allow_method_calls``
        on but ``allow_write`` OFF it must be refused -- the per-op flags stay the
        authoritative boundary even through the method-call hatch, so the denylist
        misses (message_post/toggle_active/...) cannot slip through. Once
        ``allow_write`` is on, the same call runs.
        """
        # allow_method_calls on, but write OFF -> message_post (write) refused.
        self._enable_model(
            "base.model_res_partner",
            allow_read=True,
            allow_create=True,
            allow_write=False,
            allow_unlink=True,
            allow_method_calls=True,
        )
        utils.clear_mcp_caches()
        denied = self._call_tool(
            "call_model_method",
            {
                "model": "res.partner",
                "method": "message_post",
                "record_ids": [self.partner.id],
                "kwargs": {"body": "should be blocked"},
            },
        )
        self.assertTrue(denied["isError"], msg=denied)
        text = denied["content"][0]["text"]
        self.assertIn("write", text.lower())
        self.assertNotIn("Traceback", text)

        # Flip allow_write ON -> the same known write method now runs.
        self._enable_model(
            "base.model_res_partner",
            allow_read=True,
            allow_create=True,
            allow_write=True,
            allow_unlink=True,
            allow_method_calls=True,
        )
        utils.clear_mcp_caches()
        allowed = self._call_tool(
            "call_model_method",
            {
                "model": "res.partner",
                "method": "message_post",
                "record_ids": [self.partner.id],
                "kwargs": {"body": "now allowed"},
            },
        )
        self.assertFalse(allowed["isError"], msg=allowed)
        self.assertTrue(allowed["structuredContent"]["success"])

    # ------------------------------------------------------------------
    # create_records (batch)
    # ------------------------------------------------------------------
    def test_create_records_creates_all_in_one_call(self):
        """N entries -> N records; the result lists id/display_name/url + count."""
        marker = "MCP Batch %s" % int(time.time() * 1000000)
        names = [f"{marker} {i}" for i in range(3)]
        result = self._call_tool(
            "create_records",
            {"model": "res.partner", "records": [{"name": n} for n in names]},
        )
        self.assertFalse(result["isError"], msg=result)
        structured = result["structuredContent"]
        self.assertTrue(structured["success"])
        self.assertEqual(structured["count"], 3)
        self.assertEqual(len(structured["records"]), 3)
        self.assertEqual([r["display_name"] for r in structured["records"]], names)
        for row in structured["records"]:
            self.assertTrue(
                row["url"].endswith(
                    f"/web#id={row['id']}&model=res.partner&view_type=form"
                ),
                row["url"],
            )
        self.assertIn("created 3 res.partner records", structured["message"])
        text = result["content"][0]["text"]
        for row in structured["records"]:
            self.assertIn(f"- [{row['id']}] {row['display_name']}", text)

        self.env.invalidate_all()
        created = self.env["res.partner"].search([("name", "like", marker)])
        self.assertEqual(
            sorted(created.ids), sorted(r["id"] for r in structured["records"])
        )

    def test_create_records_single_entry_batch(self):
        """A batch of one is accepted and reports count 1."""
        result = self._call_tool(
            "create_records",
            {"model": "res.partner", "records": [{"name": "MCP Batch Solo"}]},
        )
        self.assertFalse(result["isError"], msg=result)
        self.assertEqual(result["structuredContent"]["count"], 1)

    @mute_logger("odoo.sql_db", "odoo.addons.mcp_server.controllers.error_sanitizer")
    def test_create_records_is_atomic(self):
        """One entry missing a required field fails the batch; zero rows land."""
        marker = "MCP Atomic Tag %s" % int(time.time() * 1000000)
        result = self._call_tool(
            "create_records",
            {
                "model": "res.partner.category",
                "records": [{"name": f"{marker} ok"}, {"color": 1}],
            },
        )
        self.assertTrue(result["isError"], msg=result)
        text = result["content"][0]["text"]
        # The NOT NULL violation is translated into Odoo's field-naming
        # message, not the generic "Internal server error".
        self.assertIn("a mandatory field is not set", text)
        self.assertIn("(name)", text)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("psycopg2", text)
        self.assertNotIn("res_partner_category", text)
        self.env.invalidate_all()
        self.assertFalse(
            self.env["res.partner.category"].search([("name", "like", marker)]),
            "the valid entry must be rolled back with the failing one",
        )

    @mute_logger("odoo.sql_db", "odoo.addons.mcp_server.controllers.error_sanitizer")
    def test_unique_violation_is_translated_without_leaking_values(self):
        """A unique constraint failure names the constraint, never the value."""
        self._enable_model(
            "base.model_res_partner_bank", allow_read=True, allow_create=True
        )
        acc = "MCP-UNIQUE-%s" % int(time.time() * 1000000)
        result = self._call_tool(
            "create_records",
            {
                "model": "res.partner.bank",
                "records": [
                    {"acc_number": acc, "partner_id": self.partner.id},
                    {"acc_number": acc, "partner_id": self.partner.id},
                ],
            },
        )
        self.assertTrue(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertNotEqual(text, "Internal server error")
        self.assertIn("unique", text.lower())
        self.assertNotIn(acc, text, "the conflicting value must not leak")
        self.assertNotIn("Key (", text)
        self.assertNotIn("psycopg2", text)
        self.env.invalidate_all()
        self.assertFalse(
            self.env["res.partner.bank"].search([("acc_number", "=", acc)])
        )

    @mute_logger("odoo.sql_db", "odoo.addons.mcp_server.controllers.error_sanitizer")
    def test_unique_index_violation_is_named_without_leaking(self):
        """A unique *index* violation is a data error, not an internal one.

        ``mail.notification`` declares a plain ``UniqueIndex`` on
        ``(mail_message_id, res_partner_id)``. An index has no ``pg_constraint``
        row, so Odoo cannot resolve its columns and ``_as_validation_error``
        would fall back to the raw driver text (index name + ``DETAIL`` key). The
        sanitizer must map that to the fixed unique message, never the generic
        "Internal server error" and never the raw names / values.
        """
        self._enable_model(
            "mail.model_mail_notification", allow_read=True, allow_create=True
        )
        message = self.env["mail.message"].create(
            {
                "model": "res.partner",
                "res_id": self.partner.id,
                "body": "MCP unique index probe",
                "message_type": "comment",
            }
        )
        entry = {
            "mail_message_id": message.id,
            "res_partner_id": self.partner.id,
            "notification_type": "inbox",
        }
        result = self._call_tool(
            "create_records",
            {"model": "mail.notification", "records": [entry, dict(entry)]},
        )
        self.assertTrue(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertEqual(text, error_sanitizer.UNIQUE_ERROR_MESSAGE)
        self.env.invalidate_all()
        self.assertFalse(
            self.env["mail.notification"].search([("mail_message_id", "=", message.id)])
        )

    def test_create_records_shape_rules(self):
        """Empty list, non-list, non-dict entry and empty entry are clean errors."""
        cases = {
            "empty list": [],
            "non-list": "not a list",
            "non-dict entry": [{"name": "ok"}, "garbage"],
            "empty entry": [{"name": "ok"}, {}],
        }
        for label, records in cases.items():
            with self.subTest(label):
                result = self._call_tool(
                    "create_records", {"model": "res.partner", "records": records}
                )
                self.assertTrue(result["isError"], msg=(label, result))
                text = result["content"][0]["text"]
                self.assertNotIn("Traceback", text)
                if label in ("non-dict entry", "empty entry"):
                    self.assertIn("Entry 1", text)
                else:
                    self.assertIn("non-empty list", text)

    def test_create_records_enforces_batch_cap(self):
        """A batch at the configured cap passes; one over is refused."""
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.max_batch_size", "2"
        )
        ok = self._call_tool(
            "create_records",
            {
                "model": "res.partner",
                "records": [{"name": "MCP Cap 1"}, {"name": "MCP Cap 2"}],
            },
        )
        self.assertFalse(ok["isError"], msg=ok)
        self.assertEqual(ok["structuredContent"]["count"], 2)

        over = self._call_tool(
            "create_records",
            {
                "model": "res.partner",
                "records": [{"name": f"MCP Cap {i}"} for i in range(3)],
            },
        )
        self.assertTrue(over["isError"], msg=over)
        text = over["content"][0]["text"]
        self.assertIn("too many", text.lower())
        self.assertIn("max 2", text)
        self.assertIn("split", text.lower())

    def test_batch_cap_falls_back_to_default_on_bad_config(self):
        """Garbage, 0 and negative cap values all fall back to 100."""
        params = self.env["ir.config_parameter"].sudo()
        mixin = self.env["mcp.mixin"]
        for raw in ("garbage", "0", "-5", ""):
            with self.subTest(raw):
                params.set_param("mcp_server.max_batch_size", raw)
                self.assertEqual(mixin._max_batch_size(), 100)
        params.set_param("mcp_server.max_batch_size", "7")
        self.assertEqual(mixin._max_batch_size(), 7)

    def test_create_records_gated_like_single_record_tool(self):
        """Per-op gate and non-enabled model fail exactly like create_record."""
        blocked = self._call_tool(
            "create_records",
            {"model": "res.country", "records": [{"name": "MCP Batchland"}]},
        )
        self.assertTrue(blocked["isError"])
        text = blocked["content"][0]["text"]
        self.assertIn("not allowed", text.lower())
        self.assertIn("via MCP", text)

        not_enabled = self._call_tool(
            "create_records",
            {"model": "res.currency", "records": [{"name": "MCP"}]},
        )
        self.assertTrue(not_enabled["isError"])
        text = not_enabled["content"][0]["text"]
        self.assertIn("not enabled for MCP", text)
        self.assertNotIn("Traceback", text)

    def test_create_records_low_privilege_user_blocked_by_acl(self):
        """A low-priv user is stopped by Odoo's ACL and nothing is created."""
        marker = "MCP Batch SMTP %s" % int(time.time() * 1000000)
        result = self._call_tool(
            "create_records",
            {
                "model": "ir.mail_server",
                "records": [{"name": marker, "smtp_host": "localhost"}],
            },
            api_key=self.low_priv_key,
        )
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        self.assertTrue(text.strip())
        self.assertNotIn("Traceback", text)
        self.assertNotIn("via MCP", text)
        self.env.invalidate_all()
        self.assertFalse(
            self.env["ir.mail_server"].sudo().search([("name", "=", marker)])
        )

    # ------------------------------------------------------------------
    # update_records (batch)
    # ------------------------------------------------------------------
    def _make_partners(self, count, marker):
        return self.env["res.partner"].create(
            [{"name": f"{marker} {i}"} for i in range(count)]
        )

    def test_update_records_applies_values_to_all(self):
        """One values dict lands on every listed id; the result lists them all."""
        partners = self._make_partners(3, "MCP Bulk")
        result = self._call_tool(
            "update_records",
            {
                "model": "res.partner",
                "record_ids": partners.ids,
                "values": {"comment": "bulk-updated"},
            },
        )
        self.assertFalse(result["isError"], msg=result)
        structured = result["structuredContent"]
        self.assertEqual(structured["count"], 3)
        self.assertEqual([r["id"] for r in structured["records"]], partners.ids)
        self.assertIn("updated 3 res.partner records", structured["message"])
        for row in structured["records"]:
            self.assertTrue(
                row["url"].endswith(
                    f"/web#id={row['id']}&model=res.partner&view_type=form"
                ),
                row["url"],
            )
        self.env.invalidate_all()
        self.assertEqual(set(partners.mapped("comment")), {"<p>bulk-updated</p>"})

    def test_update_records_single_id_and_dedupe(self):
        """A single-id batch works; duplicate ids collapse to the unique count."""
        single = self._call_tool(
            "update_records",
            {
                "model": "res.partner",
                "record_ids": [self.partner.id],
                "values": {"ref": "solo"},
            },
        )
        self.assertFalse(single["isError"], msg=single)
        self.assertEqual(single["structuredContent"]["count"], 1)

        partners = self._make_partners(2, "MCP Dupe")
        dup = self._call_tool(
            "update_records",
            {
                "model": "res.partner",
                "record_ids": [partners[0].id, partners[1].id, partners[0].id],
                "values": {"ref": "deduped"},
            },
        )
        self.assertFalse(dup["isError"], msg=dup)
        self.assertEqual(dup["structuredContent"]["count"], 2)
        self.assertEqual(
            [r["id"] for r in dup["structuredContent"]["records"]], partners.ids
        )

    def _assert_id_coercion_matrix(self, tool, build_args):
        """Shared id-coercion matrix for update_records and call_model_method.

        Accepted forms resolve to a real id (success) or, for the float bound,
        pass coercion and then fail as *not found*; rejected forms are refused
        with the list-of-integers UserError before any lookup.
        """
        pid = self.partner.id
        accepted = {"digit string": str(pid), "integral float": float(pid)}
        for label, rid in accepted.items():
            with self.subTest(label):
                result = self._call_tool(tool, build_args([rid]))
                self.assertFalse(result["isError"], msg=(label, result))
        with self.subTest("float at MAX_SAFE_INTEGER passes coercion"):
            result = self._call_tool(tool, build_args([float(2**53 - 1)]))
            self.assertTrue(result["isError"], msg=result)
            self.assertIn("not found", result["content"][0]["text"].lower())

        rejected = {
            "non-integral float": 1.5,
            "bool": True,
            "nan": float("nan"),
            "inf": float("inf"),
            "float past MAX_SAFE_INTEGER": float(2**53),
            "non-numeric string": "abc",
            "none": None,
            "dict": {"id": pid},
            "list": [pid],
        }
        for label, rid in rejected.items():
            with self.subTest(label):
                result = self._call_tool(tool, build_args([rid]))
                self.assertTrue(result["isError"], msg=(label, result))
                text = result["content"][0]["text"]
                self.assertIn("must be a list of integers", text)
                self.assertNotIn("Traceback", text)

    def test_update_records_id_coercion_matrix(self):
        self._assert_id_coercion_matrix(
            "update_records",
            lambda ids: {
                "model": "res.partner",
                "record_ids": ids,
                "values": {"ref": "coerced"},
            },
        )

    def test_call_model_method_id_coercion_matrix(self):
        """The tightening applies to call_model_method too (shared helper)."""
        self._set_method_calls(True)
        self._assert_id_coercion_matrix(
            "call_model_method",
            lambda ids: {
                "model": "res.partner",
                "method": "message_post",
                "record_ids": ids,
                "kwargs": {"body": "coercion matrix"},
            },
        )

    def test_single_record_tools_share_id_coercion(self):
        """update_record / delete_record refuse a non-integral id.

        ``int(1.5)`` used to target record 1 -- a record the client never
        named. The single-record path now shares ``_coerce_record_id`` with
        the batch tools, so it is refused up front and nothing is touched.
        """
        for tool, extra in (
            ("update_record", {"values": {"ref": "never"}}),
            ("delete_record", {}),
        ):
            with self.subTest(tool):
                result = self._call_tool(
                    tool, {"model": "res.partner", "record_id": 1.5, **extra}
                )
                self.assertTrue(result["isError"], msg=result)
                self.assertIn("must be an integer", result["content"][0]["text"])
        self.env.invalidate_all()
        partner_one = self.env["res.partner"].browse(1)
        self.assertTrue(partner_one.exists())
        self.assertNotEqual(partner_one.ref, "never")

    def test_call_model_method_negative_max_limit_falls_back(self):
        """A negative max_limit does not refuse every record-targeted call."""
        self._set_method_calls(True)
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.max_limit", "-5")
        result = self._call_tool(
            "call_model_method",
            {
                "model": "res.partner",
                "method": "message_post",
                "record_ids": [self.partner.id],
                "kwargs": {"body": "negative cap"},
            },
        )
        self.assertFalse(result["isError"], msg=result)

    def test_call_model_method_keeps_duplicate_ids(self):
        """call_model_method does not dedupe (deduplicate=False preserved).

        ``[id, id]`` browses a two-record set, so the singleton-only
        ``message_post`` trips ``ensure_one`` -- proof the duplicate reached
        the ORM instead of being collapsed by the shared helper.
        """
        self._set_method_calls(True)
        result = self._call_tool(
            "call_model_method",
            {
                "model": "res.partner",
                "method": "message_post",
                "record_ids": [self.partner.id, self.partner.id],
                "kwargs": {"body": "dup call"},
            },
        )
        self.assertTrue(result["isError"], msg=result)
        self.assertIn("singleton", result["content"][0]["text"].lower())

    def test_update_records_missing_ids_lists_all_and_writes_nothing(self):
        """Every missing id is named in one MissingError; zero writes land."""
        partners = self._make_partners(2, "MCP Missing")
        result = self._call_tool(
            "update_records",
            {
                "model": "res.partner",
                "record_ids": partners.ids + [999999998, 999999999],
                "values": {"ref": "should-not-land"},
            },
        )
        self.assertTrue(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertIn("not found", text.lower())
        self.assertIn("999999998", text)
        self.assertIn("999999999", text)
        self.env.invalidate_all()
        self.assertFalse(any(partners.mapped("ref")))

    def test_update_records_rule_hidden_id_is_access_error(self):
        """A record hidden by a write rule surfaces as AccessError, not MissingError.

        ``exists()`` is record-rule-blind, so the id passes the existence pass
        and the ORM write refuses it -- the whole batch rolls back.
        """
        partners = self._make_partners(2, "MCP Rule")
        hidden = partners[1]
        self.env["ir.rule"].sudo().create(
            {
                "name": "MCP test: hide one partner from writes",
                "model_id": self.env.ref("base.model_res_partner").id,
                "domain_force": f"[('id', '!=', {hidden.id})]",
                "groups": [(6, 0, [self.env.ref("base.group_user").id])],
                "perm_read": False,
                "perm_write": True,
                "perm_create": False,
                "perm_unlink": False,
            }
        )
        self.env.flush_all()
        self.env.registry.clear_cache()
        result = self._call_tool(
            "update_records",
            {
                "model": "res.partner",
                "record_ids": partners.ids,
                "values": {"ref": "rule-blocked"},
            },
        )
        self.assertTrue(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertNotIn("not found", text.lower())
        self.assertNotIn("Traceback", text)
        self.env.invalidate_all()
        self.assertFalse(any(partners.mapped("ref")), "batch must roll back")

    def test_update_records_shape_rules(self):
        """Empty ids, non-list ids and empty values are clean UserErrors."""
        cases = {
            "empty ids": ([], {"ref": "x"}, "non-empty list"),
            "non-list ids": ("12", {"ref": "x"}, "non-empty list"),
            "empty values": ([self.partner.id], {}, "No values"),
            "non-dict values": ([self.partner.id], "x", "No values"),
        }
        for label, (ids, values, expected) in cases.items():
            with self.subTest(label):
                result = self._call_tool(
                    "update_records",
                    {"model": "res.partner", "record_ids": ids, "values": values},
                )
                self.assertTrue(result["isError"], msg=(label, result))
                text = result["content"][0]["text"]
                self.assertIn(expected, text)
                self.assertNotIn("Traceback", text)

    def test_update_records_cap_counts_duplicates(self):
        """The cap applies to the raw list, so duplicates cannot bypass it."""
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.max_batch_size", "2"
        )
        result = self._call_tool(
            "update_records",
            {
                "model": "res.partner",
                "record_ids": [self.partner.id] * 3,
                "values": {"ref": "over"},
            },
        )
        self.assertTrue(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertIn("too many", text.lower())
        self.assertIn("max 2", text)

    # -- form (b): per-record values -----------------------------------
    def test_update_records_each_applies_distinct_values(self):
        """'updates' writes each record its own values; result lists them in order."""
        partners = self._make_partners(3, "MCP Each")
        result = self._call_tool(
            "update_records",
            {
                "model": "res.partner",
                "updates": [
                    {"id": partners[0].id, "values": {"ref": "one"}},
                    {"id": str(partners[1].id), "values": {"ref": "two"}},
                    {"id": partners[2].id, "values": {"ref": "three", "comment": "c"}},
                ],
            },
        )
        self.assertFalse(result["isError"], msg=result)
        structured = result["structuredContent"]
        self.assertEqual(structured["count"], 3)
        self.assertEqual([r["id"] for r in structured["records"]], partners.ids)
        self.env.invalidate_all()
        self.assertEqual(partners.mapped("ref"), ["one", "two", "three"])
        self.assertEqual(partners[2].comment, "<p>c</p>")

    def test_update_tools_name_an_unknown_field(self):
        """An unknown field name gets core's message, not an internal error.

        ``res.partner.write`` reads every key before core checks it, so the
        name is checked first; nothing is written.
        """
        partner = self._make_partners(1, "MCP Unknown Field")
        expected = "Invalid field 'no_such_field' on model 'res.partner'"
        calls = (
            (
                "update_record",
                {
                    "model": "res.partner",
                    "record_id": partner.id,
                    "values": {"ref": "x", "no_such_field": 1},
                },
            ),
            (
                "update_records",
                {
                    "model": "res.partner",
                    "record_ids": partner.ids,
                    "values": {"ref": "x", "no_such_field": 1},
                },
            ),
            (
                "update_records",
                {
                    "model": "res.partner",
                    "updates": [
                        {"id": partner.id, "values": {"ref": "x", "no_such_field": 1}}
                    ],
                },
            ),
        )
        for tool, arguments in calls:
            with self.subTest(tool=tool, shape=sorted(arguments)):
                result = self._call_tool(tool, arguments)
                self.assertTrue(result["isError"], msg=result)
                self.assertEqual(result["content"][0]["text"], expected)
        self.env.invalidate_all()
        self.assertFalse(partner.ref)

    def test_update_records_each_is_atomic(self):
        """An invalid later entry rolls back the earlier, valid writes."""
        partners = self._make_partners(2, "MCP Each Atomic")
        result = self._call_tool(
            "update_records",
            {
                "model": "res.partner",
                "updates": [
                    {"id": partners[0].id, "values": {"ref": "landed?"}},
                    {"id": partners[1].id, "values": {"type": "bogus"}},
                ],
            },
        )
        self.assertTrue(result["isError"], msg=result)
        self.assertIn("Wrong value", result["content"][0]["text"])
        self.env.invalidate_all()
        self.assertFalse(partners[0].ref, "first write must roll back with the batch")

    def test_update_records_each_rejects_duplicate_ids(self):
        partners = self._make_partners(2, "MCP Each Dup")
        result = self._call_tool(
            "update_records",
            {
                "model": "res.partner",
                "updates": [
                    {"id": partners[0].id, "values": {"ref": "a"}},
                    {"id": partners[1].id, "values": {"ref": "b"}},
                    {"id": partners[0].id, "values": {"ref": "c"}},
                ],
            },
        )
        self.assertTrue(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertIn("Duplicate ids", text)
        self.assertIn(str(partners[0].id), text)
        self.env.invalidate_all()
        self.assertFalse(any(partners.mapped("ref")))

    def test_update_records_each_missing_id_lists_all_and_writes_nothing(self):
        partners = self._make_partners(1, "MCP Each Missing")
        result = self._call_tool(
            "update_records",
            {
                "model": "res.partner",
                "updates": [
                    {"id": partners.id, "values": {"ref": "never"}},
                    {"id": 999999998, "values": {"ref": "x"}},
                    {"id": 999999999, "values": {"ref": "y"}},
                ],
            },
        )
        self.assertTrue(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertIn("999999998", text)
        self.assertIn("999999999", text)
        self.env.invalidate_all()
        self.assertFalse(partners.ref)

    def test_update_records_each_shape_rules(self):
        pid = self.partner.id
        cases = {
            "empty list": ([], "non-empty list"),
            "not a list": ({"id": pid, "values": {"ref": "x"}}, "non-empty list"),
            "entry not dict": ([{"id": pid, "values": {"ref": "x"}}, 5], "Entry 1"),
            "entry missing values": ([{"id": pid}], "Entry 0"),
            "entry missing id": ([{"values": {"ref": "x"}}], "Entry 0"),
            "entry extra key": (
                [{"id": pid, "values": {"ref": "x"}, "z": 1}],
                "Entry 0",
            ),
            "entry empty values": ([{"id": pid, "values": {}}], "Entry 0"),
            "entry values not dict": ([{"id": pid, "values": "x"}], "Entry 0"),
            "entry bad id": ([{"id": 1.5, "values": {"ref": "x"}}], "list of integers"),
        }
        for label, (updates, expected) in cases.items():
            with self.subTest(label):
                result = self._call_tool(
                    "update_records", {"model": "res.partner", "updates": updates}
                )
                self.assertTrue(result["isError"], msg=(label, result))
                text = result["content"][0]["text"]
                self.assertIn(expected, text)
                self.assertNotIn("Traceback", text)

    def test_update_records_each_cap(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.max_batch_size", "2"
        )
        partners = self._make_partners(3, "MCP Each Cap")
        result = self._call_tool(
            "update_records",
            {
                "model": "res.partner",
                "updates": [{"id": p.id, "values": {"ref": "x"}} for p in partners],
            },
        )
        self.assertTrue(result["isError"], msg=result)
        self.assertIn("Too many record_ids: 3 (max 2)", result["content"][0]["text"])

    def test_update_records_forms_are_exclusive(self):
        """Both forms at once, or neither, is a clean error."""
        pid = self.partner.id
        both = self._call_tool(
            "update_records",
            {
                "model": "res.partner",
                "record_ids": [pid],
                "values": {"ref": "x"},
                "updates": [{"id": pid, "values": {"ref": "y"}}],
            },
        )
        self.assertTrue(both["isError"], msg=both)
        self.assertIn("not both", both["content"][0]["text"])
        neither = self._call_tool("update_records", {"model": "res.partner"})
        self.assertTrue(neither["isError"], msg=neither)
        self.assertIn(
            "Pass 'record_ids' + 'values', or 'updates'", neither["content"][0]["text"]
        )
        half = self._call_tool(
            "update_records", {"model": "res.partner", "record_ids": [pid]}
        )
        self.assertTrue(half["isError"], msg=half)
        self.assertIn("No values", half["content"][0]["text"])

    def test_update_records_each_audit_row(self):
        partners = self._make_partners(2, "MCP Each Audit")
        result = self._call_tool(
            "update_records",
            {
                "model": "res.partner",
                "updates": [
                    {"id": partners[0].id, "values": {"ref": "a"}},
                    {"id": partners[1].id, "values": {"ref": "b"}},
                ],
            },
        )
        self.assertFalse(result["isError"], msg=result)
        row = self._latest_success_row("write")
        self.assertEqual(row.tool_name, "update_records")
        self.assertEqual(row.record_ids, ",".join(map(str, partners.ids)))
        self.assertEqual(row.response_data, "2 records")
        self.assertEqual(row.request_data, "model, updates, batch_size=2")

    def test_batch_list_arguments_accept_json_strings(self):
        """A stringified array (JSON or Python literal) is parsed like a list."""
        partners = self._make_partners(2, "MCP Stringified")
        created = self._call_tool(
            "create_records",
            {"model": "res.partner", "records": '[{"name": "MCP Str A"}]'},
        )
        self.assertFalse(created["isError"], msg=created)
        self.assertEqual(created["structuredContent"]["count"], 1)

        shared = self._call_tool(
            "update_records",
            {
                "model": "res.partner",
                "record_ids": json.dumps(partners.ids),
                "values": {"ref": "str-shared"},
            },
        )
        self.assertFalse(shared["isError"], msg=shared)
        self.assertEqual(shared["structuredContent"]["count"], 2)

        each = self._call_tool(
            "update_records",
            {
                "model": "res.partner",
                # Python-literal form (single quotes), as some clients emit
                "updates": "[{'id': %s, 'values': {'ref': 'str-each'}}]"
                % partners[0].id,
            },
        )
        self.assertFalse(each["isError"], msg=each)
        self.env.invalidate_all()
        self.assertEqual(partners[0].ref, "str-each")

        # A string that is not a list still hits the tool's own shape rule.
        for label, args, needle in (
            ("garbage", {"records": "not a list"}, "non-empty list"),
            ("scalar json", {"records": "42"}, "non-empty list"),
            ("object json", {"records": '{"name": "x"}'}, "non-empty list"),
        ):
            with self.subTest(label):
                result = self._call_tool(
                    "create_records", {"model": "res.partner", **args}
                )
                self.assertTrue(result["isError"], msg=(label, result))
                self.assertIn(needle, result["content"][0]["text"])

    def test_update_records_write_disabled_model_is_blocked(self):
        """The per-op write gate refuses the batch like update_record."""
        result = self._call_tool(
            "update_records",
            {
                "model": "res.country",
                "record_ids": [self.env.ref("base.de").id],
                "values": {"name": "MCP"},
            },
        )
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        self.assertIn("not allowed", text.lower())
        self.assertIn("via MCP", text)

    # ------------------------------------------------------------------
    # Audit logging (mcp.log)
    # ------------------------------------------------------------------
    def test_audit_rows_written_for_success_and_failure(self):
        """A successful and a failing tools/call each persist an mcp.log row."""
        ok = self._call_tool(
            "create_record",
            {"model": "res.partner", "values": {"name": "Audit OK"}},
        )
        self.assertFalse(ok["isError"], msg=ok)

        bad = self._call_tool(
            "create_record",
            {"model": "res.country", "values": {"name": "Audit Bad"}},
        )
        self.assertTrue(bad["isError"])

        self.env.invalidate_all()
        log_model = self.env["mcp.log"].sudo()

        success_rows = log_model.search(
            [
                ("event_type", "=", "model_access"),
                ("model_name", "=", "res.partner"),
                ("operation", "=", "create"),
                ("user_id", "=", self.mcp_user.id),
            ]
        )
        self.assertTrue(
            success_rows, "expected a model_access audit row for the successful create"
        )

        # The blocked create is an authorization denial (the per-model gate
        # raises AccessError), so it is audited as permission_denied WITH auth
        # attribution -- not a generic E500 error.
        denied_rows = log_model.search(
            [
                ("event_type", "=", "permission_denied"),
                ("model_name", "=", "res.country"),
                ("operation", "=", "create"),
                ("user_id", "=", self.mcp_user.id),
            ]
        )
        self.assertTrue(
            denied_rows,
            "expected a permission_denied audit row for the blocked create",
        )
        self.assertEqual(denied_rows[0].auth_method, "api_key")
        e500_rows = log_model.search(
            [
                ("event_type", "=", "error"),
                ("model_name", "=", "res.country"),
                ("operation", "=", "create"),
                ("user_id", "=", self.mcp_user.id),
            ]
        )
        self.assertFalse(
            e500_rows, "the blocked create must not also be logged as an E500 error"
        )

    def test_audit_records_ids_and_real_method_name(self):
        """A write logs its record_ids; call_model_method logs the real method."""
        # A write (update_record) must record the touched record id.
        upd = self._call_tool(
            "update_record",
            {
                "model": "res.partner",
                "record_id": self.partner.id,
                "values": {"comment": "audited"},
            },
        )
        self.assertFalse(upd["isError"], msg=upd)

        # call_model_method must log the ACTUAL business method (message_post),
        # not the literal tool name, mirroring the legacy XML-RPC proxy.
        self._set_method_calls(True)
        called = self._call_tool(
            "call_model_method",
            {
                "model": "res.partner",
                "method": "message_post",
                "record_ids": [self.partner.id],
                "kwargs": {"body": "audited method call"},
            },
        )
        self.assertFalse(called["isError"], msg=called)

        self.env.invalidate_all()
        log_model = self.env["mcp.log"].sudo()

        write_row = log_model.search(
            [
                ("event_type", "=", "model_access"),
                ("model_name", "=", "res.partner"),
                ("operation", "=", "write"),
                ("user_id", "=", self.mcp_user.id),
            ],
            order="id desc",
            limit=1,
        )
        self.assertTrue(write_row, "expected a model_access row for the update")
        self.assertEqual(write_row.record_ids, str(self.partner.id))
        # The row is self-describing: which tool, how it authenticated, the MCP
        # client, the argument keys and a result-size summary (metadata only --
        # no raw field values).
        self.assertEqual(write_row.tool_name, "update_record")
        self.assertEqual(write_row.auth_method, "api_key")
        self.assertTrue(write_row.user_agent)
        self.assertIn("values", write_row.request_data)
        self.assertEqual(write_row.response_data, "1 record(s)")

        method_row = log_model.search(
            [
                ("event_type", "=", "model_access"),
                ("model_name", "=", "res.partner"),
                ("operation", "=", "message_post"),
                ("user_id", "=", self.mcp_user.id),
            ],
            order="id desc",
            limit=1,
        )
        self.assertTrue(
            method_row,
            "call_model_method must log the real method name as the operation",
        )
        self.assertEqual(method_row.record_ids, str(self.partner.id))

    def _latest_success_row(self, operation):
        self.env.invalidate_all()
        return (
            self.env["mcp.log"]
            .sudo()
            .search(
                [
                    ("event_type", "=", "model_access"),
                    ("model_name", "=", "res.partner"),
                    ("operation", "=", operation),
                    ("user_id", "=", self.mcp_user.id),
                ],
                order="id desc",
                limit=1,
            )
        )

    def test_audit_batch_success_rows_carry_result_ids(self):
        """Each batch call logs one row: ids from the result, count + batch size."""
        log_model = self.env["mcp.log"].sudo()
        before = log_model.search_count([("user_id", "=", self.mcp_user.id)])

        created = self._call_tool(
            "create_records",
            {
                "model": "res.partner",
                "records": [{"name": "Audit Batch A"}, {"name": "Audit Batch B"}],
            },
        )
        self.assertFalse(created["isError"], msg=created)
        created_ids = [r["id"] for r in created["structuredContent"]["records"]]

        partners = self._make_partners(2, "MCP Audit Upd")
        updated = self._call_tool(
            "update_records",
            {
                "model": "res.partner",
                # duplicate on purpose: the row must carry the DEDUPED ids
                "record_ids": [partners[0].id, partners[1].id, partners[0].id],
                "values": {"ref": "audited-batch"},
            },
        )
        self.assertFalse(updated["isError"], msg=updated)

        self.env.invalidate_all()
        self.assertEqual(
            log_model.search_count([("user_id", "=", self.mcp_user.id)]), before + 2
        )

        create_row = self._latest_success_row("create")
        self.assertEqual(create_row.tool_name, "create_records")
        self.assertEqual(create_row.record_ids, ",".join(map(str, created_ids)))
        self.assertEqual(create_row.response_data, "2 records")
        self.assertEqual(create_row.request_data, "model, records, batch_size=2")

        write_row = self._latest_success_row("write")
        self.assertEqual(write_row.tool_name, "update_records")
        self.assertEqual(write_row.record_ids, ",".join(map(str, partners.ids)))
        self.assertEqual(write_row.response_data, "2 records")
        self.assertEqual(
            write_row.request_data, "model, record_ids, values, batch_size=3"
        )

    @mute_logger("odoo.sql_db", "odoo.addons.mcp_server.controllers.error_sanitizer")
    def test_audit_batch_error_leaves_error_row_only(self):
        """A failed batch logs the existing id-less error row, no success row."""
        log_model = self.env["mcp.log"].sudo()
        success_before = log_model.search_count(
            [("event_type", "=", "model_access"), ("user_id", "=", self.mcp_user.id)]
        )
        result = self._call_tool(
            "create_records",
            {
                "model": "res.partner.category",
                "records": [{"name": "Audit Atomic"}, {"color": 2}],
            },
        )
        self.assertTrue(result["isError"], msg=result)

        self.env.invalidate_all()
        self.assertEqual(
            log_model.search_count(
                [
                    ("event_type", "=", "model_access"),
                    ("user_id", "=", self.mcp_user.id),
                ]
            ),
            success_before,
        )
        error_row = log_model.search(
            [
                ("event_type", "=", "error"),
                ("model_name", "=", "res.partner.category"),
                ("operation", "=", "create"),
                ("user_id", "=", self.mcp_user.id),
            ],
            order="id desc",
            limit=1,
        )
        self.assertTrue(error_row, "expected an error row for the failed batch")
        self.assertFalse(error_row.record_ids)

    def test_audit_batch_malformed_result_degrades_to_no_ids(self):
        """A batch tool returning malformed ``records`` still succeeds, id-less."""

        def fake_create_records(mixin_self, model, records):
            return {
                "content": [{"type": "text", "text": "ok"}],
                "structuredContent": {
                    "success": True,
                    "count": 1,
                    "records": "not-a-list",
                },
            }

        mixin_cls = type(self.env["mcp.mixin"])
        with patch.object(mixin_cls, "create_records", fake_create_records):
            result = self._call_tool(
                "create_records",
                {"model": "res.partner", "records": [{"name": "Malformed"}]},
            )
        self.assertFalse(result["isError"], msg=result)
        row = self._latest_success_row("create")
        self.assertEqual(row.tool_name, "create_records")
        self.assertFalse(row.record_ids)
        self.assertEqual(row.response_data, "1 records")

    # ------------------------------------------------------------------
    # Rate limiting primitives (endpoint enforcement covered in test_mcp_rate_limit)
    # ------------------------------------------------------------------
    def test_rate_limit_primitives_trip_at_threshold(self):
        """record_api_request + check_rate_limit deny once the limit is reached."""
        rate_limiting._api_limiter.clear()
        uid = self.mcp_user.id
        dbname = self.env.cr.dbname
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.request_limit", "11"
        )

        mock_request = MagicMock()
        mock_request.env = self.env
        with patch(
            "odoo.addons.mcp_server.controllers.rate_limiting.request", mock_request
        ):
            for _ in range(10):
                rate_limiting.record_api_request(uid, dbname)
            # 10 recorded requests stay under the limit of 11.
            self.assertTrue(rate_limiting.check_rate_limit(uid, dbname))

            # The 11th request hits the threshold -> subsequent checks deny.
            rate_limiting.record_api_request(uid, dbname)
            self.assertFalse(rate_limiting.check_rate_limit(uid, dbname))

    # ------------------------------------------------------------------
    # post_message (-> message_post, gated as a write op)
    # ------------------------------------------------------------------
    def test_post_message_success_creates_mail_message(self):
        """post_message on a chatter model creates a mail.message."""
        result = self._call_tool(
            "post_message",
            {
                "model": "res.partner",
                "record_id": self.partner.id,
                "body": "Hello from MCP",
            },
        )
        self.assertFalse(result["isError"], msg=result)
        message_id = result["structuredContent"]["message_id"]
        self.assertTrue(message_id)

        self.env.invalidate_all()
        message = self.env["mail.message"].sudo().browse(message_id).exists()
        self.assertTrue(message, "post_message must create a mail.message row")
        self.assertEqual(message.model, "res.partner")
        self.assertEqual(message.res_id, self.partner.id)
        self.assertIn("Hello from MCP", message.body or "")

    def test_post_message_non_chatter_model_is_clean_error(self):
        """post_message on a model without chatter errors cleanly (no traceback)."""
        # res.country has no mail.thread; enable it for write so the per-op gate
        # passes and we reach the missing-chatter guard.
        self._enable_model("base.model_res_country", allow_read=True, allow_write=True)
        utils.clear_mcp_caches()
        result = self._call_tool(
            "post_message",
            {"model": "res.country", "record_id": 1, "body": "nope"},
        )
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        self.assertIn("chatter", text.lower())
        self.assertNotIn("Traceback", text)

    def test_post_message_invalid_subtype_is_clean_error(self):
        """post_message with an unknown subtype errors cleanly."""
        result = self._call_tool(
            "post_message",
            {
                "model": "res.partner",
                "record_id": self.partner.id,
                "body": "Hi",
                "subtype": "bogus",
            },
        )
        self.assertTrue(result["isError"])
        self.assertIn("subtype", result["content"][0]["text"].lower())

    # ------------------------------------------------------------------
    # Savepoint rollback: mutate-then-raise leaves NO partial row
    # ------------------------------------------------------------------
    @mute_logger(
        "odoo.addons.mcp_server.controllers.error_sanitizer",
        "odoo.addons.mcp_server.controllers.mcp",
        "odoo.sql_db",
    )
    def test_create_record_savepoint_rolls_back_partial_writes(self):
        """A create that inserts a parent then fails on a child must persist nothing.

        The nested child carries a bad ``country_id`` foreign key: Odoo creates
        the parent row first, then the child INSERT trips the FK constraint. The
        dispatcher's savepoint must roll back the already-inserted parent, so no
        orphan record survives (and the request returns a sanitized failure, not
        a 500).
        """
        marker = "MCP Savepoint Orphan %s" % int(time.time() * 1000000)
        response = self._post_rpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_record",
                    "arguments": {
                        "model": "res.partner",
                        "values": {
                            "name": marker,
                            "child_ids": [
                                [0, 0, {"name": "child", "country_id": 999999999}]
                            ],
                        },
                    },
                },
            }
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        # The call must fail -- either as a JSON-RPC invalid/internal error or a
        # sanitized isError tool result -- but never report success.
        if "error" in payload:
            self.assertIn(payload["error"]["code"], (-32602, -32603))
        else:
            self.assertTrue(payload["result"]["isError"], msg=payload)
            text = payload["result"]["content"][0]["text"]
            # The FK violation is translated into insert-oriented wording that
            # names the field -- not core's delete-case text, and never the
            # raw driver text.
            self.assertIn("refers to a record that does not exist", text)
            self.assertIn("(country_id)", text)
            self.assertNotIn("trying to delete", text)
            self.assertNotIn("psycopg2", text)
            self.assertNotIn("res_partner_country_id_fkey", text)
            self.assertNotIn("violates", text)

        # The partial parent insert must have been rolled back by the savepoint.
        self.env.invalidate_all()
        orphan = self.env["res.partner"].search([("name", "=", marker)])
        self.assertFalse(orphan, "savepoint must roll back the partial parent insert")

    @mute_logger(
        "odoo.addons.mcp_server.controllers.error_sanitizer",
        "odoo.addons.mcp_server.controllers.mcp",
    )
    def test_savepoint_rolls_back_valid_write_on_non_db_error(self):
        """The controller savepoint rolls back a VALID write when the tool raises.

        Unlike the FK test above -- whose IntegrityError poisons the entire
        request transaction, so it would roll back even with the savepoint
        removed -- here the tool performs a real, VALID ``write`` on an existing
        record and then raises a NON-DB ``UserError``, which does NOT poison the
        cursor. That write happens inside the ``cr.savepoint()`` in
        ``_tools_call``; the savepoint rolls it back before the request returns
        the sanitized ``isError`` result. Remove that savepoint and the service
        layer would flush+commit the valid write, persisting it -- so this test
        fails if the savepoint is removed.

        Patching targets the registry class (``type(env['mcp.mixin'])``) because
        recordset methods are read-only on instances; the ``/mcp`` handler
        runs in-process under HttpCase and shares that class, so the patch binds.
        """
        marker = "MCP Savepoint Valid Write %s" % int(time.time() * 1000000)
        partner_id = self.partner.id

        def fake_update_record(mixin_self, model, record_id, values):
            # A real, valid write inside the controller's savepoint...
            mixin_self.env["res.partner"].browse(int(record_id)).write({"ref": marker})
            # ...then a non-DB error that leaves the cursor usable.
            raise UserError(  # pylint: disable=translation-required
                "Intentional post-write failure for savepoint test"
            )

        mixin_cls = type(self.env["mcp.mixin"])
        with patch.object(mixin_cls, "update_record", fake_update_record):
            result = self._call_tool(
                "update_record",
                {
                    "model": "res.partner",
                    "record_id": partner_id,
                    "values": {"name": "ignored"},
                },
            )

        # The tool raised -> sanitized isError result (HTTP 200), not a 500.
        self.assertTrue(result["isError"], msg=result)
        self.assertNotIn("Traceback", result["content"][0]["text"])

        # The valid write must have been rolled back by the controller savepoint.
        self.env.invalidate_all()
        self.assertNotEqual(
            self.env["res.partner"].browse(partner_id).ref,
            marker,
            "controller savepoint must roll back the valid write on tool error",
        )

    @mute_logger(
        "odoo.addons.mcp_server.controllers.error_sanitizer",
        "odoo.addons.mcp_server.controllers.mcp",
    )
    def test_malformed_tool_return_rolls_back_and_is_error(self):
        """A tool returning a non-dict after a valid write -> isError + rollback.

        Covers the ``_ToolResultError`` branch in ``_tools_call``: normalizing
        the tool output (``dict(tool_output)``) fails, so the malformed return is
        re-raised INSIDE the savepoint. Two things must hold: the partial write
        is rolled back (not flushed+committed), and the failure is classified as
        an ``isError`` tool result -- NOT a ``-32602`` invalid-params client
        error (``_call_tool`` asserts the response carries no ``error`` envelope,
        so simply reaching its ``result`` proves the distinction).
        """
        marker = "MCP Malformed Return %s" % int(time.time() * 1000000)
        partner_id = self.partner.id

        def fake_update_record(mixin_self, model, record_id, values):
            # A real, valid write inside the controller's savepoint...
            mixin_self.env["res.partner"].browse(int(record_id)).write({"ref": marker})
            # ...then a malformed (non-dict) return the controller can't normalize.
            return "not-a-dict"

        mixin_cls = type(self.env["mcp.mixin"])
        with patch.object(mixin_cls, "update_record", fake_update_record):
            result = self._call_tool(
                "update_record",
                {
                    "model": "res.partner",
                    "record_id": partner_id,
                    "values": {"name": "ignored"},
                },
            )

        # Classified as a tool error (isError result), not a -32602 protocol error.
        self.assertTrue(result["isError"], msg=result)
        self.assertNotIn("Traceback", result["content"][0]["text"])

        # The valid write must have been rolled back by the controller savepoint.
        self.env.invalidate_all()
        self.assertNotEqual(
            self.env["res.partner"].browse(partner_id).ref,
            marker,
            "controller savepoint must roll back the write on a malformed return",
        )

    # ------------------------------------------------------------------
    # update_record / delete_record -- gating denial + missing record
    # ------------------------------------------------------------------
    def test_update_on_write_disabled_model_is_blocked(self):
        """update_record on a read-only-via-MCP model is refused by the gate."""
        country = self.env.ref("base.us")
        result = self._call_tool(
            "update_record",
            {
                "model": "res.country",
                "record_id": country.id,
                "values": {"name": "MCP Renamed"},
            },
        )
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        self.assertIn("via MCP", text)
        self.assertNotIn("Traceback", text)
        # The gate fired before any ORM write -> the name is untouched.
        self.env.invalidate_all()
        self.assertNotEqual(country.name, "MCP Renamed")

    def test_delete_on_unlink_disabled_model_is_blocked(self):
        """delete_record on a model without MCP unlink is refused by the gate."""
        country = self.env.ref("base.us")
        result = self._call_tool(
            "delete_record",
            {"model": "res.country", "record_id": country.id},
        )
        self.assertTrue(result["isError"])
        self.assertIn("via MCP", result["content"][0]["text"])
        self.env.invalidate_all()
        self.assertTrue(country.exists(), "the gate must block the unlink")

    def test_update_missing_record_returns_missing_error(self):
        """update_record on a non-existent id yields a clean MissingError."""
        result = self._call_tool(
            "update_record",
            {
                "model": "res.partner",
                "record_id": 999999999,
                "values": {"name": "x"},
            },
        )
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        self.assertIn("not found", text.lower())
        self.assertNotIn("Traceback", text)

    def test_delete_missing_record_returns_missing_error(self):
        """delete_record on a non-existent id yields a clean MissingError."""
        result = self._call_tool(
            "delete_record",
            {"model": "res.partner", "record_id": 999999999},
        )
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        self.assertIn("not found", text.lower())
        self.assertNotIn("Traceback", text)

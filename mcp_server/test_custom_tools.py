"""Tests for MCP custom tools: ``mcp.custom.tool`` + server-action runner.

Covers the admin-defined custom-tool feature wired onto ``mcp.custom.tool`` and
served through ``POST /mcp``:

* MODEL (``TransactionCase``): the ``name`` regex + builtin-collision + JSON-object
  ``input_schema`` ``@api.constrains``, the ``UNIQUE(name)`` SQL constraint, and
  the field defaults.
* EXECUTION / ACCESS / ROLLBACK / SCOPE / AUDIT (``HttpCase``): a code action
  ``mcp['result'] = {'echo': mcp['args'].get('x')}`` wrapped in a tool round-trips
  through ``tools/list`` + ``tools/call`` (correct ``readOnlyHint`` /
  ``destructiveHint``); the module's NON-su ``_user_can_run`` gate hides and
  refuses the tool for a user outside the action's Allowed Groups (and, with empty
  groups, for a user without model write access); an action that creates a record
  then raises rolls the partial write back and returns a sanitized ``isError``; an
  ``mcp:read`` OAuth session may only call ``is_readonly`` tools; and every call
  writes an ``mcp.log`` audit row.

Mirrors the HttpCase + ``_generate("rpc", ...)`` key-minting + ``url_open``
harness of ``test_mcp_write_tools.py`` and the direct-token-minting approach of
``test_oauth_scopes.py`` (reusing ``test_oauth.py``'s ``_sha256_hex`` helper).
"""

import json
import re
import secrets
import time
from datetime import timedelta

from lxml import etree
from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import common, tagged
from odoo.tools import file_open, mute_logger

from ..controllers import rate_limiting, utils
from .test_helpers import (
    UrlOpenCompatMixin,
    create_test_user,
    grant_mcp_access,
    users_groups_field,
)
from .test_oauth import _sha256_hex

# A code action that echoes its ``x`` argument back through the shared dict.
_ECHO_CODE = "mcp['result'] = {'echo': mcp['args'].get('x')}"
_ECHO_SCHEMA = '{"type": "object", "properties": {"x": {"type": "string"}}}'
# A schema declaring a REQUIRED ``target_id`` argument (distinctive name so a
# leak of the required-arg field name is unambiguous to assert against).
_REQUIRED_SCHEMA = (
    '{"type": "object", "properties": {"target_id": {"type": "integer"}}, '
    '"required": ["target_id"]}'
)


@tagged("much_unit", "post_install", "-at_install")
class TestCustomToolViews(common.TransactionCase):
    def test_code_editor_mode_is_known_to_core(self):
        """The Input Schema editor uses a mode that core's CodeEditor accepts.

        CodeEditor validates its ``mode`` prop against ``CodeEditor.MODES``; an
        unknown mode raises an OwlError in debug mode and loses the highlighting.
        """
        with file_open("web/static/src/core/code_editor/code_editor.js") as source:
            modes = re.search(r"static MODES = \[([^\]]*)\]", source.read()).group(1)
        allowed = set(re.findall(r'"(\w+)"', modes))
        view = self.env.ref("mcp_server.mcp_custom_tool_view_form")
        arch = etree.fromstring(view.arch)
        field = arch.xpath("//field[@name='input_schema']")[0]
        mode = re.search(r"'mode':\s*'(\w+)'", field.get("options")).group(1)
        self.assertIn(mode, allowed)


@tagged("much_unit", "post_install", "-at_install")
class TestCustomToolsModel(common.TransactionCase):
    """``mcp.custom.tool`` constraints + field defaults (no HTTP needed)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.action = cls.env["ir.actions.server"].create(
            {
                "name": "Echo Action (model tests)",
                "model_id": cls.env.ref("base.model_res_partner").id,
                "state": "code",
                "code": _ECHO_CODE,
            }
        )

    def _make_tool(self, **vals):
        """Create a custom tool bound to the shared echo action."""
        base = {
            "name": "echo_tool",
            "description": "Echo the x argument back.",
            "action_id": self.action.id,
        }
        base.update(vals)
        return self.env["mcp.custom.tool"].create(base)

    def test_defaults_are_sane(self):
        """A minimally-specified tool gets the documented defaults."""
        tool = self._make_tool(name="echo_defaults")
        self.assertEqual(
            json.loads(tool.input_schema),
            {"type": "object", "properties": {}},
        )
        self.assertFalse(tool.is_readonly)
        self.assertTrue(tool.active)

    def test_name_bad_chars_raises(self):
        """A name with characters outside the MCP-safe set is rejected."""
        with self.assertRaises(ValidationError):
            self._make_tool(name="bad name!")

    def test_name_too_long_raises(self):
        """A name longer than 64 characters is rejected by the regex."""
        with self.assertRaises(ValidationError):
            self._make_tool(name="a" * 65)

    def test_name_collides_with_builtin_raises(self):
        """A name equal to a builtin tool name is rejected at save."""
        with self.assertRaises(ValidationError):
            self._make_tool(name="search_records")

    def test_name_trailing_newline_raises(self):
        """A name with a trailing newline is rejected (regex uses fullmatch).

        ``re.match(r"...$", "echo\\n")`` matches -- ``$`` also anchors just before
        a trailing newline -- so the earlier ``match``-based check accepted a name
        with a trailing newline. ``fullmatch`` requires the whole string to match,
        so the trailing newline is now rejected.
        """
        with self.assertRaises(ValidationError):
            self._make_tool(name="echo\n")

    def test_duplicate_name_raises(self):
        """The UNIQUE(name) constraint rejects a second tool with the same name."""
        self._make_tool(name="echo_unique")
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            with self.env.cr.savepoint():
                self._make_tool(name="echo_unique")
                self.env.flush_all()

    def test_invalid_json_schema_raises(self):
        """An input_schema that is not valid JSON is rejected."""
        with self.assertRaises(ValidationError):
            self._make_tool(name="echo_badjson", input_schema="{not json")

    def test_json_array_schema_raises(self):
        """An input_schema that is a JSON array (not an object) is rejected."""
        with self.assertRaises(ValidationError):
            self._make_tool(name="echo_array", input_schema="[]")

    def test_schema_type_not_object_raises(self):
        """An input_schema whose 'type' is not 'object' is rejected."""
        with self.assertRaises(ValidationError):
            self._make_tool(
                name="echo_stringtype", input_schema='{"type": "string"}'
            )

    def test_required_not_a_list_raises(self):
        """An input_schema whose 'required' is not a list is rejected.

        The tools/call required-arg check iterates 'required'; a bare non-list
        value (here 42) would crash that consumer, so it is rejected at save.
        """
        with self.assertRaises(ValidationError):
            self._make_tool(
                name="echo_req_notlist",
                input_schema='{"type": "object", "required": 42}',
            )

    def test_required_non_string_element_raises(self):
        """An input_schema whose 'required' has a non-string element is rejected.

        The consumer joins the required names with ', '; a non-string element
        (here the int 1) would raise there, so it is rejected at save.
        """
        with self.assertRaises(ValidationError):
            self._make_tool(
                name="echo_req_badelem",
                input_schema='{"type": "object", "required": ["x", 1]}',
            )

    def test_write_name_to_builtin_raises(self):
        """Renaming an existing tool to a builtin name is rejected on write.

        Pins that the name @api.constrains fires on write(), not just create().
        """
        tool = self._make_tool(name="echo_rename_builtin")
        with self.assertRaises(ValidationError):
            tool.write({"name": "search_records"})

    def test_write_name_invalid_regex_raises(self):
        """Renaming an existing tool to an invalid name is rejected on write."""
        tool = self._make_tool(name="echo_rename_bad")
        with self.assertRaises(ValidationError):
            tool.write({"name": "bad name!"})

    def test_non_code_action_raises(self):
        """A tool wrapping a non-Python-Code server action is rejected.

        Only ``state='code'`` actions read ``mcp['args']`` and assign
        ``mcp['result']``; a per-record action (e.g. Update Record) never runs
        for a record-less tool call, so it is refused at save.
        """
        non_code_action = self.env["ir.actions.server"].create(
            {
                "name": "Update Partner (non-code)",
                "model_id": self.env.ref("base.model_res_partner").id,
                "state": "object_write",
            }
        )
        with self.assertRaises(ValidationError):
            self._make_tool(name="update_tool", action_id=non_code_action.id)

    def test_switching_action_to_non_code_raises_on_write(self):
        """Re-pointing a tool's action to a non-code action is rejected on write.

        Pins that the action @api.constrains fires on write(), not just create().
        """
        tool = self._make_tool(name="echo_switch")
        non_code_action = self.env["ir.actions.server"].create(
            {
                "name": "Create Partner (non-code)",
                "model_id": self.env.ref("base.model_res_partner").id,
                "state": "object_create",
            }
        )
        with self.assertRaises(ValidationError):
            tool.write({"action_id": non_code_action.id})


@tagged("much_unit", "post_install", "-at_install")
class TestCustomTools(UrlOpenCompatMixin, common.HttpCase):
    """Custom-tool execution, access gate, rollback, scope + audit on /mcp."""

    def setUp(self):
        super().setUp()
        utils.clear_mcp_caches()
        rate_limiting._api_limiter.clear()

        unique_id = str(int(time.time() * 1000))[-6:]
        groups_field = users_groups_field(self.env)
        internal = self.env.ref("base.group_user")
        partner_mgr = self.env.ref("base.group_partner_manager")

        # The action's Allowed Group -- membership is the access gate.
        self.tool_group = self.env["res.groups"].create(
            {"name": f"MCP Tool Group {unique_id}"}
        )

        # user_in: in the tool group AND has res.partner write (partner manager).
        self.user_in = create_test_user(
            self.env,
            "MCP Custom In",
            f"mcp_custom_in_{unique_id}",
            email=f"mcp_custom_in_{unique_id}@example.com",
            **{
                groups_field: [
                    (6, 0, [internal.id, partner_mgr.id, self.tool_group.id])
                ]
            },
        )
        grant_mcp_access(self.user_in)
        self.key_in = self._mint_key(self.user_in, "Custom In Key")

        # user_out: bare internal -> not in the tool group, no res.partner write.
        self.user_out = create_test_user(
            self.env,
            "MCP Custom Out",
            f"mcp_custom_out_{unique_id}",
            email=f"mcp_custom_out_{unique_id}@example.com",
            **{groups_field: [(6, 0, [internal.id])]},
        )
        grant_mcp_access(self.user_out)
        self.key_out = self._mint_key(self.user_out, "Custom Out Key")

        # A grouped echo tool (write) -- only tool-group members may run it.
        self.echo_tool = self._make_tool(
            "echo_grouped",
            code=_ECHO_CODE,
            group_ids=[(6, 0, [self.tool_group.id])],
            is_readonly=False,
        )
        # A read-only echo tool, same group -- callable under an mcp:read token.
        self.echo_readonly = self._make_tool(
            "echo_readonly",
            code=_ECHO_CODE,
            group_ids=[(6, 0, [self.tool_group.id])],
            is_readonly=True,
        )
        # An ungrouped echo tool -- access falls back to res.partner write.
        self.echo_nogroup = self._make_tool(
            "echo_nogroup",
            code=_ECHO_CODE,
            group_ids=[(6, 0, [])],
            is_readonly=False,
        )

        params = self.env["ir.config_parameter"].sudo()
        params.set_param("mcp_server.enabled", "True")
        params.set_param("mcp_server.enable_logging", "True")
        params.set_param("mcp_server.enable_oauth", "True")
        utils.clear_mcp_caches()

        # OAuth audience the AS derives from the request host (RFC 8707); a token's
        # audience must equal this to be accepted at /mcp.
        self.resource = self.base_url() + "/mcp"
        self.oauth_client = (
            self.env["mcp.oauth.client"]
            .sudo()
            .create(
                {
                    "client_id": f"custom-tool-client-{unique_id}",
                    "redirect_uris": "http://127.0.0.1:8765/callback",
                }
            )
        )

    # ------------------------------------------------------------------
    # Fixture helpers
    # ------------------------------------------------------------------
    def _mint_key(self, user, name):
        """Mint an ``rpc``-scope API key for ``user``."""
        return self.env(user=user)["res.users.apikeys"]._generate("rpc", name)

    def _make_tool(
        self,
        name,
        *,
        code,
        group_ids,
        is_readonly,
        model_xmlid="base.model_res_partner",
        input_schema=_ECHO_SCHEMA,
    ):
        """Create a server action + a custom tool wrapping it."""
        action = self.env["ir.actions.server"].create(
            {
                "name": f"Action {name}",
                "model_id": self.env.ref(model_xmlid).id,
                "state": "code",
                "code": code,
                # ir.actions.server's groups field is ``groups_id`` on v18;
                # the helper kwarg keeps the v19 public name.
                "groups_id": group_ids,
            }
        )
        return self.env["mcp.custom.tool"].create(
            {
                "name": name,
                "description": f"Custom tool {name}.",
                "action_id": action.id,
                "input_schema": input_schema,
                "is_readonly": is_readonly,
            }
        )

    def _mint_oauth_token(self, scope):
        """Craft a live OAuth access token row (for ``self.user_in``); return the
        raw bearer."""
        raw = secrets.token_urlsafe(48)
        self.env["mcp.oauth.token"].sudo().create(
            {
                "access_token_hash": _sha256_hex(raw),
                "client": self.oauth_client.id,
                "user_id": self.user_in.id,
                "scope": scope,
                "audience": self.resource,
                "access_expires_at": fields.Datetime.now() + timedelta(hours=1),
            }
        )
        return raw

    # ------------------------------------------------------------------
    # RPC helpers
    # ------------------------------------------------------------------
    def _rpc(self, token, method, params=None):
        """POST a JSON-RPC ``method`` to /mcp with a bearer ``token``."""
        body = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            body["params"] = params
        headers = {"Authorization": f"Bearer {token}"}
        return self.url_open("/mcp", json=body, headers=headers)

    def _list_tools(self, token):
        """Return the full tool dicts advertised by ``tools/list`` for ``token``."""
        response = self._rpc(token, "tools/list")
        self.assertEqual(response.status_code, 200, response.text[:500])
        payload = response.json()
        self.assertNotIn("error", payload, msg=payload.get("error"))
        return payload["result"]["tools"]

    def _list_tool_names(self, token):
        """Return the tool names advertised by ``tools/list`` for ``token``."""
        return [tool["name"] for tool in self._list_tools(token)]

    def _call_tool(self, token, name, arguments=None):
        """Invoke ``tools/call`` over ``token`` and return the tool-result dict."""
        response = self._rpc(
            token, "tools/call", {"name": name, "arguments": arguments or {}}
        )
        self.assertEqual(response.status_code, 200, response.text[:500])
        payload = response.json()
        self.assertNotIn("error", payload, msg=payload.get("error"))
        return payload["result"]

    # ------------------------------------------------------------------
    # Execution: listing + annotations + argument round-trip
    # ------------------------------------------------------------------
    def test_tool_listed_with_annotations_and_round_trips(self):
        """A wrapped code action is listed with correct hints and round-trips args."""
        by_name = {tool["name"]: tool for tool in self._list_tools(self.key_in)}

        self.assertIn("echo_grouped", by_name)
        self.assertEqual(
            by_name["echo_grouped"]["annotations"],
            {"readOnlyHint": False, "destructiveHint": True},
        )
        self.assertEqual(
            by_name["echo_grouped"]["inputSchema"],
            json.loads(_ECHO_SCHEMA),
        )

        self.assertIn("echo_readonly", by_name)
        self.assertEqual(
            by_name["echo_readonly"]["annotations"],
            {"readOnlyHint": True, "destructiveHint": False},
        )

        # tools/call round-trips {"x": ...} -> result JSON {"echo": ...} in text.
        result = self._call_tool(self.key_in, "echo_grouped", {"x": "hello"})
        self.assertFalse(result["isError"], msg=result)
        self.assertEqual(
            json.loads(result["content"][0]["text"]), {"echo": "hello"}
        )
        # A custom schema without ``additionalProperties: false`` keeps
        # accepting undeclared keys (they travel in mcp['args']); the
        # unknown-argument check only enforces what a schema declares.
        result = self._call_tool(
            self.key_in, "echo_grouped", {"x": "hello", "extra": 1}
        )
        self.assertFalse(result["isError"], msg=result)

    def test_custom_tool_strict_schema_rejects_unknown_arg(self):
        """A custom schema with ``additionalProperties: false`` is enforced too."""
        self._make_tool(
            "echo_strict",
            code=_ECHO_CODE,
            group_ids=[(6, 0, [self.tool_group.id])],
            is_readonly=True,
            input_schema=json.dumps(
                {
                    "type": "object",
                    "properties": {"x": {"type": "string"}},
                    "additionalProperties": False,
                }
            ),
        )
        result = self._call_tool(
            self.key_in, "echo_strict", {"x": "hello", "extra": 1}
        )
        self.assertTrue(result["isError"], msg=result)
        self.assertIn("Unknown argument(s): extra", result["content"][0]["text"])
        result = self._call_tool(self.key_in, "echo_strict", {"x": "hello"})
        self.assertFalse(result["isError"], msg=result)

    def test_custom_tool_arguments_validated_against_schema(self):
        """Arguments breaking the tool's input schema are refused before it runs.

        type (unions, integer vs bool/fraction), enum, minItems and simple
        items.type are checked; the refusal is an isError result naming the
        argument, audited as an E400 row, and the action never runs.
        """
        self._make_tool(
            "typed_tool",
            code="mcp['result'] = {'n': mcp['args'].get('n')}",
            group_ids=[(6, 0, [self.tool_group.id])],
            is_readonly=True,
            input_schema=json.dumps(
                {
                    "type": "object",
                    "properties": {
                        "n": {"type": "integer"},
                        "mode": {"type": "string", "enum": ["a", "b"]},
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        },
                        "note": {"type": ["string", "null"]},
                        "ratio": {"type": "number"},
                    },
                    "required": ["n"],
                }
            ),
        )
        refused = {
            "string for integer": (
                {"n": "abc"},
                "Invalid argument 'n': must be an integer, got a string.",
            ),
            "bool for integer": (
                {"n": True},
                "Invalid argument 'n': must be an integer, got a boolean.",
            ),
            "fraction for integer": (
                {"n": 1.5},
                "Invalid argument 'n': must be an integer, got a number.",
            ),
            "null for required": (
                {"n": None},
                "Invalid argument 'n': must be an integer, got null.",
            ),
            "enum": (
                {"n": 1, "mode": "c"},
                "Invalid argument 'mode': must be one of a, b.",
            ),
            "minItems": (
                {"n": 1, "tags": []},
                "Invalid argument 'tags': must be a non-empty list.",
            ),
            "items.type": (
                {"n": 1, "tags": ["x", 2]},
                "Invalid argument 'tags': must be a list of strings; item 1 is an "
                "integer.",
            ),
            "union": (
                {"n": 1, "note": 5},
                "Invalid argument 'note': must be a string or null, got an integer.",
            ),
        }
        log_model = self.env["mcp.log"].sudo()
        for label, (arguments, message) in refused.items():
            with self.subTest(label):
                result = self._call_tool(self.key_in, "typed_tool", arguments)
                self.assertTrue(result["isError"], msg=result)
                self.assertEqual(result["content"][0]["text"], message)
                self.env.invalidate_all()
                row = log_model.search(
                    [("operation", "=", "typed_tool")], order="id desc", limit=1
                )
                self.assertEqual(row.error_code, "E400")
                self.assertEqual(row.error_message, message)

        accepted = (
            {"n": 3},
            {"n": 3.0},
            {"n": 1, "mode": "b", "tags": ["x"], "note": None, "ratio": 2},
            {"n": 1, "mode": None},
        )
        for arguments in accepted:
            with self.subTest(arguments=arguments):
                result = self._call_tool(self.key_in, "typed_tool", arguments)
                self.assertFalse(result["isError"], msg=result)

    def test_custom_tool_unreadable_schema_parts_do_not_crash(self):
        """Schema parts the validator cannot read are not enforced, never a crash.

        An unknown type name, a non-object property schema, a non-list enum or
        a non-integer minItems leave the argument unchecked; the call runs.
        """
        self._make_tool(
            "odd_schema_tool",
            code=_ECHO_CODE,
            group_ids=[(6, 0, [self.tool_group.id])],
            is_readonly=True,
            input_schema=json.dumps(
                {
                    "type": "object",
                    "properties": {
                        "x": {"type": "int", "enum": "abc", "minItems": "2"},
                        "y": 5,
                        "z": {"type": [], "items": "string"},
                    },
                }
            ),
        )
        with mute_logger("odoo.addons.mcp_server.controllers.mcp"):
            result = self._call_tool(
                self.key_in, "odd_schema_tool", {"x": [1], "y": "a", "z": [2]}
            )
        self.assertFalse(result["isError"], msg=result)
        self.assertEqual(json.loads(result["content"][0]["text"]), {"echo": [1]})

    # ------------------------------------------------------------------
    # Access gate: action's Allowed Groups (enforced NON-su, list + call)
    # ------------------------------------------------------------------
    def test_user_in_group_can_list_and_call(self):
        """A user in the action's Allowed Groups sees and can run the tool."""
        self.assertIn("echo_grouped", self._list_tool_names(self.key_in))
        result = self._call_tool(self.key_in, "echo_grouped", {"x": "hi"})
        self.assertFalse(result["isError"], msg=result)

    def test_user_not_in_group_hidden_and_denied(self):
        """A user outside the Allowed Groups: tool hidden + call access-denied."""
        self.assertNotIn("echo_grouped", self._list_tool_names(self.key_out))
        result = self._call_tool(self.key_out, "echo_grouped", {"x": "hi"})
        self.assertTrue(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertIn("not allowed", text.lower())
        self.assertNotIn("Traceback", text)

    def test_empty_groups_fallback_visible_with_model_write(self):
        """With no Allowed Groups, a user WITH model write access sees the tool."""
        self.assertIn("echo_nogroup", self._list_tool_names(self.key_in))
        result = self._call_tool(self.key_in, "echo_nogroup", {"x": "ok"})
        self.assertFalse(result["isError"], msg=result)

    def test_empty_groups_fallback_hidden_without_model_write(self):
        """With no Allowed Groups, a user WITHOUT model write access: hidden + denied."""
        self.assertNotIn("echo_nogroup", self._list_tool_names(self.key_out))
        result = self._call_tool(self.key_out, "echo_nogroup", {"x": "no"})
        self.assertTrue(result["isError"], msg=result)
        self.assertIn("not allowed", result["content"][0]["text"].lower())

    # ------------------------------------------------------------------
    # Exception inside the action code -> sanitized isError + rollback
    # ------------------------------------------------------------------
    @mute_logger(
        "odoo.addons.mcp_server.controllers.error_sanitizer",
        "odoo.addons.mcp_server.controllers.mcp",
    )
    def test_action_exception_rolls_back_and_is_error(self):
        """An action that creates a record then raises: sanitized isError + rollback."""
        marker = "MCP Custom Rollback %s" % int(time.time() * 1000000)
        code = (
            "env['res.partner'].create({'name': %r})\n"
            "raise UserError('deliberate custom-tool failure')"
        ) % marker
        self._make_tool(
            "rollback_tool",
            code=code,
            group_ids=[(6, 0, [self.tool_group.id])],
            is_readonly=False,
        )

        result = self._call_tool(self.key_in, "rollback_tool", {})
        self.assertTrue(result["isError"], msg=result)
        self.assertNotIn("Traceback", result["content"][0]["text"])

        # The partial create must have been rolled back by the controller savepoint.
        self.env.invalidate_all()
        orphan = self.env["res.partner"].search([("name", "=", marker)])
        self.assertFalse(orphan, "the action's partial write must be rolled back")

    # ------------------------------------------------------------------
    # Non-elevation: the wrapped action executes under the CALLER's ACLs,
    # never sudo. This is the discriminating test -- a regression of _run_tool
    # to a sudo() exec keeps every echo/entitled-write test green but fails here.
    # ------------------------------------------------------------------
    @mute_logger(
        "odoo.addons.mcp_server.controllers.error_sanitizer",
        "odoo.addons.mcp_server.controllers.mcp",
    )
    def test_action_runs_under_caller_acls_not_sudo(self):
        """Authorized caller + code touching a forbidden model -> access-denied isError.

        user_in is in the tool group, so _user_can_run passes and the tool runs;
        but user_in (internal + partner manager) has NO rights on
        ir.config_parameter (system-only). The action code tries to create one.
        Under the correct non-su execution the ORM raises AccessError -> sanitized
        isError and nothing persists. Had _run_tool regressed to a sudo() exec,
        the parameter would be created and the call would succeed -- so this fails
        closed on that privilege escalation.
        """
        marker = "mcp.escalation.probe.%d" % int(time.time() * 1000000)
        code = "env['ir.config_parameter'].create({'key': %r, 'value': '1'})" % marker
        self._make_tool(
            "escalation_probe",
            code=code,
            group_ids=[(6, 0, [self.tool_group.id])],
            is_readonly=False,
        )

        result = self._call_tool(self.key_in, "escalation_probe", {})
        self.assertTrue(result["isError"], msg=result)
        self.assertNotIn("Traceback", result["content"][0]["text"])

        # Decisive: the forbidden write must not have taken effect. A sudo
        # regression would have created the parameter.
        self.env.invalidate_all()
        leaked = self.env["ir.config_parameter"].sudo().search([("key", "=", marker)])
        self.assertFalse(leaked, "tool code must execute under caller ACLs, never sudo")

    # ------------------------------------------------------------------
    # Runtime state guard: an action flipped to non-code AFTER the tool passed
    # its create-time @api.constrains must fail loud, never silently no-op into
    # a false "completed successfully".
    # ------------------------------------------------------------------
    @mute_logger(
        "odoo.addons.mcp_server.controllers.error_sanitizer",
        "odoo.addons.mcp_server.controllers.mcp",
    )
    def test_action_flipped_to_non_code_after_create_fails_loud(self):
        """A tool whose action is non-code at call time yields a clean isError.

        The action @api.constrains only re-fires when action_id is (re)assigned,
        so an admin can flip the linked ir.actions.server.state to a per-record
        type after the tool passed validation. A record-less tool call would then
        run() to no effect (no active_ids) and report false success -- so
        _run_tool has a runtime state backstop that raises a clean isError.
        """
        tool = self._make_tool(
            "echo_flip",
            code=_ECHO_CODE,
            group_ids=[(6, 0, [self.tool_group.id])],
            is_readonly=False,
        )
        # Mutate the underlying action to a non-code state DIRECTLY: this writes
        # ir.actions.server, NOT mcp.custom.tool, so the tool's action_id
        # @api.constrains never fires -- exactly the gap the runtime guard closes.
        tool.action_id.sudo().write({"state": "object_write"})

        result = self._call_tool(self.key_in, "echo_flip", {"x": "hi"})
        self.assertTrue(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertIn("not a Python Code action", text)
        self.assertNotIn("completed successfully", text.lower())
        self.assertNotIn("Traceback", text)

    # ------------------------------------------------------------------
    # Scope interplay: an mcp:read OAuth session may call read-only tools only
    # ------------------------------------------------------------------
    def test_read_scope_blocks_write_tool_allows_readonly_tool(self):
        """Over an mcp:read token: a write custom tool is blocked, a read-only one runs."""
        token = self._mint_oauth_token("mcp:read")

        names = self._list_tool_names(token)
        # is_readonly=False custom tool is hidden from a read-only session...
        self.assertNotIn("echo_grouped", names)
        # ...and is_readonly=True custom tool stays visible.
        self.assertIn("echo_readonly", names)

        denied = self._call_tool(token, "echo_grouped", {"x": "nope"})
        self.assertTrue(denied["isError"], msg=denied)
        self.assertIn("read-only", denied["content"][0]["text"].lower())
        self.assertNotIn("Traceback", denied["content"][0]["text"])

        allowed = self._call_tool(token, "echo_readonly", {"x": "yes"})
        self.assertFalse(allowed["isError"], msg=allowed)
        self.assertEqual(
            json.loads(allowed["content"][0]["text"]), {"echo": "yes"}
        )

    # ------------------------------------------------------------------
    # Audit: a custom-tool call writes an mcp.log row (operation = tool name)
    # ------------------------------------------------------------------
    def test_custom_tool_call_writes_audit_row(self):
        """A custom-tool tools/call persists an mcp.log row keyed by the tool name."""
        result = self._call_tool(self.key_in, "echo_grouped", {"x": "audit"})
        self.assertFalse(result["isError"], msg=result)

        self.env.invalidate_all()
        rows = (
            self.env["mcp.log"]
            .sudo()
            .search(
                [
                    ("event_type", "=", "model_access"),
                    ("operation", "=", "echo_grouped"),
                    ("user_id", "=", self.user_in.id),
                ]
            )
        )
        self.assertTrue(rows, "a custom-tool call must write an mcp.log audit row")

    # ------------------------------------------------------------------
    # Authorization gate fires BEFORE the required-arg check (no field leak)
    # ------------------------------------------------------------------
    def test_required_arg_name_not_leaked_to_unauthorized_caller(self):
        """A hidden tool's required-arg names must not leak to an unauthorized caller.

        A grouped tool with a REQUIRED ``target_id`` argument. An authorized
        caller who omits it gets the normal missing-arg validation isError
        (SEP-1303) that names the field; an UNAUTHORIZED caller of the (hidden)
        tool gets an access-denied isError that does NOT reveal the
        required-arg name -- the auth gate fires before the required-arg check.
        """
        self._make_tool(
            "req_tool",
            code=_ECHO_CODE,
            group_ids=[(6, 0, [self.tool_group.id])],
            is_readonly=False,
            input_schema=_REQUIRED_SCHEMA,
        )

        # Authorized caller (in the tool group), omitting target_id -> the
        # normal missing-arg validation isError (SEP-1303), which DOES name
        # the field.
        result = self._call_tool(self.key_in, "req_tool", {})
        self.assertTrue(result["isError"], msg=result)
        self.assertIn("target_id", result["content"][0]["text"])

        # Unauthorized caller (not in the tool group): access-denied isError
        # that does NOT reveal the required-arg name (the gate fired first).
        result = self._call_tool(self.key_out, "req_tool", {})
        self.assertTrue(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertIn("not allowed", text.lower())
        self.assertNotIn("target_id", text)
        self.assertNotIn("required argument", text.lower())

    # ------------------------------------------------------------------
    # Read-only scope refusal fires BEFORE the required-arg check (no leak)
    # ------------------------------------------------------------------
    def test_read_scope_refusal_precedes_required_arg_check(self):
        """An mcp:read caller ENTITLED to a write tool gets the read-only refusal,
        not a validation error that names the tool's required-argument fields.

        The caller (user_in) IS in the tool's Allowed Groups, so the
        authorization gate passes; the tool is write (is_readonly=False) and the
        token is mcp:read, so the scope gate must refuse it. Because the scope
        gate precedes the required-arg check, omitting the required ``target_id``
        yields the read-only isError -- the validation isError that would name
        ``target_id`` never runs, so an entitled read-only caller cannot harvest
        the required-arg field name.
        """
        self._make_tool(
            "req_write_tool",
            code=_ECHO_CODE,
            group_ids=[(6, 0, [self.tool_group.id])],
            is_readonly=False,
            input_schema=_REQUIRED_SCHEMA,
        )
        token = self._mint_oauth_token("mcp:read")

        # Omit the required target_id: the response is the READ-ONLY isError
        # result -- not the missing-arg validation isError (its text is
        # asserted below to not name the field).
        result = self._call_tool(token, "req_write_tool", {})
        self.assertTrue(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertIn("read-only", text.lower())
        # The required-arg check never ran, so its field name is not leaked.
        self.assertNotIn("target_id", text)
        self.assertNotIn("required argument", text.lower())
        self.assertNotIn("Traceback", text)

    # ------------------------------------------------------------------
    # active=False: excluded from tools/list AND unknown at tools/call
    # ------------------------------------------------------------------
    def test_inactive_tool_absent_from_list_and_unknown_on_call(self):
        """An inactive custom tool is not listed and tools/call reports unknown-tool."""
        tool = self._make_tool(
            "echo_inactive",
            code=_ECHO_CODE,
            group_ids=[(6, 0, [self.tool_group.id])],
            is_readonly=False,
        )
        tool.active = False

        self.assertNotIn("echo_inactive", self._list_tool_names(self.key_in))

        resp = self._rpc(
            self.key_in, "tools/call", {"name": "echo_inactive", "arguments": {}}
        )
        self.assertEqual(resp.status_code, 200, resp.text[:500])
        payload = resp.json()
        self.assertIn("error", payload, msg=payload)
        self.assertEqual(payload["error"]["code"], -32602)
        self.assertIn("Unknown tool", payload["error"]["message"])

    # ------------------------------------------------------------------
    # Portal callers are refused AT THE DOOR: the MCP access group implies
    # base.group_user, so a portal user can never hold it and every portal
    # credential is 403'd before dispatch. The sudo display-read machinery is
    # pinned model-side by test_portal_visible_tools_empty_while_plain_search_
    # raises / test_run_tool_authorized_caller_without_model_read_round_trips,
    # and controller-side for internal users by
    # test_non_admin_internal_key_lists_builtins_without_model_read.
    # ------------------------------------------------------------------
    def test_portal_oauth_token_refused_at_door_with_403(self):
        """A live OAuth token bound to a portal user is refused with 403.

        Even a token whose action group_ids would authorize the wrapped tool:
        the per-user MCP opt-in gate (mcp_server.group_mcp_user) runs before any
        tool dispatch, and portal users cannot be members. 403 -- not 401 --
        so a spec-following client surfaces the error instead of looping
        through a re-auth that would only mint another refused token.
        """
        unique_id = str(int(time.time() * 1000))[-6:]
        groups_field = users_groups_field(self.env)
        portal_group = self.env.ref("base.group_portal")
        portal = create_test_user(
            self.env,
            "MCP Portal",
            f"mcp_portal_{unique_id}",
            email=f"mcp_portal_{unique_id}@example.com",
            **{groups_field: [(6, 0, [portal_group.id])]},
        )
        # A tool whose Allowed Groups include base.group_portal -- the door gate,
        # not the tool authorization, must be what refuses the call.
        self._make_tool(
            "echo_portal_ctrl",
            code=_ECHO_CODE,
            group_ids=[(6, 0, [portal_group.id])],
            is_readonly=True,
        )
        # A live OAuth access token bound to the portal user (mcp:read).
        raw = secrets.token_urlsafe(48)
        self.env["mcp.oauth.token"].sudo().create(
            {
                "access_token_hash": _sha256_hex(raw),
                "client": self.oauth_client.id,
                "user_id": portal.id,
                "scope": "mcp:read",
                "audience": self.resource,
                "access_expires_at": fields.Datetime.now() + timedelta(hours=1),
            }
        )

        response = self._rpc(raw, "tools/list")
        self.assertEqual(response.status_code, 403, response.text[:500])

    def test_non_admin_internal_key_lists_builtins_without_model_read(self):
        """A non-admin internal user (no mcp.custom.tool read) still gets tools/list.

        With the mcp.custom.tool read ACL admin-only, the controller's
        sudo-mediated discovery/display reads are what let a plain base.group_user
        (with only an rpc API key, no admin) still enumerate the builtins over
        tools/list -- no direct model read required.
        """
        names = self._list_tool_names(self.key_out)
        self.assertIn("search_records", names)
        # tools/list succeeded (no crash); grouped custom tools the user cannot
        # run stay hidden by the non-su gate.
        self.assertNotIn("echo_grouped", names)

    def test_portal_visible_tools_empty_while_plain_search_raises(self):
        """_visible_tools() for a portal user returns empty WITHOUT raising, even
        though a plain (non-su) search on the model raises AccessError.

        Pins that the sudo re-bind in _visible_tools() is exactly what saves the
        portal caller: the discovery search is sudo'd (no AccessError), while the
        caller-bound filter still excludes every tool the portal user cannot run.
        """
        unique_id = str(int(time.time() * 1000))[-6:]
        groups_field = users_groups_field(self.env)
        portal = create_test_user(
            self.env,
            "MCP Portal Unit",
            f"mcp_portal_unit_{unique_id}",
            email=f"mcp_portal_unit_{unique_id}@example.com",
            **{groups_field: [(6, 0, [self.env.ref("base.group_portal").id])]},
        )
        Tool = self.env["mcp.custom.tool"].with_user(portal)
        # A plain search would raise (portal has no mcp.custom.tool read ACL)...
        with self.assertRaises(AccessError):
            Tool.search([("active", "=", True)])
        # ...but _visible_tools() sudo's the discovery, so it returns cleanly empty.
        self.assertFalse(Tool._visible_tools())

    def test_visible_tools_non_su_gate_no_escalation(self):
        """The sudo discovery re-bind does not escalate an internal caller.

        _visible_tools() runs the discovery search under sudo() but re-binds
        (with_env) to the caller before _user_can_run(), so the write check is
        evaluated as the REAL user -- not short-circuited True under sudo. An
        internal user (base.group_user) not in a group-less tool's model-write set
        is therefore still excluded.
        """
        visible = (
            self.env["mcp.custom.tool"].with_user(self.user_out)._visible_tools()
        )
        names = visible.mapped("name")
        # user_out is not in the tool group and lacks res.partner write access.
        self.assertNotIn("echo_grouped", names)
        self.assertNotIn("echo_nogroup", names)

    def test_run_tool_authorized_caller_without_model_read_round_trips(self):
        """A caller LACKING mcp.custom.tool read but AUTHORIZED to run runs it.

        Pins that _run_tool reads the action ref under sudo: the runtime state
        guard and the execution call navigate ``self.action_id``, and a portal
        user -- who has no mcp.custom.tool read ACL (that ACL is admin-only, and
        the portal user is not an admin) but IS in the action's Allowed Groups --
        would hit AccessError just reaching action_id under a plain (non-su) read.
        With ``self.sudo().action_id`` (read the action ref under sudo) plus
        ``.with_env(self.env)`` (run the code under the caller's non-su ACLs) the
        call round-trips its result.
        """
        unique_id = str(int(time.time() * 1000))[-6:]
        groups_field = users_groups_field(self.env)
        portal_group = self.env.ref("base.group_portal")
        portal = create_test_user(
            self.env,
            "MCP Portal Run",
            f"mcp_portal_run_{unique_id}",
            email=f"mcp_portal_run_{unique_id}@example.com",
            **{groups_field: [(6, 0, [portal_group.id])]},
        )
        # A tool whose action Allowed Groups INCLUDE base.group_portal, so
        # _user_can_run() returns True for the portal user via the group branch
        # (never the model-write fallback).
        tool = self._make_tool(
            "echo_portal_run",
            code=_ECHO_CODE,
            group_ids=[(6, 0, [portal_group.id])],
            is_readonly=False,
        )

        # Precondition: the portal user genuinely has no mcp.custom.tool read, so a
        # plain (non-su) navigation of action_id raises.
        with self.assertRaises(AccessError):
            tool.with_user(portal).action_id.state

        # _run_tool sudo-reads the action ref (state guard + execution) while
        # running the code under the portal caller's env, so it round-trips
        # {"echo": ...} rather than raising AccessError.
        result = tool.with_user(portal)._run_tool({"x": "portal"})
        self.assertEqual(result, {"echo": "portal"})

    # ------------------------------------------------------------------
    # Audit: an access-denied custom-tool call writes a permission_denied row
    # (NOT a generic E500 error row).
    # ------------------------------------------------------------------
    def test_access_denied_custom_tool_call_writes_permission_denied_row(self):
        """A user-not-in-group custom-tool call audits a permission_denied event.

        The authorization gate returns an isError result (it does not raise),
        so the denial must be audited as a distinct permission_denied row keyed on
        the tool name -- not the generic E500 the shared finally would otherwise
        write.
        """
        result = self._call_tool(self.key_out, "echo_grouped", {"x": "nope"})
        self.assertTrue(result["isError"], msg=result)

        self.env.invalidate_all()
        denied = (
            self.env["mcp.log"]
            .sudo()
            .search(
                [
                    ("event_type", "=", "permission_denied"),
                    ("operation", "=", "echo_grouped"),
                    ("user_id", "=", self.user_out.id),
                    ("endpoint", "=", "/mcp"),
                ]
            )
        )
        self.assertTrue(
            denied, "an access-denied custom-tool call must audit a permission_denied row"
        )
        # And the same call must NOT also be logged as a generic E500 error.
        e500 = (
            self.env["mcp.log"]
            .sudo()
            .search(
                [
                    ("event_type", "=", "error"),
                    ("error_code", "=", "E500"),
                    ("operation", "=", "echo_grouped"),
                    ("user_id", "=", self.user_out.id),
                ]
            )
        )
        self.assertFalse(
            e500, "the denial must not be logged as a generic E500 error row"
        )

    # ------------------------------------------------------------------
    # Defensive guard: a corrupted `required` (raw-SQL row, bypassing the
    # @api.constrains) must not crash the required-arg check -- the call returns
    # a NORMAL response and still writes exactly one audit row (invariant).
    # ------------------------------------------------------------------
    def test_corrupted_required_does_not_crash_tools_call(self):
        """A malformed `required` written past the constraint degrades cleanly.

        The @api.constrains normally keeps `required` a list of strings, but a
        distributable module must tolerate a corrupted row (e.g. one written via
        raw SQL). The consumer coerces a non-list `required` to empty, so the
        call round-trips a NORMAL result (not a -32603 crash) and still audits.
        """
        tool = self._make_tool(
            "echo_corrupt_required",
            code=_ECHO_CODE,
            group_ids=[(6, 0, [self.tool_group.id])],
            is_readonly=False,
        )
        # Bypass the @api.constrains: write a malformed `required` (a bare int)
        # straight to the column via raw SQL, then drop the ORM cache.
        self.env.cr.execute(
            "UPDATE mcp_custom_tool SET input_schema = %s WHERE id = %s",
            ['{"type": "object", "required": 42}', tool.id],
        )
        self.env.invalidate_all()

        result = self._call_tool(self.key_in, "echo_corrupt_required", {"x": "hi"})
        # NOT a -32603 crash: a normal isError=False result that round-trips.
        self.assertFalse(result["isError"], msg=result)
        self.assertEqual(
            json.loads(result["content"][0]["text"]), {"echo": "hi"}
        )

        # Invariant restored: the call still writes its mcp.log audit row.
        self.env.invalidate_all()
        rows = (
            self.env["mcp.log"]
            .sudo()
            .search(
                [
                    ("event_type", "=", "model_access"),
                    ("operation", "=", "echo_corrupt_required"),
                    ("user_id", "=", self.user_in.id),
                ]
            )
        )
        self.assertEqual(
            len(rows), 1, "a corrupted-row call must still write exactly one audit row"
        )

    # ------------------------------------------------------------------
    # Defensive guard: a corrupted `input_schema` (raw-SQL row, bypassing the
    # @api.constrains) must be SKIPPED in tools/list, never crash the handshake
    # -- a sibling valid tool stays listed and there is no -32603.
    # ------------------------------------------------------------------
    def test_tools_list_skips_custom_tool_with_malformed_input_schema(self):
        """A custom tool with a corrupted input_schema is skipped in tools/list.

        The @api.constrains normally keeps input_schema valid JSON, but a
        distributable module must tolerate a corrupted row (e.g. one written via
        raw SQL). tools/list logs and skips the single bad row -- the malformed
        tool's name is ABSENT while a sibling valid tool's name is PRESENT, and
        the handshake does not crash with a -32603.
        """
        malformed = self._make_tool(
            "echo_malformed_schema",
            code=_ECHO_CODE,
            group_ids=[(6, 0, [self.tool_group.id])],
            is_readonly=False,
        )
        # Bypass the @api.constrains: write invalid JSON straight to the column
        # via raw SQL, then drop the ORM cache.
        self.env.cr.execute(
            "UPDATE mcp_custom_tool SET input_schema = %s WHERE id = %s",
            ["{not valid json", malformed.id],
        )
        self.env.invalidate_all()

        # _list_tool_names asserts no "error" in the payload, so this also pins
        # that the malformed row does not surface as a -32603 internal error.
        names = self._list_tool_names(self.key_in)
        self.assertNotIn("echo_malformed_schema", names)
        # A sibling valid tool the same caller may run stays listed.
        self.assertIn("echo_grouped", names)

    # ------------------------------------------------------------------
    # Defensive guard: a VALID-JSON but non-dict `input_schema` (raw-SQL row,
    # bypassing the @api.constrains) must be treated like a corrupted row --
    # SKIPPED in tools/list and DEGRADED to {} in tools/call -- never advertised
    # verbatim (invalid `inputSchema`) nor crashed with a -32603.
    # ------------------------------------------------------------------
    def test_non_dict_input_schema_skipped_in_list_and_degrades_on_call(self):
        """A valid-JSON but non-object input_schema is guarded at both sites.

        The @api.constrains normally keeps input_schema a JSON object, but a
        distributable module must tolerate a corrupted row (e.g. `42` written
        via raw SQL). `_parsed_input_schema` returns None for a non-dict value,
        so tools/list SKIPS the bad row (a sibling valid tool stays listed, no
        -32603) and tools/call DEGRADES the schema to {} -- the tool runs
        normally (isError False) and still writes its audit row.
        """
        tool = self._make_tool(
            "echo_nondict_schema",
            code=_ECHO_CODE,
            group_ids=[(6, 0, [self.tool_group.id])],
            is_readonly=False,
        )
        # Bypass the @api.constrains: write valid JSON that is NOT an object
        # (a bare int) straight to the column via raw SQL, then drop the cache.
        self.env.cr.execute(
            "UPDATE mcp_custom_tool SET input_schema = %s WHERE id = %s",
            ["42", tool.id],
        )
        self.env.invalidate_all()

        # (a) Absent from tools/list; a sibling valid tool stays listed; no
        # -32603 (_list_tool_names asserts no "error" in the payload).
        names = self._list_tool_names(self.key_in)
        self.assertNotIn("echo_nondict_schema", names)
        self.assertIn("echo_grouped", names)

        # (b) tools/call degrades the schema to {} and round-trips a NORMAL
        # result (not a -32603 crash).
        result = self._call_tool(self.key_in, "echo_nondict_schema", {"x": "hi"})
        self.assertFalse(result["isError"], msg=result)
        self.assertEqual(json.loads(result["content"][0]["text"]), {"echo": "hi"})

        # Invariant: the call still writes its mcp.log audit row.
        self.env.invalidate_all()
        rows = (
            self.env["mcp.log"]
            .sudo()
            .search(
                [
                    ("event_type", "=", "model_access"),
                    ("operation", "=", "echo_nondict_schema"),
                    ("user_id", "=", self.user_in.id),
                ]
            )
        )
        self.assertEqual(
            len(rows), 1, "a non-dict-schema call must still write exactly one audit row"
        )

    # ------------------------------------------------------------------
    # A non-string tool `name` must be refused as a clean invalid-params
    # (-32602) error -- never an unaudited -32603 -- and must audit an E400 row.
    # ------------------------------------------------------------------
    def test_non_string_tool_name_returns_invalid_params_and_audits(self):
        """tools/call with a non-string `name` yields -32602 (not -32603) + audit.

        `name` is unchecked client JSON that reaches index.get(name); an
        unhashable list/dict would raise TypeError OUTSIDE any try (an unaudited
        -32603). The name guard refuses it as a clean -32602 and audits an E400
        row (coercing the non-string name for the row's Char operation field).
        """
        for bad_name in ([1, 2], 42):
            resp = self._rpc(
                self.key_in,
                "tools/call",
                {"name": bad_name, "arguments": {}},
            )
            self.assertEqual(resp.status_code, 200, resp.text[:500])
            payload = resp.json()
            self.assertIn("error", payload, msg=payload)
            self.assertEqual(payload["error"]["code"], -32602, msg=payload)

        self.env.invalidate_all()
        rows = (
            self.env["mcp.log"]
            .sudo()
            .search(
                [
                    ("event_type", "=", "error"),
                    ("error_code", "=", "E400"),
                    ("user_id", "=", self.user_in.id),
                    ("endpoint", "=", "/mcp"),
                ]
            )
        )
        # Both malformed calls audited, with the coerced name as the operation.
        ops = set(rows.mapped("operation"))
        self.assertIn("[1, 2]", ops)
        self.assertIn("42", ops)

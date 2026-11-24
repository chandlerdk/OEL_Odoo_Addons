"""Tests for the native MCP read tools (``tools/call`` + resources).

Exercises the read-side tool layer wired onto ``mcp.mixin`` and served through
``POST /mcp``:

* ``tools/list`` advertises the read tools (``list_models`` / ``get_record`` /
  ``get_fields`` / ``search_records`` / ``aggregate_records`` /
  ``list_resource_templates``) with their JSON-Schema ``inputSchema``.
* ``tools/call search_records`` returns smart-field selection + LLM-formatted
  text + ``structuredContent`` with pagination info.
* Access control: a non-enabled model is refused (``isError``, no traceback) and
  a low-privilege user is blocked by Odoo's own ACL on an enabled model.
* Binary/image fields are replaced with ``odoo://record/...`` URIs, and
  ``resources/read`` materialises both a binary field (blob) and a textual
  ``ir.attachment`` (inline text).

Mirrors the HttpCase + ``_generate("rpc", ...)`` key-minting pattern of
``test_mcp_protocol.py``; all requests ride ``self.url_open`` so the HttpCase
test-cursor cookie travels with them (needed by the audit's independent cursor).
"""

import base64
import json
import time
from contextlib import contextmanager
from unittest.mock import patch

from odoo.exceptions import AccessError, MissingError, UserError
from odoo.tests import common, tagged

from ..controllers import rate_limiting, utils
from ..models.mcp_tools_read import DEFAULT_LIMIT, MAX_LIMIT
from ..tools.smart_fields import DEFAULT_MAX_SMART_FIELDS
from .test_helpers import create_test_user, grant_mcp_access, users_groups_field

# Valid 1x1 RGB PNG, used to populate ``res.partner.image_1920``.
_PNG_1X1 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4"
    "nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)

READ_TOOL_NAMES = {
    "list_models",
    "get_record",
    "get_fields",
    "search_records",
    "aggregate_records",
    "list_resource_templates",
}


@tagged("much_unit", "post_install", "-at_install")
class TestMcpReadTools(common.HttpCase):
    """Native MCP read tools + resources on ``/mcp``."""

    def setUp(self):
        super().setUp()
        utils.clear_mcp_caches()
        # Reset the shared in-memory rate-limit cache for test independence.
        rate_limiting._api_limiter.clear()

        unique_id = str(int(time.time() * 1000))[-6:]

        # Key-owner user (default internal-user groups -> can read res.partner,
        # but NOT res.mail_server which is group_system only).
        self.mcp_user = create_test_user(
            self.env,
            "MCP Read User",
            f"mcp_read_user_{unique_id}",
            email=f"mcp_read_{unique_id}@example.com",
        )
        grant_mcp_access(self.mcp_user)
        self.api_key = self._mint_key(self.mcp_user, "Read Tools Key")

        # Low-privilege user pinned to the bare internal-user group, so it
        # cannot read an admin-only model even when that model is MCP-enabled.
        groups_field = users_groups_field(self.env)
        self.low_priv_user = create_test_user(
            self.env,
            "MCP Low Priv User",
            f"mcp_lowpriv_user_{unique_id}",
            email=f"mcp_lowpriv_{unique_id}@example.com",
            **{groups_field: [(6, 0, [self.env.ref("base.group_user").id])]},
        )
        # The door requires the MCP group; the user stays "low priv" for the
        # Odoo ACL assertions (no extra functional groups).
        grant_mcp_access(self.low_priv_user)
        self.low_priv_key = self._mint_key(self.low_priv_user, "Low Priv Key")

        # Enable res.partner (read) for MCP.
        self.partner_model = self._enable_model(
            "base.model_res_partner", allow_read=True
        )
        # Enable an admin-only model (read) -- the low-priv user is blocked by
        # Odoo ACL even though MCP allows the read operation.
        self.mail_server_model = self._enable_model(
            "base.model_ir_mail_server", allow_read=True
        )
        # Enable ir.attachment (read) for MCP -- the odoo://attachment/{id}
        # resource scheme is now gated on the allow-list (attachment access
        # requires ir.attachment or the attachment's parent model enabled).
        self._enable_model("base.model_ir_attachment", allow_read=True)
        # Ensure res.users is NOT MCP-enabled (non-enabled-model deny path).
        self._disable_model("base.model_res_users")

        # A partner carrying an image, to exercise the binary -> URI path.
        self.partner_with_image = self.env["res.partner"].create(
            {"name": f"Imaged Partner {unique_id}", "image_1920": _PNG_1X1}
        )

        # A textual, public ir.attachment for the inline-text resources/read
        # path. ``public=True`` makes it readable by the key user (no special
        # ACL), so the read runs as that user without bypassing access control.
        self.text_attachment = (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": "note.txt",
                    "mimetype": "text/plain",
                    "raw": b"hello mcp",
                    "public": True,
                }
            )
        )

        params = self.env["ir.config_parameter"].sudo()
        params.set_param("mcp_server.enabled", "True")
        # Logging on so the resources/read audit-row test can assert the
        # persisted mcp.log entry.
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

    def _disable_model(self, model_xmlid):
        """Remove any ``mcp.enabled.model`` row for ``model_xmlid``."""
        model_id = self.env.ref(model_xmlid).id
        existing = (
            self.env["mcp.enabled.model"].sudo().search([("model_id", "=", model_id)])
        )
        existing.unlink()

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

    def _rpc_result(self, method, params=None, api_key=_DEFAULT_KEY):
        """POST ``method`` and return the JSON-RPC ``result`` payload."""
        response = self._post_rpc(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
            api_key=api_key,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("error", payload, msg=payload.get("error"))
        return payload["result"]

    def _call_tool(self, name, arguments=None, api_key=_DEFAULT_KEY):
        """Invoke ``tools/call`` and return the tool-result dict."""
        return self._rpc_result(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            api_key=api_key,
        )

    # ------------------------------------------------------------------
    # tools/list -- read tools advertised with schemas
    # ------------------------------------------------------------------
    def test_tools_list_exposes_read_tools_with_schemas(self):
        """tools/list advertises every read tool with a valid inputSchema."""
        result = self._rpc_result("tools/list")
        tools = {tool["name"]: tool for tool in result["tools"]}

        self.assertTrue(
            READ_TOOL_NAMES.issubset(tools),
            msg=f"Missing read tools: {READ_TOOL_NAMES - set(tools)}",
        )

        for name in READ_TOOL_NAMES:
            schema = tools[name]["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertIn("properties", schema)

        # get_record requires both model and record_id.
        get_record_schema = tools["get_record"]["inputSchema"]
        self.assertEqual(set(get_record_schema["required"]), {"model", "record_id"})
        # search_records requires model and exposes the paging/filter args.
        search_schema = tools["search_records"]["inputSchema"]
        self.assertIn("model", search_schema["required"])
        for arg in ("model", "domain", "fields", "limit", "offset", "order"):
            self.assertIn(arg, search_schema["properties"])
        # aggregate_records requires model + groupby.
        self.assertEqual(
            set(tools["aggregate_records"]["inputSchema"]["required"]),
            {"model", "groupby"},
        )
        # get_fields requires only model and exposes the discovery filters.
        get_fields_schema = tools["get_fields"]["inputSchema"]
        self.assertEqual(set(get_fields_schema["required"]), {"model"})
        for arg in ("model", "field_names", "attributes"):
            self.assertIn(arg, get_fields_schema["properties"])

    # ------------------------------------------------------------------
    # search_records -- smart fields + formatted output + pagination
    # ------------------------------------------------------------------
    def test_search_records_returns_smart_fields_and_pagination(self):
        """search_records returns smart fields, formatted text and paging info."""
        # The assertions below compare against the code fallback constants, so
        # drop any limit parameters a previous settings save left in the DB.
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("mcp_server.default_limit", False)
        params.set_param("mcp_server.max_limit", False)

        result = self._call_tool("search_records", {"model": "res.partner"})

        self.assertFalse(result["isError"])
        # LLM-friendly text output is present and non-empty.
        self.assertTrue(result["content"])
        self.assertTrue(result["content"][0]["text"].strip())

        structured = result["structuredContent"]
        self.assertEqual(structured["model"], "res.partner")
        # Pagination info: capped default limit + total count + offset.
        self.assertEqual(structured["limit"], DEFAULT_LIMIT)
        self.assertEqual(structured["offset"], 0)
        self.assertIsInstance(structured["total"], int)
        self.assertGreaterEqual(structured["total"], 1)

        records = structured["records"]
        self.assertTrue(records)
        self.assertLessEqual(len(records), DEFAULT_LIMIT)

        first = records[0]
        self.assertIn("id", first)
        # Smart selection: more than just the id, but capped at the smart max.
        self.assertGreater(len(first), 1)
        self.assertLessEqual(len(first), DEFAULT_MAX_SMART_FIELDS)

    def test_tool_limit_settings_are_honored(self):
        """The configurable tool limits clamp page size and smart-field count.

        Lowering ``mcp_server.default_limit`` and ``mcp_server.max_smart_fields``
        must change the live behaviour of search_records end-to-end (proving the
        settings are read at runtime, not the baked-in fallbacks). Config is set
        inside the rolled-back test transaction, so no teardown restore is needed.
        """
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("mcp_server.default_limit", "3")
        params.set_param("mcp_server.max_smart_fields", "5")
        utils.clear_mcp_caches()

        # Ensure there are more partners than the lowered default page size.
        self.env["res.partner"].create(
            [{"name": f"Limit Partner {i}"} for i in range(5)]
        )

        result = self._call_tool("search_records", {"model": "res.partner"})
        self.assertFalse(result["isError"], msg=result)
        structured = result["structuredContent"]

        # default_limit drives the page size when the client omits 'limit'.
        self.assertEqual(structured["limit"], 3)
        self.assertLessEqual(len(structured["records"]), 3)

        # Smart selection now caps SCORED fields at 5; the always-on essential
        # fields (id/name/display_name/active) may be appended on top, so the
        # total stays well below the default cap and proves the setting applies.
        first = structured["records"][0]
        self.assertLessEqual(len(first), 5 + 4)
        self.assertLess(len(first), DEFAULT_MAX_SMART_FIELDS)

    def test_effective_limit_clamps_nonpositive_config(self):
        """A 0/negative default_limit or max_limit falls back to the constants.

        Odoo treats ``limit<=0`` as "no limit", so a misconfigured 0 here would
        silently remove the size cap (unbounded full-table fetch). The policy
        must clamp back to the module defaults instead.
        """
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("mcp_server.default_limit", "0")
        params.set_param("mcp_server.max_limit", "0")
        mixin = self.env["mcp.mixin"]

        # An explicit (even huge) limit is capped to MAX_LIMIT, never left at 0.
        self.assertEqual(mixin._effective_limit(9999), MAX_LIMIT)
        # An omitted / zero / negative limit falls back to DEFAULT_LIMIT, not 0.
        self.assertEqual(mixin._effective_limit(None), DEFAULT_LIMIT)
        self.assertEqual(mixin._effective_limit(0), DEFAULT_LIMIT)
        self.assertEqual(mixin._effective_limit(-5), DEFAULT_LIMIT)

    def test_effective_limit_default_never_exceeds_max(self):
        """A Default configured above the Maximum still never returns > Maximum.

        The cap always wins: with Default 500 / Maximum 100 a fields-less call
        (no / zero / negative ``limit``) must return at most 100 rows, never
        the raw default (500) -- that would contradict both the field help and
        the advertised schema text. The invariant also holds for the advertised
        ``limit`` description, which is computed from the same ``_limit_bounds``
        source, so the promise cannot drift from the behaviour.
        """
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("mcp_server.default_limit", "500")
        params.set_param("mcp_server.max_limit", "100")
        mixin = self.env["mcp.mixin"]

        # No / zero / negative limit uses the default, clamped down to the max.
        self.assertEqual(mixin._effective_limit(None), 100)
        self.assertEqual(mixin._effective_limit(0), 100)
        self.assertEqual(mixin._effective_limit(-1), 100)
        # An explicit limit is still independently capped at the max.
        self.assertEqual(mixin._effective_limit(9999), 100)

        # The advertised limit description promises the same clamped default
        # (100, not the raw 500) -- no divergence between text and behaviour.
        schema = mixin._mcp_live_input_schema(
            {
                "properties": {
                    "limit": {
                        "description": "Defaults to %(default)s, capped at %(max)s."
                    }
                }
            }
        )
        self.assertEqual(
            schema["properties"]["limit"]["description"],
            "Defaults to 100, capped at 100.",
        )

    def test_get_record_returns_smart_defaults(self):
        """get_record returns a single record with smart-default metadata."""
        result = self._call_tool(
            "get_record",
            {"model": "res.partner", "record_id": self.partner_with_image.id},
        )
        self.assertFalse(result["isError"])
        record = result["structuredContent"]["record"]
        self.assertEqual(record["id"], self.partner_with_image.id)
        # Smart defaults annotate how the field set was chosen.
        metadata = result["structuredContent"]["metadata"]
        self.assertEqual(metadata["field_selection_method"], "smart_defaults")

    def test_get_record_named_long_field_full_text(self):
        """A long field named in ``fields`` is whole in the text block."""
        partner = self.env["res.partner"].create(
            {"name": "Long Comment Partner", "comment": "word " * 1000}
        )
        stored = str(partner.comment)
        result = self._call_tool(
            "get_record",
            {"model": "res.partner", "record_id": partner.id, "fields": ["comment"]},
        )
        self.assertFalse(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertIn(stored, text)
        self.assertNotIn("truncated", text)

    # ------------------------------------------------------------------
    # x2many rendering -- inline names (get_record) vs. count (search)
    # ------------------------------------------------------------------
    def test_get_record_small_x2many_lists_names_inline(self):
        """A small x2many collection is listed inline by name (<= the cap)."""
        parent = self.env["res.partner"].create({"name": "Inline Parent"})
        children = self.env["res.partner"].create(
            [
                {"name": "Child Alpha", "parent_id": parent.id},
                {"name": "Child Beta", "parent_id": parent.id},
            ]
        )
        # No cap configured -> default (3) applies, so 2 children render inline.
        result = self._call_tool(
            "get_record",
            {
                "model": "res.partner",
                "record_id": parent.id,
                "fields": ["name", "child_ids"],
            },
        )
        self.assertFalse(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertIn("child_ids: 2 record(s)", text)
        self.assertIn("Items:", text)
        self.assertIn("Child Alpha", text)
        self.assertIn(f"(id {children[0].id})", text)
        self.assertIn(f"(id {children[1].id})", text)
        # Everything shown inline -> no fall-back search hint for this field.
        self.assertNotIn("View all", text)
        # structuredContent stays pure int-ids (the names are display-only).
        self.assertEqual(
            result["structuredContent"]["record"]["child_ids"], children.ids
        )

    def test_get_record_large_x2many_collapses_to_count_and_hint(self):
        """An x2many collection above the cap collapses to a count + search hint."""
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("mcp_server.max_related_items", "2")
        parent = self.env["res.partner"].create({"name": "Collapse Parent"})
        self.env["res.partner"].create(
            [{"name": f"Child {i}", "parent_id": parent.id} for i in range(3)]
        )
        result = self._call_tool(
            "get_record",
            {
                "model": "res.partner",
                "record_id": parent.id,
                "fields": ["name", "child_ids"],
            },
        )
        self.assertFalse(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertIn("child_ids: 3 record(s)", text)
        # Above the cap: no inline names, just the count + a search_records hint.
        self.assertNotIn("Items:", text)
        self.assertIn("View all", text)
        self.assertIn("search_records", text)
        # A one2many hint filters on its inverse field (parent_id), so it
        # returns the whole set -- not a truncated id list.
        self.assertIn(f'domain=[["parent_id", "=", {parent.id}]]', text)
        self.assertNotIn('"id", "in"', text)

    def test_get_record_x2many_inline_falls_back_when_unreadable(self):
        """A related model the caller cannot read collapses to count + hint."""
        # Expose the tag model so the collapse still offers a search hint
        # (the hint is suppressed only for non-enabled models).
        self._enable_model("base.model_res_partner_category", allow_read=True)
        utils.clear_mcp_caches()
        tag = self.env["res.partner.category"].create({"name": "VIP"})
        partner = self.env["res.partner"].create(
            {"name": "Tagged Partner", "category_id": [(6, 0, tag.ids)]}
        )
        # Simulate an ACL/record-rule denial on the related model: the inline
        # name read raises AccessError and the field must fall back gracefully.
        category_cls = type(self.env["res.partner.category"])
        with patch.object(category_cls, "read", side_effect=AccessError("denied")):
            result = self._call_tool(
                "get_record",
                {
                    "model": "res.partner",
                    "record_id": partner.id,
                    "fields": ["category_id"],
                },
            )
        self.assertFalse(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertIn("category_id: 1 record(s)", text)
        self.assertNotIn("Items:", text)
        self.assertIn("search_records", text)

    def test_search_records_x2many_renders_as_count(self):
        """search_records renders an x2many field as a count, not a raw id list."""
        parent = self.env["res.partner"].create({"name": "Search Parent"})
        self.env["res.partner"].create(
            [
                {"name": "SC One", "parent_id": parent.id},
                {"name": "SC Two", "parent_id": parent.id},
            ]
        )
        result = self._call_tool(
            "search_records",
            {
                "model": "res.partner",
                "domain": [["id", "=", parent.id]],
                "fields": ["name", "child_ids"],
            },
        )
        self.assertFalse(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertIn("child_ids: 2 record(s)", text)
        # Not a raw id list.
        self.assertNotIn("child_ids: [", text)

    def test_get_record_large_m2m_view_all_uses_id_list(self):
        """A collapsed many2many (no inverse field) hints with an id-in domain."""
        self._enable_model("base.model_res_partner_category", allow_read=True)
        utils.clear_mcp_caches()
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.max_related_items", "1"
        )
        tags = self.env["res.partner.category"].create([{"name": "M1"}, {"name": "M2"}])
        partner = self.env["res.partner"].create(
            {"name": "M2M Parent", "category_id": [(6, 0, tags.ids)]}
        )
        result = self._call_tool(
            "get_record",
            {
                "model": "res.partner",
                "record_id": partner.id,
                "fields": ["category_id"],
            },
        )
        self.assertFalse(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertIn("category_id: 2 record(s)", text)
        self.assertIn("model='res.partner.category'", text)
        # A many2many has no scalar inverse -> id-in domain, not a field filter.
        self.assertIn('domain=[["id", "in", ', text)

    def test_get_record_x2many_hint_suppressed_when_relation_not_enabled(self):
        """A collapsed relation whose target isn't MCP-enabled says so, no hint."""
        self._disable_model("base.model_res_partner_category")
        utils.clear_mcp_caches()
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.max_related_items", "1"
        )
        tags = self.env["res.partner.category"].create([{"name": "N1"}, {"name": "N2"}])
        partner = self.env["res.partner"].create(
            {"name": "Not-exposed relation", "category_id": [(6, 0, tags.ids)]}
        )
        result = self._call_tool(
            "get_record",
            {
                "model": "res.partner",
                "record_id": partner.id,
                "fields": ["category_id"],
            },
        )
        self.assertFalse(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertIn("category_id: 2 record(s)", text)
        # No dead search hint; instead a note explaining why.
        self.assertNotIn("View all", text)
        self.assertIn("res.partner.category", text)
        self.assertIn("not enabled for MCP", text)

    def test_get_record_small_x2many_not_inlined_when_relation_not_enabled(self):
        """A small x2many is NOT inlined when its target isn't MCP-enabled.

        Pins the per-model opt-in on the inline preview: even a small,
        ACL-readable collection must not leak related display names for a model
        the admin never exposed via MCP. It collapses to the count + the "not
        enabled" note instead of listing the names inline.
        """
        self._disable_model("base.model_res_partner_category")
        utils.clear_mcp_caches()
        # Default cap (3) applies -> a single tag is within the inline range, so
        # only the per-model gate (not the size check) can suppress the preview.
        tag = self.env["res.partner.category"].create({"name": "SecretTagLeak"})
        partner = self.env["res.partner"].create(
            {"name": "Small-not-enabled", "category_id": [(6, 0, tag.ids)]}
        )
        result = self._call_tool(
            "get_record",
            {
                "model": "res.partner",
                "record_id": partner.id,
                "fields": ["category_id"],
            },
        )
        self.assertFalse(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertIn("category_id: 1 record(s)", text)
        # The inline preview is gated by the opt-in: the tag name must not leak.
        self.assertNotIn("Items:", text)
        self.assertNotIn("SecretTagLeak", text)
        self.assertIn("not enabled for MCP", text)

    # ------------------------------------------------------------------
    # get_fields -- field-definition discovery
    # ------------------------------------------------------------------
    def test_get_fields_returns_definitions(self):
        """get_fields describes a model's fields with the curated attributes."""
        result = self._call_tool("get_fields", {"model": "res.partner"})
        self.assertFalse(result["isError"], msg=result)

        structured = result["structuredContent"]
        self.assertEqual(structured["model"], "res.partner")
        self.assertGreater(structured["total"], 0)

        by_name = {field["name"]: field for field in structured["fields"]}
        # A known scalar field carries type/label/required/readonly.
        name_field = by_name["name"]
        self.assertEqual(name_field["type"], "char")
        self.assertIn("string", name_field)
        self.assertIn("required", name_field)
        self.assertIn("readonly", name_field)
        # A many2one carries its relation target.
        self.assertEqual(by_name["country_id"]["relation"], "res.country")
        # A selection field carries non-empty [value, label] pairs.
        selection = by_name["type"]["selection"]
        self.assertTrue(selection)
        self.assertEqual(len(selection[0]), 2)

    def test_get_fields_curated_default_attributes(self):
        """Omitting attributes returns only the compact curated attribute set."""
        result = self._call_tool("get_fields", {"model": "res.partner"})
        self.assertFalse(result["isError"], msg=result)

        allowed = {
            "name",
            "type",
            "string",
            "required",
            "readonly",
            "relation",
            "selection",
            "selection_more",
        }
        for field in result["structuredContent"]["fields"]:
            extra = set(field) - allowed
            self.assertFalse(extra, msg=f"unexpected keys: {extra}")
            # Noisy attributes are not returned by default.
            self.assertNotIn("help", field)
            self.assertNotIn("depends", field)

    def test_get_fields_default_is_curated_and_all_sentinel_expands(self):
        """Omitting field_names returns a curated subset with an expansion hint.

        ``["__all__"]`` returns the complete schema. Keeps a schema-discovery
        call affordable in tokens on wide models like res.partner.
        """
        curated = self._call_tool("get_fields", {"model": "res.partner"})
        self.assertFalse(curated["isError"], msg=curated)
        full = self._call_tool(
            "get_fields", {"model": "res.partner", "field_names": ["__all__"]}
        )
        self.assertFalse(full["isError"], msg=full)

        curated_names = {f["name"] for f in curated["structuredContent"]["fields"]}
        full_names = {f["name"] for f in full["structuredContent"]["fields"]}
        self.assertLess(len(curated_names), len(full_names))
        self.assertTrue(curated_names <= full_names)
        # Essential + structural fields survive the curation.
        for expected in ("name", "email", "country_id"):
            self.assertIn(expected, curated_names)
        # Chatter boilerplate does not.
        self.assertFalse({n for n in curated_names if n.startswith("message_")})
        # The curated text carries the expansion hint; the full one does not.
        self.assertIn("more fields not shown", curated["content"][0]["text"])
        self.assertNotIn("more fields not shown", full["content"][0]["text"])
        self.assertEqual(
            curated["structuredContent"]["omitted"],
            len(full_names) - len(curated_names),
        )
        # Giant selection lists are capped in the curated view (res.partner's
        # tz has ~500 options) and complete when the field is named explicitly.
        curated_tz = next(
            f for f in curated["structuredContent"]["fields"] if f["name"] == "tz"
        )
        self.assertLessEqual(len(curated_tz["selection"]), 20)
        self.assertGreater(curated_tz["selection_more"], 0)
        self.assertIn("more options", curated["content"][0]["text"])
        explicit_tz = self._call_tool(
            "get_fields", {"model": "res.partner", "field_names": ["tz"]}
        )
        self.assertFalse(explicit_tz["isError"], msg=explicit_tz)
        tz_field = explicit_tz["structuredContent"]["fields"][0]
        self.assertNotIn("selection_more", tz_field)
        self.assertEqual(
            len(tz_field["selection"]),
            len(curated_tz["selection"]) + curated_tz["selection_more"],
        )

    def test_get_fields_field_names_filter(self):
        """field_names restricts the result to exactly the requested fields."""
        result = self._call_tool(
            "get_fields",
            {"model": "res.partner", "field_names": ["name", "email"]},
        )
        self.assertFalse(result["isError"], msg=result)
        names = {field["name"] for field in result["structuredContent"]["fields"]}
        self.assertEqual(names, {"name", "email"})

    def test_get_fields_attributes_narrowing(self):
        """attributes narrows which attributes each field carries."""
        result = self._call_tool(
            "get_fields",
            {"model": "res.partner", "attributes": ["type"]},
        )
        self.assertFalse(result["isError"], msg=result)
        field = result["structuredContent"]["fields"][0]
        self.assertIn("name", field)
        self.assertIn("type", field)
        self.assertNotIn("string", field)
        self.assertNotIn("required", field)

        # Explicitly requesting 'help' surfaces it (on the fields that have it).
        with_help = self._call_tool(
            "get_fields",
            {"model": "res.partner", "attributes": ["type", "help"]},
        )
        self.assertFalse(with_help["isError"], msg=with_help)
        self.assertTrue(
            any("help" in field for field in with_help["structuredContent"]["fields"]),
            msg="expected 'help' on at least one field when requested explicitly",
        )

    def test_get_fields_non_enabled_model_is_denied(self):
        """get_fields on a non-MCP-enabled model errors cleanly (no traceback)."""
        result = self._call_tool("get_fields", {"model": "res.users"})
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        self.assertIn("not enabled", text.lower())
        self.assertNotIn("Traceback", text)

    # ------------------------------------------------------------------
    # Access control
    # ------------------------------------------------------------------
    def test_tools_call_non_enabled_model_is_denied(self):
        """A tool call on a non-MCP-enabled model is refused (isError, clean)."""
        result = self._call_tool("search_records", {"model": "res.users"})
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        self.assertIn("not enabled", text.lower())
        self.assertNotIn("Traceback", text)

    def test_tools_call_low_privilege_user_blocked_by_acl(self):
        """A low-priv user is blocked by Odoo ACL on an MCP-enabled model."""
        # ir.mail_server is MCP-enabled (read) but admin-only in Odoo, so the
        # ORM raises AccessError -> surfaced as an isError tool result.
        result = self._call_tool(
            "search_records",
            {"model": "ir.mail_server"},
            api_key=self.low_priv_key,
        )
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        self.assertTrue(text.strip())
        self.assertNotIn("Traceback", text)
        # The refusal must come from Odoo's own ACL, NOT the MCP gate: the model
        # is MCP-enabled for read, so neither the per-operation gate message
        # ("via MCP") nor the model-gate message ("not enabled") may appear.
        self.assertNotIn("via MCP", text)
        self.assertNotIn("not enabled", text.lower())

    # ------------------------------------------------------------------
    # Binary -> URI + resources/read materialisation
    # ------------------------------------------------------------------
    def test_binary_field_replaced_with_uri(self):
        """Populated binary/image fields become odoo:// record-field URIs."""
        expected_uri = (
            f"odoo://record/res.partner/{self.partner_with_image.id}/image_1920"
        )

        # get_record with explicit fields including the image.
        get_result = self._call_tool(
            "get_record",
            {
                "model": "res.partner",
                "record_id": self.partner_with_image.id,
                "fields": ["name", "image_1920"],
            },
        )
        self.assertFalse(get_result["isError"])
        self.assertEqual(
            get_result["structuredContent"]["record"]["image_1920"], expected_uri
        )

        # search_records likewise rewrites the binary field to a URI.
        search_result = self._call_tool(
            "search_records",
            {
                "model": "res.partner",
                "domain": [["id", "=", self.partner_with_image.id]],
                "fields": ["name", "image_1920"],
            },
        )
        self.assertFalse(search_result["isError"])
        row = search_result["structuredContent"]["records"][0]
        self.assertEqual(row["image_1920"], expected_uri)

    def test_bulk_read_skips_fields_the_user_cannot_read(self):
        """``__all__`` leaves out a field whose read is refused, and names it.

        A computed field can read a model the caller has no access to (e.g. the
        accounting totals on a contact); an explicit field list still fails.
        """
        partner = self.partner_with_image
        with self._refuse_partner_comment():
            got = self._call_tool(
                "get_record",
                {
                    "model": "res.partner",
                    "record_id": partner.id,
                    "fields": ["__all__"],
                },
            )
            found = self._call_tool(
                "search_records",
                {
                    "model": "res.partner",
                    "domain": [["id", "=", partner.id]],
                    "fields": ["__all__"],
                },
            )
            explicit = self._call_tool(
                "get_record",
                {
                    "model": "res.partner",
                    "record_id": partner.id,
                    "fields": ["comment"],
                },
            )
        for result in (got, found):
            self.assertFalse(result["isError"], msg=result)
            # Apps such as accounting add more unreadable computed fields.
            self.assertIn("comment", result["structuredContent"]["skipped_fields"])
            self.assertIn("comment", result["content"][0]["text"])
        self.assertNotIn("comment", got["structuredContent"]["record"])
        self.assertIn("name", got["structuredContent"]["record"])
        self.assertTrue(explicit["isError"], msg=explicit)

    def test_bulk_read_caches_unreadable_fields(self):
        """A repeat bulk read leaves the cached unreadable fields out without
        probing the fields again."""
        tools_cls = type(self.env["mcp.mixin"])
        arguments = {"model": "res.partner", "fields": ["__all__"]}
        calls = (
            ("get_record", {**arguments, "record_id": self.partner_with_image.id}),
            ("search_records", {**arguments, "domain": [["id", "!=", 0]]}),
        )
        with self._refuse_partner_comment():
            for tool, args in calls:
                self.assertFalse(self._call_tool(tool, args)["isError"])
            with patch.object(
                tools_cls,
                "_drop_unreadable_fields",
                side_effect=AssertionError("fields probed again"),
            ):
                for tool, args in calls:
                    with self.subTest(tool=tool):
                        result = self._call_tool(tool, args)
                        self.assertFalse(result["isError"], msg=result)
                        self.assertIn(
                            "comment", result["structuredContent"]["skipped_fields"]
                        )

    def test_bulk_read_skips_signup_url_without_user_write(self):
        """``signup_url`` is left out for a caller who cannot write users.

        Odoo 17 refuses it only for the contacts of internal users, which a
        probe of one record does not see; the bulk read must not fail.
        """
        internal = self.mcp_user.partner_id
        for tool, arguments in (
            ("get_record", {"record_id": internal.id}),
            ("search_records", {"domain": [["id", "in", [internal.id]]]}),
        ):
            with self.subTest(tool=tool):
                result = self._call_tool(
                    tool,
                    {"model": "res.partner", "fields": ["__all__"], **arguments},
                    api_key=self.low_priv_key,
                )
                self.assertFalse(result["isError"], msg=result)
                skipped = result["structuredContent"]["skipped_fields"]
                self.assertIn("signup_url", skipped)

    @contextmanager
    def _refuse_partner_comment(self):
        """Refuse every read of ``res.partner.comment``, as a computed field on
        a model the caller cannot access would; clear the cached probe after."""
        self.addCleanup(self.env.registry.clear_cache)
        partner_cls = type(self.env["res.partner"])
        orig_read, orig_search_read = partner_cls.read, partner_cls.search_read

        def read(records, fields=None, load="_classic_read"):
            if fields and "comment" in fields:
                raise AccessError(  # pylint: disable=translation-required
                    "Refused for this test"
                )
            return orig_read(records, fields, load)

        def search_read(records, domain=None, fields=None, *args, **kwargs):
            if fields and "comment" in fields:
                raise AccessError(  # pylint: disable=translation-required
                    "Refused for this test"
                )
            return orig_search_read(records, domain, fields, *args, **kwargs)

        with (
            patch.object(partner_cls, "read", read),
            patch.object(partner_cls, "search_read", search_read),
        ):
            yield

    def test_binary_field_uri_on_archived_record(self):
        """An archived record's populated binary still becomes a URI."""
        self.partner_with_image.active = False
        expected_uri = (
            f"odoo://record/res.partner/{self.partner_with_image.id}/image_1920"
        )
        get_result = self._call_tool(
            "get_record",
            {
                "model": "res.partner",
                "record_id": self.partner_with_image.id,
                "fields": ["name", "image_1920"],
            },
        )
        self.assertFalse(get_result["isError"], msg=get_result)
        self.assertEqual(
            get_result["structuredContent"]["record"]["image_1920"], expected_uri
        )

    def test_resources_read_image_returns_blob(self):
        """resources/read materialises a record image field as a base64 blob."""
        uri = f"odoo://record/res.partner/{self.partner_with_image.id}/image_1920"
        result = self._rpc_result("resources/read", {"uri": uri})

        contents = result["contents"]
        self.assertEqual(len(contents), 1)
        entry = contents[0]
        self.assertEqual(entry["uri"], uri)
        self.assertTrue(entry["mimeType"].startswith("image/"))
        # Non-textual content is returned as a base64 blob (decodable bytes).
        self.assertNotIn("text", entry)
        self.assertTrue(base64.b64decode(entry["blob"]))

    def test_resources_read_text_attachment_inline(self):
        """resources/read returns a textual ir.attachment inline as text."""
        uri = f"odoo://attachment/{self.text_attachment.id}"
        result = self._rpc_result("resources/read", {"uri": uri})

        entry = result["contents"][0]
        self.assertEqual(entry["uri"], uri)
        self.assertEqual(entry["mimeType"], "text/plain")
        self.assertEqual(entry["text"], "hello mcp")
        self.assertNotIn("blob", entry)

    # ------------------------------------------------------------------
    # resources/read -- error + forbidden paths (sanitized JSON-RPC errors)
    # ------------------------------------------------------------------
    def _rpc_error(self, method, params):
        """POST ``method`` and return the JSON-RPC ``error`` object (asserts one)."""
        response = self._post_rpc(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("error", payload, msg=payload)
        return payload["error"]

    def test_resources_read_empty_uri_is_invalid_params(self):
        """An empty/missing uri is rejected as -32602 invalid params."""
        error = self._rpc_error("resources/read", {"uri": ""})
        self.assertEqual(error["code"], -32602)

    def test_resources_read_malformed_uri_is_invalid_params(self):
        """A malformed odoo:// uri is rejected as a clean -32602 error."""
        error = self._rpc_error("resources/read", {"uri": "odoo://bogus/nope"})
        self.assertEqual(error["code"], -32602)
        self.assertNotIn("Traceback", error["message"])

    def test_resources_read_non_enabled_model_is_error(self):
        """A record uri on a non-MCP-enabled model errors cleanly (no traceback)."""
        # res.users is NOT MCP-enabled (disabled in setUp).
        uri = f"odoo://record/res.users/{self.mcp_user.id}/image_1920"
        error = self._rpc_error("resources/read", {"uri": uri})
        self.assertEqual(error["code"], -32602)
        self.assertNotIn("Traceback", error["message"])

    def test_resources_read_missing_attachment_clean_error(self):
        """A non-existent attachment id yields a clean MissingError -> -32602."""
        error = self._rpc_error(
            "resources/read", {"uri": "odoo://attachment/999999999"}
        )
        self.assertEqual(error["code"], -32602)
        self.assertNotIn("Traceback", error["message"])

    def test_resources_read_non_binary_field_is_error(self):
        """A record uri pointing at a non-binary field errors cleanly."""
        uri = f"odoo://record/res.partner/{self.partner_with_image.id}/name"
        error = self._rpc_error("resources/read", {"uri": uri})
        self.assertEqual(error["code"], -32602)
        self.assertIn("binary", error["message"].lower())
        self.assertNotIn("Traceback", error["message"])

    def test_resources_read_attachment_res_model_gating(self):
        """Attachment access is gated on the allow-list (res_model rule).

        With ir.attachment NOT MCP-enabled, an attachment whose parent model is
        also not enabled is refused (AccessError); enabling that parent model for
        read then grants the read of the same (public) attachment.
        """
        # Drop ir.attachment from the allow-list so only the res_model rule can
        # grant access; res.users stays disabled (from setUp).
        self._disable_model("base.model_ir_attachment")
        utils.clear_mcp_caches()

        attachment = (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": "gated.txt",
                    "mimetype": "text/plain",
                    "raw": b"gated payload",
                    "public": True,
                    "res_model": "res.users",
                    "res_id": self.mcp_user.id,
                }
            )
        )
        uri = f"odoo://attachment/{attachment.id}"

        # Denied: neither ir.attachment nor res.users is MCP-enabled for read.
        error = self._rpc_error("resources/read", {"uri": uri})
        self.assertEqual(error["code"], -32602)
        self.assertNotIn("Traceback", error["message"])

        # Allowed once the attachment's parent model (res.users) is enabled.
        self._enable_model("base.model_res_users", allow_read=True)
        utils.clear_mcp_caches()
        result = self._rpc_result("resources/read", {"uri": uri})
        entry = result["contents"][0]
        self.assertEqual(entry["uri"], uri)
        self.assertEqual(entry["text"], "gated payload")

    def test_resources_read_writes_audit_row(self):
        """A successful resources/read persists an mcp.log audit row."""
        uri = f"odoo://record/res.partner/{self.partner_with_image.id}/image_1920"
        result = self._rpc_result("resources/read", {"uri": uri})
        self.assertEqual(len(result["contents"]), 1)

        self.env.invalidate_all()
        rows = (
            self.env["mcp.log"]
            .sudo()
            .search(
                [
                    ("event_type", "=", "model_access"),
                    ("model_name", "=", "res.partner"),
                    ("operation", "=", "read"),
                    ("user_id", "=", self.mcp_user.id),
                ]
            )
        )
        self.assertTrue(rows, "expected an audit row for the resources/read")

    def test_resources_read_denied_model_audited_as_permission_denied(self):
        """A forbidden resources/read logs permission_denied, not a generic error.

        res.users is not MCP-enabled, so _read_resource raises AccessError. Like a
        denied tools/call, that must be audited as a permission_denied row -- not
        an E500 'error' row indistinguishable from a real internal fault. The
        client still receives the same sanitised -32602 invalid-params error.
        """
        uri = f"odoo://record/res.users/{self.mcp_user.id}/image_1920"
        error = self._rpc_error("resources/read", {"uri": uri})
        self.assertEqual(error["code"], -32602)

        self.env.invalidate_all()
        logs = self.env["mcp.log"].sudo()
        # Scoped to this test's freshly-created user id, so rows leaked by other
        # suites cannot match (mirrors test_resources_read_writes_audit_row).
        denied = logs.search(
            [
                ("event_type", "=", "permission_denied"),
                ("model_name", "=", "res.users"),
                ("operation", "=", "read"),
                ("user_id", "=", self.mcp_user.id),
            ]
        )
        self.assertEqual(
            len(denied),
            1,
            "a forbidden resources/read must log one permission_denied row",
        )
        self.assertEqual(denied.auth_method, "api_key")
        # The denial must NOT also surface as a generic internal-error row.
        err_rows = logs.search(
            [
                ("event_type", "=", "error"),
                ("model_name", "=", "res.users"),
                ("operation", "=", "read"),
                ("user_id", "=", self.mcp_user.id),
            ]
        )
        self.assertFalse(err_rows, "denial must not be logged as an E500 error row")

    def test_search_records_audit_row_summarizes_total(self):
        """A search_records audit row records the match total as response_data.

        _audit_response_summary is tool-aware: search_records stashes the page
        total under 'total' (not 'total_count'), so the model_access row's
        response_data must read 'N records' matching structuredContent['total'].
        """
        result = self._call_tool("search_records", {"model": "res.partner"})
        self.assertFalse(result["isError"], msg=result)
        total = result["structuredContent"]["total"]

        self.env.invalidate_all()
        rows = (
            self.env["mcp.log"]
            .sudo()
            .search(
                [
                    ("event_type", "=", "model_access"),
                    ("tool_name", "=", "search_records"),
                    ("model_name", "=", "res.partner"),
                    ("user_id", "=", self.mcp_user.id),
                ]
            )
        )
        self.assertEqual(len(rows), 1, "expected one search_records audit row")
        self.assertEqual(rows.response_data, "%s records" % total)

    def test_aggregate_records_audit_row_summarizes_group_count(self):
        """An aggregate_records audit row records the group count as response_data.

        aggregate_records stashes no 'total'; _audit_response_summary summarises
        it as 'N groups' from len(structuredContent['groups']).
        """
        result = self._call_tool(
            "aggregate_records",
            {"model": "res.partner", "groupby": ["is_company"]},
        )
        self.assertFalse(result["isError"], msg=result)
        group_count = len(result["structuredContent"]["groups"])

        self.env.invalidate_all()
        rows = (
            self.env["mcp.log"]
            .sudo()
            .search(
                [
                    ("event_type", "=", "model_access"),
                    ("tool_name", "=", "aggregate_records"),
                    ("model_name", "=", "res.partner"),
                    ("user_id", "=", self.mcp_user.id),
                ]
            )
        )
        self.assertEqual(len(rows), 1, "expected one aggregate_records audit row")
        self.assertEqual(rows.response_data, "%s groups" % group_count)

    # ------------------------------------------------------------------
    # aggregate_records / list_models / list_resource_templates
    # ------------------------------------------------------------------
    def test_aggregate_records_success_and_denial(self):
        """aggregate_records groups an enabled model and denies a non-enabled one."""
        result = self._call_tool(
            "aggregate_records",
            {"model": "res.partner", "groupby": ["is_company"]},
        )
        self.assertFalse(result["isError"], msg=result)
        structured = result["structuredContent"]
        self.assertEqual(structured["model"], "res.partner")
        self.assertEqual(structured["groupby"], ["is_company"])
        # Defaults to a __count aggregate, and there is at least one bucket.
        self.assertEqual(structured["aggregates"], ["__count"])
        self.assertTrue(structured["groups"])
        self.assertIn("__count", structured["groups"][0])

        denied = self._call_tool(
            "aggregate_records", {"model": "res.users", "groupby": ["active"]}
        )
        self.assertTrue(denied["isError"])
        self.assertIn("not enabled", denied["content"][0]["text"].lower())

    def test_aggregate_records_structured_drops_internal_keys(self):
        """Structured groups drop engine internals but keep requested aggregates.

        Internal ``__``-prefixed bucket keys (``__extra_domain`` /
        ``__fold``-style grouping-engine tags) must never reach the client,
        while the requested ``__count`` aggregate and the groupby field value
        are preserved.
        """
        result = self._call_tool(
            "aggregate_records",
            {"model": "res.partner", "groupby": ["is_company"]},
        )
        self.assertFalse(result["isError"], msg=result)
        groups = result["structuredContent"]["groups"]
        self.assertTrue(groups)
        for group in groups:
            self.assertNotIn("__extra_domain", group)
            self.assertNotIn("__fold", group)
            # The default requested aggregate and the groupby value survive.
            self.assertIn("__count", group)
            self.assertIn("is_company", group)

    def test_aggregate_records_signals_truncation(self):
        """A page smaller than the group count sets has_more + a next-page hint.

        res.partner has both is_company True and False, so grouping on it yields
        at least two buckets; limit=1 forces truncation.
        """
        result = self._call_tool(
            "aggregate_records",
            {"model": "res.partner", "groupby": ["is_company"], "limit": 1},
        )
        self.assertFalse(result["isError"], msg=result)
        structured = result["structuredContent"]
        self.assertEqual(len(structured["groups"]), 1)
        self.assertEqual(structured["limit"], 1)
        self.assertEqual(structured["offset"], 0)
        self.assertTrue(structured["has_more"], msg=structured)
        self.assertIn("next page", result["content"][0]["text"].lower())

    def test_aggregate_records_accepts_string_aggregates(self):
        """A bare-string ``aggregates`` is treated as one aggregate, not exploded.

        ``groupby`` already coerces a string to a one-element list; ``aggregates``
        must do the same, otherwise ``list("__count")`` fans out into characters
        and yields a confusing error instead of the requested aggregate.
        """
        result = self._call_tool(
            "aggregate_records",
            {
                "model": "res.partner",
                "groupby": "is_company",
                "aggregates": "__count",
            },
        )
        self.assertFalse(result["isError"], msg=result)
        structured = result["structuredContent"]
        self.assertEqual(structured["aggregates"], ["__count"])
        self.assertTrue(structured["groups"])
        self.assertIn("__count", structured["groups"][0])

    def test_aggregate_records_relational_buckets_and_sum(self):
        """Relational buckets arrive as [id, display_name]; field:sum as numbers.

        The tool shapes raw ``_read_group`` tuples itself
        (``_format_read_group_rows``): a many2one groupby value must reach the
        client as a JSON ``[id, display_name]`` pair -- ``false`` when unset --
        and a ``field:sum`` aggregate as a plain number.
        """
        parent = self.env["res.partner"].create({"name": "Agg Parent"})
        self.env["res.partner"].create(
            [
                {"name": "Agg Child 1", "parent_id": parent.id, "color": 2},
                {"name": "Agg Child 2", "parent_id": parent.id, "color": 3},
            ]
        )
        result = self._call_tool(
            "aggregate_records",
            {
                "model": "res.partner",
                "groupby": ["parent_id"],
                "aggregates": ["color:sum", "__count"],
                "domain": [["parent_id", "=", parent.id]],
            },
        )
        self.assertFalse(result["isError"], msg=result)
        groups = result["structuredContent"]["groups"]
        self.assertEqual(len(groups), 1)
        bucket = groups[0]
        self.assertEqual(bucket["parent_id"], [parent.id, parent.display_name])
        self.assertEqual(bucket["color:sum"], 5)
        self.assertEqual(bucket["__count"], 2)

    def test_aggregate_records_date_bucket_is_string(self):
        """A date/datetime granularity bucket is emitted in its string form.

        v19's ``formatted_read_group`` returns ``(bucket start, localized
        label)`` for time buckets; on v18 the tool intentionally emits only
        the string-coerced bucket start (JSON-serializable, stable regardless
        of babel locale data).
        """
        marker = self.env["res.partner"].create({"name": "Agg Dated"})
        result = self._call_tool(
            "aggregate_records",
            {
                "model": "res.partner",
                "groupby": ["create_date:month"],
                "domain": [["id", "=", marker.id]],
            },
        )
        self.assertFalse(result["isError"], msg=result)
        groups = result["structuredContent"]["groups"]
        self.assertEqual(len(groups), 1)
        bucket_value = groups[0]["create_date:month"]
        self.assertIsInstance(bucket_value, str)
        # Month granularity truncates to the first of the month.
        self.assertRegex(bucket_value, r"^\d{4}-\d{2}-01")
        self.assertEqual(groups[0]["__count"], 1)

    def test_aggregate_records_refuses_type_mismatch(self):
        """``sum`` over a text field is a clear tool error, not an SQL failure."""
        for spec, needed in (("name:sum", "integer"), ("name:bool_or", "boolean")):
            with self.subTest(spec=spec):
                result = self._call_tool(
                    "aggregate_records",
                    {
                        "model": "res.partner",
                        "groupby": ["is_company"],
                        "aggregates": [spec],
                    },
                )
                self.assertTrue(result["isError"], msg=result)
                text = result["content"][0]["text"]
                self.assertIn(spec, text)
                self.assertIn(needed, text)
                self.assertNotIn("Internal server error", text)

    def test_aggregate_records_numeric_sum_still_works(self):
        """The type check lets a numeric aggregate through."""
        result = self._call_tool(
            "aggregate_records",
            {
                "model": "res.partner",
                "groupby": ["is_company"],
                "aggregates": ["color:sum"],
            },
        )
        self.assertFalse(result["isError"], msg=result)
        self.assertIn("color:sum", result["structuredContent"]["groups"][0])

    def test_aggregate_records_no_truncation_no_has_more(self):
        """A page covering every group reports has_more False and no hint."""
        result = self._call_tool(
            "aggregate_records",
            {"model": "res.partner", "groupby": ["is_company"], "limit": 50},
        )
        self.assertFalse(result["isError"], msg=result)
        structured = result["structuredContent"]
        self.assertFalse(structured["has_more"], msg=structured)
        self.assertNotIn("next page", result["content"][0]["text"].lower())

    def test_refused_call_audit_row_names_the_tool(self):
        """A call refused for a model that is not enabled is audited with its tool."""
        result = self._call_tool("search_records", {"model": "ir.config_parameter"})
        self.assertTrue(result["isError"], msg=result)
        self.env.invalidate_all()
        denied = (
            self.env["mcp.log"]
            .sudo()
            .search(
                [
                    ("event_type", "=", "permission_denied"),
                    ("model_name", "=", "ir.config_parameter"),
                    ("user_id", "=", self.mcp_user.id),
                ]
            )
        )
        self.assertEqual(denied.mapped("tool_name"), ["search_records"])

    def test_aggregate_records_max_on_boolean_is_refused(self):
        """max and min on a boolean field get a clear error, not a SQL error."""
        result = self._call_tool(
            "aggregate_records",
            {
                "model": "res.partner",
                "groupby": ["is_company"],
                "aggregates": ["active:max"],
            },
        )
        self.assertTrue(result["isError"], msg=result)
        self.assertIn("bool_or", result["content"][0]["text"])

    def test_list_models_reflects_enabled_set(self):
        """list_models returns the MCP-enabled set (reflecting a setUp toggle)."""
        result = self._call_tool("list_models")
        self.assertFalse(result["isError"], msg=result)
        models = result["structuredContent"]["models"]
        names = {entry["model"] for entry in models}
        # res.partner is enabled for read in setUp; res.users is not.
        self.assertIn("res.partner", names)
        self.assertNotIn("res.users", names)
        partner = next(e for e in models if e["model"] == "res.partner")
        self.assertTrue(partner["operations"]["read"])

    def test_list_resource_templates_via_tools_call(self):
        """list_resource_templates runs via tools/call with both templates."""
        result = self._call_tool("list_resource_templates")
        self.assertFalse(result["isError"], msg=result)
        templates = result["structuredContent"]["templates"]
        uri_templates = {tpl["uri_template"] for tpl in templates}
        self.assertEqual(
            uri_templates,
            {"odoo://record/{model}/{id}/{field}", "odoo://attachment/{id}"},
        )

    # ------------------------------------------------------------------
    # get_record not-found + malformed tools/call
    # ------------------------------------------------------------------
    def test_get_record_not_found_is_clean_error(self):
        """get_record on a missing id is a clean isError (no traceback)."""
        result = self._call_tool(
            "get_record", {"model": "res.partner", "record_id": 999999999}
        )
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        self.assertIn("not found", text.lower())
        self.assertNotIn("Traceback", text)

    def test_browse_record_or_raise_missing_is_missing_error(self):
        """The shared single-record helper raises a uniform ``MissingError``.

        Every single-record tool routes through this helper, so a missing id
        raises ``MissingError`` uniformly rather than a plain ``UserError``.
        """
        with self.assertRaises(MissingError):
            self.env["mcp.mixin"]._browse_record_or_raise(
                "res.partner", self.env["res.partner"], 999999999
            )

    def _tools_call_error(self, name, arguments):
        """POST a tools/call and return its JSON-RPC ``error`` object."""
        return self._rpc_error("tools/call", {"name": name, "arguments": arguments})

    def test_tools_call_unknown_tool_is_invalid_params(self):
        """An unknown tool name yields -32602 invalid params."""
        error = self._tools_call_error("no_such_tool", {})
        self.assertEqual(error["code"], -32602)

    def test_tools_call_missing_required_arg_is_iserror(self):
        """A missing required argument is an isError tool result (SEP-1303).

        Input-validation failures on a known tool are tool execution errors
        naming the missing field -- so the model can self-correct -- not
        JSON-RPC -32602 protocol errors (2025-11-25, SEP-1303).
        """
        result = self._call_tool("get_record", {"model": "res.partner"})
        self.assertTrue(result["isError"], msg=result)
        self.assertIn("record_id", result["content"][0]["text"])

    def test_tools_call_unknown_arg_is_iserror(self):
        """An argument the schema does not declare is a clean validation error.

        Builtin schemas declare ``additionalProperties: false``; without this
        check the stray key reached ``method(**arguments)`` and the Python
        ``TypeError`` leaked the implementing class and signature.
        """
        result = self._call_tool(
            "get_record", {"model": "res.partner", "record_id": 1, "bogus": 1}
        )
        self.assertTrue(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertIn("Unknown argument(s): bogus", text)
        self.assertNotIn("unexpected keyword", text)
        self.assertNotIn("McpTools", text)

    def test_tools_call_non_object_arguments_is_iserror(self):
        """A non-object ``arguments`` payload is an isError tool result (SEP-1303)."""
        result = self._rpc_result("tools/call", {"name": "get_record", "arguments": []})
        self.assertTrue(result["isError"], msg=result)
        self.assertIn("must be an object", result["content"][0]["text"])

    def test_tools_call_wrong_type_arg_is_iserror(self):
        """A wrong-TYPE argument is a tool-execution failure -> isError result.

        Tool-internal ValueError/TypeError (here int("abc")) is surfaced as an
        MCP isError result the model can react to, not a JSON-RPC protocol error
        some clients treat as fatal. The message is sanitized (no traceback).
        """
        result = self._call_tool(
            "get_record", {"model": "res.partner", "record_id": "abc"}
        )
        self.assertTrue(result["isError"], msg=result)
        text = result["content"][0]["text"]
        self.assertTrue(text.strip())
        self.assertNotIn("Traceback", text)

    def test_tools_call_arguments_checked_against_schema(self):
        """A builtin argument breaking the advertised schema is named precisely.

        Checked before the tool runs, so a wrong type no longer reaches the
        tool as an unhandled Python error ("Internal server error").
        """
        refused = {
            "type": (
                "read_attachment",
                {"attachment_id": 1, "format": 5},
                "Invalid argument 'format': must be a string, got an integer.",
            ),
            "enum": (
                "read_attachment",
                {"attachment_id": 1, "format": "zip"},
                "Invalid argument 'format': must be one of auto, link, blob.",
            ),
            "union": (
                "search_records",
                {"model": "res.partner", "fields": 5},
                "Invalid argument 'fields': must be a list, a string or null, got "
                "an integer.",
            ),
            "items": (
                "aggregate_records",
                {"model": "res.partner", "groupby": ["is_company", 5]},
                "Invalid argument 'groupby': must be a list of strings; item 1 is "
                "an integer.",
            ),
        }
        for label, (tool, arguments, message) in refused.items():
            with self.subTest(label):
                result = self._call_tool(tool, arguments)
                self.assertTrue(result["isError"], msg=result)
                self.assertEqual(result["content"][0]["text"], message)

    def test_read_attachment_non_string_format_is_a_clean_error(self):
        """Called directly (no /mcp schema check), a non-string format is refused.

        It used to crash on ``.strip()`` with an AttributeError.
        """
        with self.assertRaises(UserError) as cm:
            self.env["mcp.mixin"].read_attachment(attachment_id=1, format=5)
        self.assertIn("Invalid format '5'", str(cm.exception))

    def test_tools_call_keeps_argument_tolerances(self):
        """The shapes the builtin tools coerce themselves still pass.

        A JSON-string ``fields`` / ``domain``, digit-string ids and page
        bounds, ``null`` for an optional argument and an enum value in another
        case all reach the tool.
        """
        partner = self.env["res.partner"].search([], limit=1)
        attachment = (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": "tolerance.txt",
                    "raw": b"hi",
                    "res_model": "res.partner",
                    "res_id": partner.id,
                }
            )
        )
        calls = (
            (
                "search_records",
                {
                    "model": "res.partner",
                    "domain": '[["id", "=", %d]]' % partner.id,
                    "fields": '["name"]',
                    "limit": "5",
                    "offset": None,
                    "order": None,
                },
            ),
            ("get_record", {"model": "res.partner", "record_id": str(partner.id)}),
            (
                "aggregate_records",
                {"model": "res.partner", "groupby": "is_company", "offset": "0"},
            ),
            (
                "read_attachment",
                {"attachment_id": attachment.id, "format": " LINK "},
            ),
        )
        for tool, arguments in calls:
            with self.subTest(tool):
                result = self._call_tool(tool, arguments)
                self.assertFalse(result["isError"], msg=result)

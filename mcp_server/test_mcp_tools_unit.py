"""Unit tests for the MCP tools helper layer and gate primitives.

TransactionCase coverage the HttpCase suites only reach indirectly:

* ``tools/smart_fields`` field-importance scoring and the sensitive-name
  blocklist (the LLM-data-leak guard) -- pairs with the get_record /
  search_records sensitive-field stripping.
* ``tools/formatters`` value rendering (numeric, datetime, selection, boolean,
  truncation) and ``tools/uri_schema`` build/parse round-trips + error paths.
* ``call_model_method``'s ``_validate_method_call`` boundary for the
  self-elevating action/cron models plus the ``run`` / ``method_direct_trigger``
  method-name backstop.
"""

from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests import common, tagged

from ..controllers import utils
from ..models import mcp_tools_read
from ..tools import smart_fields
from ..tools.formatters import DatasetFormatter, RecordFormatter
from ..tools.uri_schema import (
    URIParseError,
    build_attachment_uri,
    build_field_uri,
    parse_attachment_uri,
    parse_field_uri,
)


@tagged("much_unit", "post_install", "-at_install")
class TestSmartFields(common.TransactionCase):
    """Field-importance scoring + sensitive-name blocklist (LLM leak guard)."""

    def test_sensitive_named_fields_score_zero(self):
        """A credential-named field scores 0 and is flagged sensitive."""
        for name in (
            "password",
            "user_password",
            "api_key",
            "openai_api_key",
            "webhook_secret",
            "client_secret",
            "access_token",
            "refresh_token",
            "private_key",
            "auth_token",
            "session_token",
            "secret_key",
            "stripe_secret_key",
            "signup_url",
        ):
            self.assertEqual(
                smart_fields.score_field_importance(name, {"type": "char"}), 0, name
            )
            self.assertTrue(smart_fields.is_sensitive_field_name(name), name)

    def test_benign_lookalike_fields_not_sensitive(self):
        """Names that merely contain a marker substring are NOT flagged.

        The blocklist matches whole ``_``-delimited segments, so a relational
        reference (``token_id``), a boolean flag (``is_secret``), an unrelated
        word (``secretary_id``, ``sort_key``) and a metadata descriptor that
        pushes the marker off the final segment (``password_expiry_date``,
        ``access_token_expiry``) are honored rather than silently stripped.
        """
        for name in (
            "secretary_id",
            "secretariat_id",
            "is_secret",
            "token_id",
            "sort_key",
            "password_expiry_date",
            "access_token_expiry",
        ):
            self.assertFalse(smart_fields.is_sensitive_field_name(name), name)

    def test_sensitive_fields_excluded_from_smart_defaults(self):
        """A sensitive-named field never enters the smart default selection."""
        fields_info = {
            "id": {"type": "integer"},
            "name": {"type": "char"},
            "openai_api_key": {"type": "char", "store": True},
        }
        selected = smart_fields.get_smart_default_fields(fields_info)
        self.assertIn("name", selected)
        self.assertNotIn("openai_api_key", selected)

    def test_large_and_x2many_field_types_excluded(self):
        """binary/image/html and one2many/many2many types score 0."""
        for ftype in ("binary", "image", "html", "one2many", "many2many"):
            self.assertEqual(
                smart_fields.score_field_importance("some_field", {"type": ftype}),
                0,
                ftype,
            )

    def test_non_stored_field_score_capped(self):
        """A non-stored (computed/related) field is capped at 30."""
        score = smart_fields.score_field_importance(
            "amount_total", {"type": "float", "store": False, "required": True}
        )
        self.assertLessEqual(score, 30)

    def test_essential_field_scores_top(self):
        """An essential field always scores highest."""
        self.assertEqual(
            smart_fields.score_field_importance("display_name", {"type": "char"}), 1000
        )

    def test_strip_sensitive_fields_enforced_on_read_result(self):
        """_strip_sensitive_fields drops credential-named keys.

        This is the guard get_record / search_records apply on the *bulk* paths
        -- smart defaults and the ``["__all__"]`` sentinel, never an explicit
        field list -- so a credential-named field is not surfaced by a caller
        that did not ask for it by name.
        """
        strip = self.env["mcp.mixin"]._strip_sensitive_fields
        record = {
            "id": 1,
            "name": "ACME",
            "api_key": "sk-should-not-leak",
            "webhook_secret": "shh",
        }
        strip(record)
        self.assertEqual(set(record), {"id", "name"})

    def test_get_record_strips_bulk_but_honors_explicit_fields(self):
        """get_record drops a sensitive-named field on ``__all__`` but not when named.

        ``is_sensitive_field_name`` is patched to flag the benign ``comment``
        field so the branch is observable on a real model: the ``["__all__"]``
        read omits it (bulk guard), while an explicit ``["name", "comment"]``
        request returns it (Odoo field-level ``groups=`` is the real ACL there).
        """
        partner_model = self.env.ref("base.model_res_partner")
        enabled = self.env["mcp.enabled.model"].search(
            [("model_id", "=", partner_model.id)], limit=1
        )
        if enabled:
            enabled.write({"allow_read": True, "active": True})
        else:
            self.env["mcp.enabled.model"].create(
                {"model_id": partner_model.id, "allow_read": True}
            )
        partner = self.env["res.partner"].create(
            {"name": "Sensitive Co", "comment": "confidential"}
        )
        mixin = self.env["mcp.mixin"]

        # get_record's model gate calls the global is_mcp_enabled(), which reads
        # the HTTP ``request`` -- unbound in a TransactionCase. Patch it True so
        # the gate resolves via ``env`` (the enabled-model row set up above); the
        # per-model allow_read flag is still enforced.
        with patch(
            "odoo.addons.mcp_server.controllers.utils.is_mcp_enabled",
            return_value=True,
        ), patch.object(
            mcp_tools_read, "is_sensitive_field_name", lambda name: name == "comment"
        ):
            explicit = mixin.get_record("res.partner", partner.id, ["name", "comment"])
            bulk = mixin.get_record("res.partner", partner.id, ["__all__"])

        self.assertIn("comment", explicit["structuredContent"]["record"])
        self.assertNotIn("comment", bulk["structuredContent"]["record"])


@tagged("much_unit", "post_install", "-at_install")
class TestRecordFormatter(common.TransactionCase):
    """RecordFormatter value rendering."""

    def setUp(self):
        super().setUp()
        self.fmt = RecordFormatter("res.partner")

    def test_integer_thousands_separator(self):
        self.assertEqual(
            self.fmt._format_field_value("x", 1234567, {"type": "integer"}),
            "1,234,567",
        )

    def test_float_precision_from_digits(self):
        self.assertEqual(
            self.fmt._format_field_value(
                "x", 1234.5, {"type": "float", "digits": (16, 3)}
            ),
            "1,234.500",
        )

    def test_monetary_two_decimals(self):
        self.assertEqual(
            self.fmt._format_field_value("x", 1234.5, {"type": "monetary"}),
            "1,234.50",
        )

    def test_datetime_iso8601_utc_offset(self):
        self.assertEqual(
            self.fmt._format_field_value(
                "x", "2024-01-15 13:45:30", {"type": "datetime"}
            ),
            "2024-01-15T13:45:30+00:00",
        )

    def test_selection_label_and_key(self):
        meta = {
            "type": "selection",
            "selection": [("draft", "Draft"), ("done", "Done")],
        }
        self.assertEqual(
            self.fmt._format_field_value("state", "draft", meta), "Draft (draft)"
        )

    def test_boolean_yes_no(self):
        self.assertEqual(
            self.fmt._format_field_value("b", True, {"type": "boolean"}), "Yes"
        )
        self.assertEqual(
            self.fmt._format_field_value("b", False, {"type": "boolean"}), "No"
        )

    def test_empty_char_reads_not_set(self):
        """An empty char field reads 'Not set', not 'No' (driven by type)."""
        self.assertEqual(
            self.fmt._format_field_value("ref", False, {"type": "char"}), "Not set"
        )

    def test_long_value_truncated_with_marker(self):
        """The marker names the get_record call that returns the whole value."""
        rendered = self.fmt._format_field_value(
            "note", "a" * 2500, {"type": "text"}, record_id=42
        )
        self.assertTrue(rendered.startswith("a" * 2000 + "... [truncated, 2500"))
        self.assertIn(
            "for the full value use the get_record tool with model='res.partner', "
            "record_id=42, fields=['note']]",
            rendered,
        )
        self.assertLess(len(rendered), 2500)

    def test_custom_max_field_length(self):
        """A custom cap cuts at that length; a value of exactly the cap is kept."""
        fmt = RecordFormatter("res.partner", max_field_length=500)
        rendered = fmt._format_field_value("note", "b" * 501, {"type": "text"}, 7)
        self.assertTrue(rendered.startswith("b" * 500 + "... [truncated, 501"))
        self.assertEqual(
            fmt._format_field_value("note", "b" * 500, {"type": "text"}, 7), "b" * 500
        )

    def test_ceiling_marker_without_hint(self):
        """full_value_hint=False names the per-field limit, not a get_record call."""
        fmt = RecordFormatter(
            "res.partner", max_field_length=100_000, full_value_hint=False
        )
        rendered = fmt._format_field_value("note", "c" * 100_001, {"type": "text"}, 7)
        self.assertIn(
            "[truncated, 100001 chars total; 100,000 characters is the text limit "
            "for one field, the full value is in structuredContent]",
            rendered,
        )
        self.assertNotIn("get_record", rendered)

    def test_marker_past_full_value_limit(self):
        """A value longer than the hinted call shows is not promised in full."""
        fmt = RecordFormatter("res.partner", max_field_length=50, full_value_limit=100)
        rendered = fmt._format_field_value("note", "g" * 150, {"type": "text"}, 3)
        self.assertIn(
            "[truncated, 150 chars total; the get_record tool with "
            "model='res.partner', record_id=3, fields=['note'] shows the first 100 "
            "characters, the full value is in structuredContent]",
            rendered,
        )
        self.assertNotIn("for the full value", rendered)
        # Within the limit the plain hint stays.
        self.assertIn(
            "for the full value use the get_record tool",
            fmt._format_field_value("note", "g" * 90, {"type": "text"}, 3),
        )

    def test_marker_without_record_id(self):
        """Without a record id the hint still names the model and the field."""
        rendered = self.fmt._format_field_value("note", "d" * 2001, {"type": "text"})
        self.assertIn(
            "use the get_record tool with model='res.partner', fields=['note']]",
            rendered,
        )
        self.assertNotIn("record_id=", rendered)

    def test_fallback_type_long_value_gets_hint(self):
        """The fallback branch (here a json field) cuts with the same hint."""
        rendered = self.fmt._format_field_value(
            "data", {"k": "e" * 3000}, {"type": "json"}, record_id=9
        )
        self.assertIn("... [truncated, ", rendered)
        self.assertIn("record_id=9, fields=['data']]", rendered)

    def test_format_record_passes_record_id_to_marker(self):
        """format_record feeds the record's own id into the marker."""
        text = self.fmt.format_record(
            {"id": 5, "name": "ACME", "comment": "f" * 2500},
            {"comment": {"type": "text"}},
        )
        self.assertIn("record_id=5, fields=['comment']]", text)


@tagged("much_unit", "post_install", "-at_install")
class TestDatasetFormatter(common.TransactionCase):
    """DatasetFormatter search-result rendering."""

    def test_search_results_header_and_pagination(self):
        fmt = DatasetFormatter("res.partner")
        rows = [{"id": 1, "display_name": "ACME"}, {"id": 2, "display_name": "Globex"}]
        text = fmt.format_search_results(
            rows, total_count=5, offset=0, limit=2, current_page=1, total_pages=3
        )
        self.assertIn("Search Results: res.partner", text)
        self.assertIn("Page 1 of 3", text)
        self.assertIn("Showing records 1-2 of 5", text)

    def test_search_results_skip_empty_fields_keep_boolean_false(self):
        """The list view spends no line on empty values; boolean False stays."""
        fmt = DatasetFormatter("res.partner")
        rows = [
            {
                "id": 1,
                "display_name": "ACME",
                "email": "a@ex.com",
                "phone": False,
                "city": "",
                "category_id": [],
                "is_company": False,
            }
        ]
        meta = {
            "email": {"type": "char"},
            "phone": {"type": "char"},
            "city": {"type": "char"},
            "category_id": {"type": "many2many"},
            "is_company": {"type": "boolean"},
        }
        text = fmt.format_search_results(
            rows,
            fields=["email", "phone", "city", "category_id", "is_company"],
            fields_metadata=meta,
        )
        self.assertIn("email: a@ex.com", text)
        self.assertIn("is_company: No", text)
        for absent in ("phone:", "city:", "category_id:", "Not set"):
            self.assertNotIn(absent, text)

    def test_search_results_inline_text_is_bounded(self):
        """Inline field values stop at MAX_INLINE_TEXT_CHARS; rows stay listed."""
        fmt = DatasetFormatter("res.partner")
        # 300 rows x 4 fields x 1,000 chars = 1.2 M chars of values, well past
        # the 200k budget; each value stays below the per-field 2,000 cap.
        fields = ["f1", "f2", "f3", "f4"]
        rows = [
            {"id": i, "display_name": f"Row {i}", **{f: "x" * 1000 for f in fields}}
            for i in range(1, 301)
        ]
        meta = {f: {"type": "char"} for f in fields}
        text = fmt.format_search_results(rows, fields=fields, fields_metadata=meta)
        self.assertLess(len(text), fmt.MAX_INLINE_TEXT_CHARS + 20_000)
        self.assertIn("[300] Row 300", text)  # every row still listed
        self.assertIn("f1: " + "x" * 1000, text)  # first rows inline
        self.assertIn("from record [", text)
        self.assertIn("fewer 'fields' or a smaller 'limit'", text)
        # A page within budget carries no notice.
        small = fmt.format_search_results(rows[:5], fields=fields, fields_metadata=meta)
        self.assertNotIn("from record [", small)

    def test_search_results_budget_checked_per_field(self):
        """Long fields at a high cap stop mid-row, one value past the budget."""
        fmt = DatasetFormatter("res.partner", max_field_length=100_000)
        fields = ["f1", "f2", "f3", "f4"]
        rows = [
            {"id": i, "display_name": f"Row {i}", **{f: "x" * 90_000 for f in fields}}
            for i in (1, 2)
        ]
        meta = {f: {"type": "text"} for f in fields}
        text = fmt.format_search_results(rows, fields=fields, fields_metadata=meta)
        # Three values (270k) pass the 200k budget; the fourth is not shown.
        self.assertLess(len(text), fmt.MAX_INLINE_TEXT_CHARS + 100_000)
        self.assertIn("f3: ", text)
        self.assertNotIn("f4: ", text)
        self.assertIn("[2] Row 2", text)
        self.assertIn(
            "from record [1] on, starting at field 'f4', field values are not shown",
            text,
        )

    def test_search_results_no_budget_note_when_nothing_is_left_out(self):
        """Crossing the budget on the last shown value adds no note."""
        fmt = DatasetFormatter("res.partner", max_field_length=100_000)
        rows = [
            {"id": i, "display_name": f"Row {i}", "f1": "x" * 90_000, "f2": False}
            for i in (1, 2, 3)
        ]
        meta = {"f1": {"type": "text"}, "f2": {"type": "text"}}
        text = fmt.format_search_results(
            rows, fields=["f1", "f2"], fields_metadata=meta
        )
        # Row 3's f1 takes the page past 200k; only empty f2 values follow.
        self.assertEqual(text.count("f1: "), 3)
        self.assertNotIn("from record [", text)

    def test_search_results_marker_names_each_row(self):
        """A cut value in a result row points at get_record for that row."""
        fmt = DatasetFormatter("res.partner", max_field_length=100)
        rows = [{"id": 31, "display_name": "Row", "comment": "y" * 150}]
        text = fmt.format_search_results(
            rows, fields=["comment"], fields_metadata={"comment": {"type": "text"}}
        )
        self.assertIn(
            "comment: " + "y" * 100 + "... [truncated, 150 chars total; for the "
            "full value use the get_record tool with model='res.partner', "
            "record_id=31, fields=['comment']]",
            text,
        )


@tagged("much_unit", "post_install", "-at_install")
class TestTextFieldLength(common.TransactionCase):
    """The configured text cap, and full text for fields named in get_record."""

    HINT = "for the full value use the get_record tool with model='res.partner'"
    CEILING = "100,000 characters is the text limit for one field"

    def setUp(self):
        super().setUp()
        partner_model = self.env.ref("base.model_res_partner")
        enabled = self.env["mcp.enabled.model"].search(
            [("model_id", "=", partner_model.id)], limit=1
        )
        if enabled:
            enabled.write({"allow_read": True, "active": True})
        else:
            self.env["mcp.enabled.model"].create(
                {"model_id": partner_model.id, "allow_read": True}
            )
        self.params = self.env["ir.config_parameter"].sudo()
        self.params.set_param("mcp_server.max_field_length", "")
        self.mixin = self.env["mcp.mixin"]
        # The model gate calls the global is_mcp_enabled(), which reads the
        # HTTP ``request`` (see test_get_record_strips_bulk_but_honors_explicit_fields).
        patcher = patch(
            "odoo.addons.mcp_server.controllers.utils.is_mcp_enabled",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _partner(self, length):
        """A partner whose html ``comment`` holds ``length`` letters; returns
        the partner and the stored (sanitized) value."""
        partner = self.env["res.partner"].create(
            {"name": "Long Text Co", "comment": "word " * (length // 5)}
        )
        return partner, str(partner.comment)

    def _search(self, partner):
        return self.mixin.search_records(
            "res.partner", [["id", "=", partner.id]], ["comment"]
        )

    def test_max_field_length_fallback_and_clamp(self):
        """Unset, malformed and non-positive values fall back; large ones clamp."""
        for value, expected in (
            ("", 2000),
            ("0", 2000),
            ("-5", 2000),
            ("abc", 2000),
            ("700", 700),
            ("500000", 100_000),
        ):
            self.params.set_param("mcp_server.max_field_length", value)
            self.assertEqual(self.mixin._max_field_length(), expected, value)

    def test_search_records_named_field_uses_configured_cap(self):
        """search_records cuts a named field at the cap and points at get_record."""
        self.params.set_param("mcp_server.max_field_length", "500")
        partner, stored = self._partner(5000)
        result = self._search(partner)
        text = result["content"][0]["text"]
        self.assertIn(
            f"comment: {stored[:500]}... [truncated, {len(stored)} chars total; "
            f"{self.HINT}, record_id={partner.id}, fields=['comment']]",
            text,
        )
        self.assertEqual(result["structuredContent"]["records"][0]["comment"], stored)

    def test_search_records_value_past_ceiling_hints_partial_read(self):
        """With the default cap, a value past 100,000 characters is not
        promised in full by the marker."""
        partner, stored = self._partner(100_500)
        text = self._search(partner)["content"][0]["text"]
        self.assertIn(
            f"comment: {stored[:2000]}... [truncated, {len(stored)} chars total; "
            f"the get_record tool with model='res.partner', record_id={partner.id}, "
            "fields=['comment'] shows the first 100,000 characters, the full value "
            "is in structuredContent]",
            text,
        )

    def test_search_records_cap_at_ceiling_drops_hint(self):
        """A cap clamped to the ceiling gives the ceiling marker, no hint."""
        self.params.set_param("mcp_server.max_field_length", "500000")
        partner, stored = self._partner(100_500)
        text = self._search(partner)["content"][0]["text"]
        self.assertIn(f"[truncated, {len(stored)} chars total; {self.CEILING}", text)
        self.assertNotIn(self.HINT, text)

    def test_get_record_named_field_full_text(self):
        """A field named in ``fields`` comes back whole in the text block."""
        partner, stored = self._partner(5000)
        result = self.mixin.get_record("res.partner", partner.id, ["name", "comment"])
        text = result["content"][0]["text"]
        self.assertIn(stored, text)
        self.assertNotIn("truncated", text)
        self.assertEqual(result["structuredContent"]["record"]["comment"], stored)

    def test_get_record_all_fields_capped_with_hint(self):
        """``["__all__"]`` is a bulk read: the cap applies, the marker hints."""
        partner, stored = self._partner(5000)
        result = self.mixin.get_record("res.partner", partner.id, ["__all__"])
        text = result["content"][0]["text"]
        self.assertIn(
            f"comment: {stored[:2000]}... [truncated, {len(stored)} chars total; "
            f"{self.HINT}, record_id={partner.id}, fields=['comment']]",
            text,
        )
        self.assertEqual(result["structuredContent"]["record"]["comment"], stored)

    def test_get_record_named_field_over_ceiling(self):
        """A named field past the ceiling is cut there, with no hint."""
        partner, stored = self._partner(100_500)
        result = self.mixin.get_record("res.partner", partner.id, ["comment"])
        text = result["content"][0]["text"]
        self.assertIn(
            f"comment: {stored[:100_000]}... [truncated, {len(stored)} chars total; "
            f"{self.CEILING}",
            text,
        )
        self.assertNotIn(self.HINT, text)
        self.assertEqual(result["structuredContent"]["record"]["comment"], stored)

    def test_clients_are_told_how_to_read_full_text(self):
        """The initialize guidance and the get_record description explain it."""
        self.assertIn(
            "To read up to 100,000 characters of one, call get_record with that "
            "field named in 'fields'.",
            utils._usage_guidance(),
        )
        description = self.mixin._get_mcp_tools()["get_record"]["description"]
        self.assertIn(
            "Name the field in 'fields' (not [\"__all__\"]) to get up to 100,000 "
            "characters of it.",
            description,
        )


@tagged("much_unit", "post_install", "-at_install")
class TestUriSchema(common.TransactionCase):
    """odoo:// URI build/parse round-trips and error paths."""

    def test_field_uri_round_trip(self):
        uri = build_field_uri("res.partner", 10, "image_1920")
        self.assertEqual(uri, "odoo://record/res.partner/10/image_1920")
        parsed = parse_field_uri(uri)
        self.assertEqual(parsed.model, "res.partner")
        self.assertEqual(parsed.record_id, 10)
        self.assertEqual(parsed.field, "image_1920")

    def test_attachment_uri_round_trip(self):
        self.assertEqual(build_attachment_uri(42), "odoo://attachment/42")
        self.assertEqual(parse_attachment_uri("odoo://attachment/42"), 42)

    def test_parse_field_uri_rejects_garbage(self):
        with self.assertRaises(URIParseError):
            parse_field_uri("not-a-uri")

    def test_parse_attachment_uri_rejects_non_numeric(self):
        with self.assertRaises(URIParseError):
            parse_attachment_uri("odoo://attachment/abc")

    def test_build_field_uri_rejects_invalid_model(self):
        with self.assertRaises(ValueError):
            build_field_uri("1badmodel", 10, "image_1920")

    def test_build_field_uri_rejects_empty_field(self):
        with self.assertRaises(ValueError):
            build_field_uri("res.partner", 10, "")


@tagged("much_unit", "post_install", "-at_install")
class TestValidateMethodCallGuard(common.TransactionCase):
    """call_model_method self-escalation guard (_validate_method_call)."""

    def setUp(self):
        super().setUp()
        self.mixin = self.env["mcp.mixin"]

    def test_action_server_run_blocked(self):
        """ir.actions.server.run (self-sudo code exec) is refused."""
        with self.assertRaises(AccessError):
            self.mixin._validate_method_call(
                "ir.actions.server", self.env["ir.actions.server"], "run"
            )

    def test_cron_method_direct_trigger_blocked(self):
        """ir.cron.method_direct_trigger (runs a job as its owner) is refused."""
        with self.assertRaises(AccessError):
            self.mixin._validate_method_call(
                "ir.cron", self.env["ir.cron"], "method_direct_trigger"
            )

    def test_actions_namespace_blocked_wholesale(self):
        """The whole ir.actions.* namespace is refused, not just the server model."""
        with self.assertRaises(AccessError):
            self.mixin._validate_method_call(
                "ir.actions.act_window",
                self.env["ir.actions.act_window"],
                "some_business_method",
            )

    def test_run_method_name_blocked_on_any_model(self):
        """'run' is refused even on a non-action model (method-name backstop)."""
        with self.assertRaises(AccessError):
            self.mixin._validate_method_call(
                "res.partner", self.env["res.partner"], "run"
            )

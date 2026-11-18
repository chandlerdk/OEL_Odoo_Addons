"""Tests for client-facing error sanitization.

Two layers: a ``TransactionCase`` exercising ``error_sanitizer`` directly, and
an ``HttpCase`` driving a real ``tools/call`` whose (monkeypatched) tool raises a
non-safe ``RuntimeError`` -- the client must receive only the generic message,
never a leaked traceback.
"""

import json
import time
from unittest.mock import patch

import psycopg2
import psycopg2.errorcodes

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import common, tagged
from odoo.tools import mute_logger

from ..controllers import error_sanitizer, rate_limiting, utils
from ..models.mcp_tools_read import McpToolsRead
from .test_helpers import create_test_user, grant_mcp_access


@tagged("much_unit", "post_install", "-at_install")
class TestErrorSanitizerUnit(common.TransactionCase):
    """Unit tests for the ``error_sanitizer`` chokepoint (no HTTP)."""

    def test_safe_exception_message_passes_through(self):
        """A SAFE Odoo exception surfaces its own (clean) message verbatim."""
        for exc in (
            UserError("Bad input from user"),
            AccessError("Operation 'write' is not allowed via MCP."),
            ValidationError("Field X is required"),
        ):
            self.assertEqual(error_sanitizer.sanitize_exception(exc), exc.args[0])

    @mute_logger("odoo.addons.mcp_server.controllers.error_sanitizer")
    def test_non_safe_exception_is_genericized(self):
        """A non-safe exception collapses to exactly the generic message."""
        result = error_sanitizer.sanitize_exception(
            RuntimeError("boom secret internal detail")
        )
        self.assertEqual(result, error_sanitizer.GENERIC_ERROR_MESSAGE)
        self.assertNotIn("boom", result)
        self.assertNotIn("secret", result)

    def test_sanitize_message_strips_traceback_internals(self):
        """A traceback-shaped string is reduced and scrubbed of all internals."""
        raw = (
            "Traceback (most recent call last):\n"
            '  File "/opt/odoo/addons/mcp_server/foo.py", line 123, in bar\n'
            "    cr.execute(sql)\n"
            '  File "/usr/lib/python3/dist-packages/psycopg2/__init__.py", '
            "line 9, in execute\n"
            "psycopg2.errors.UniqueViolation: duplicate key value violates "
            "unique constraint at 0x7fae12 <class 'psycopg2.errors.UniqueViolation'>"
        )
        cleaned = error_sanitizer.sanitize_message(raw)

        self.assertNotIn("Traceback", cleaned)
        self.assertNotIn('File "', cleaned)
        self.assertNotIn(".py", cleaned)
        self.assertNotIn("line 123", cleaned)
        self.assertNotIn("psycopg2", cleaned)
        self.assertNotIn("0x", cleaned)
        self.assertNotIn("<class", cleaned)
        # The user-relevant tail survives so the message is not empty.
        self.assertTrue(cleaned.strip())

    def test_sanitize_message_empty_returns_generic(self):
        """An empty/blank message degrades to the generic message."""
        self.assertEqual(
            error_sanitizer.sanitize_message(""), error_sanitizer.GENERIC_ERROR_MESSAGE
        )

    def test_sanitize_message_strips_pg_detail_without_traceback(self):
        """A bare Postgres DETAIL/HINT line (no traceback) is scrubbed.

        A SAFE exception wrapping a unique/FK violation can carry the driver's
        DETAIL line verbatim -- leaking a column name and another record's value
        -- with no ``Traceback`` marker to trigger ``_reduce_traceback``. The
        DETAIL/HINT/CONTEXT/LINE lines must be stripped unconditionally.
        """
        raw = (
            "duplicate key value violates unique constraint\n"
            "DETAIL:  Key (email)=(victim@example.com) already exists.\n"
            "HINT: try another value"
        )
        cleaned = error_sanitizer.sanitize_message(raw)
        self.assertNotIn("victim@example.com", cleaned)
        self.assertNotIn("DETAIL", cleaned)
        self.assertNotIn("HINT", cleaned)
        self.assertNotIn("email", cleaned)
        # The generic leading line still survives so the message is not empty.
        self.assertIn("duplicate key value", cleaned)

    @mute_logger("odoo.addons.mcp_server.controllers.error_sanitizer")
    def test_sanitize_integrity_error_raw_driver_fallback_is_genericized(self):
        """Odoo's last-resort raw driver text collapses to the generic message.

        A bare ``IntegrityError`` carries no ``diag.table_name`` and no
        constraint name, so it is neither a NOT NULL / FK violation nor a
        registered ``_sql_constraints`` entry: core would only echo the raw
        driver text, which the sanitizer replaces.
        """
        exc = psycopg2.IntegrityError("new row violates check constraint x_chk")
        self.assertIsNone(exc.diag.table_name)
        result = error_sanitizer.sanitize_integrity_error(self.env, exc)
        self.assertEqual(result, error_sanitizer.GENERIC_ERROR_MESSAGE)
        self.assertNotIn("x_chk", result)

    @mute_logger("odoo.addons.mcp_server.controllers.error_sanitizer")
    def test_sanitize_integrity_error_raw_unique_fallback_is_named(self):
        """An unattributable unique violation gets the fixed unique message."""
        exc = psycopg2.errors.UniqueViolation(
            'duplicate key value violates unique constraint "t_uniq"\n'
            "DETAIL:  Key (a, b)=(1, 2) already exists."
        )
        result = error_sanitizer.sanitize_integrity_error(self.env, exc)
        self.assertEqual(result, error_sanitizer.UNIQUE_ERROR_MESSAGE)

    @mute_logger("odoo.addons.mcp_server.controllers.error_sanitizer")
    def test_sanitize_integrity_error_translated_message_is_scrubbed(self):
        """A field-naming message passes through, minus the Detail line."""
        # A NOT NULL violation is one core translates; the translator itself
        # is pinned so the scrubbing is what is under test.
        exc = psycopg2.errors.NotNullViolation("null value in column")
        with patch.object(
            error_sanitizer,
            "_as_validation_error",
            return_value=ValidationError(
                "The value for 'email' (Email) already exists.\n\n"
                "Detail: Key (email)=(victim@example.com) already exists.\n"
            ),
        ):
            result = error_sanitizer.sanitize_integrity_error(self.env, exc)
        self.assertIn("already exists", result)
        self.assertNotIn("victim@example.com", result)
        self.assertNotIn("Detail", result)

    @mute_logger("odoo.addons.mcp_server.controllers.error_sanitizer")
    def test_sanitize_integrity_error_delete_fk_keeps_core_wording(self):
        """A DELETE-time FK violation keeps Odoo's delete-oriented message.

        Only the INSERT / UPDATE case gets the module's own wording; a delete
        blocked by a referencing record is exactly what core's text describes.
        """

        class ForeignKeyViolation(psycopg2.errors.ForeignKeyViolation):
            # Odoo 17 core matches on the pgcode, which a hand-made error lacks.
            pgcode = psycopg2.errorcodes.FOREIGN_KEY_VIOLATION

        exc = ForeignKeyViolation(
            'update or delete on table "res_partner" violates foreign key '
            'constraint "x_fkey" on table "y"'
        )
        result = error_sanitizer.sanitize_integrity_error(self.env, exc)
        self.assertIn("record being deleted", result)

    @mute_logger("odoo.addons.mcp_server.controllers.error_sanitizer")
    def test_sanitize_integrity_error_translation_failure_is_genericized(self):
        """A crash while translating never propagates; the generic text wins."""
        exc = psycopg2.errors.NotNullViolation("null value in column")
        with patch.object(
            error_sanitizer,
            "_as_validation_error",
            side_effect=RuntimeError("translator broke"),
        ):
            result = error_sanitizer.sanitize_integrity_error(self.env, exc)
        self.assertEqual(result, error_sanitizer.GENERIC_ERROR_MESSAGE)


@tagged("much_unit", "post_install", "-at_install")
class TestErrorSanitizerEndpoint(common.HttpCase):
    """A non-safe tool error surfaces as the generic message over ``/mcp``."""

    def setUp(self):
        super().setUp()
        utils.clear_mcp_caches()
        rate_limiting._api_limiter.clear()

        unique_id = str(int(time.time() * 1000))[-6:]
        self.mcp_user = create_test_user(
            self.env,
            "MCP Sanitizer User",
            f"mcp_sanitize_user_{unique_id}",
            email=f"mcp_sanitize_{unique_id}@example.com",
        )
        grant_mcp_access(self.mcp_user)
        self.api_key = self.env(user=self.mcp_user)["res.users.apikeys"]._generate(
            "rpc", "Sanitizer Key"
        )

        self.env["ir.config_parameter"].sudo().set_param("mcp_server.enabled", "True")
        utils.clear_mcp_caches()

    def _post_rpc(self, body):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        return self.url_open("/mcp", data=json.dumps(body), headers=headers)

    def _call_tool(self, name, arguments=None):
        response = self._post_rpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("error", payload, msg=payload.get("error"))
        return payload["result"]

    @mute_logger(
        "odoo.addons.mcp_server.controllers.error_sanitizer",
        "odoo.addons.mcp_server.controllers.mcp",
    )
    def test_non_safe_tool_error_returns_generic_text(self):
        """A tool raising a non-safe RuntimeError yields the generic isError text.

        ``list_models`` is monkeypatched to raise a ``RuntimeError`` whose message
        embeds traceback-shaped internals; the client must receive only the single
        generic message, never the leaked detail.
        """
        # Prime the (registry-cached) tool index BEFORE patching, so swapping
        # list_models for a function without the @mcp_tool tag does not drop it
        # from discovery -- the dispatcher still finds its metadata and invokes
        # the (now raising) method.
        self.assertIn("list_models", self.env["mcp.mixin"]._get_mcp_tools())

        def boom(self, *args, **kwargs):
            raise RuntimeError(
                'leaky File "/opt/odoo/secret.py", line 7 '
                "psycopg2.OperationalError at 0x7f00"
            )

        with patch.object(McpToolsRead, "list_models", boom):
            result = self._call_tool("list_models")

        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        self.assertEqual(text, error_sanitizer.GENERIC_ERROR_MESSAGE)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("secret.py", text)
        self.assertNotIn("psycopg2", text)

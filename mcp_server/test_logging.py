"""Tests for MCP logging functionality."""

from datetime import datetime, timedelta
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger

from .test_helpers import create_test_user


@tagged("much_unit", "post_install", "-at_install")
class TestMCPLogging(TransactionCase):
    def setUp(self):
        super().setUp()
        # Logging is on by default (mcp_server.enable_logging) and there is no
        # test-mode skip guard, so log_event records whenever enabled.
        self.MCPLog = self.env["mcp.log"]
        self.test_user = create_test_user(
            self.env, "Test MCP User", "test_mcp_user", email="test_mcp@example.com"
        )
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.enable_logging", "True"
        )

    def _backdate_log(self, log, when):
        """Force a log's create_date to ``when``.

        Odoo 19 strips create_date (a log-access magic column) from create()
        vals, so it must be set directly via SQL for retention/cleanup tests.
        """
        self.env.cr.execute(
            "UPDATE mcp_log SET create_date = %s WHERE id = %s",
            (when, log.id),
        )
        log.invalidate_recordset(["create_date"])

    def _clear_logs(self):
        """Empty mcp.log so a global ``cleanup_old_logs()`` count is deterministic.

        Audit rows are written on independent, committed cursors (so they survive
        a request-transaction rollback), which means rows leaked by other HttpCase
        suites persist in the DB. ``cleanup_old_logs()`` deletes across the whole
        table, so without a clean slate the asserted ``deleted_count`` would also
        count that pollution. Cleared inside the test transaction; the rollback
        restores the leaked rows afterwards.
        """
        self.env["mcp.log"].sudo().search([]).unlink()

    def test_log_event_basic(self):
        """Test basic log event creation."""
        log = self.MCPLog.log_event(
            "auth_success",
            user_id=self.test_user.id,
            ip_address="192.168.1.1",
            endpoint="/mcp/auth/validate",
        )

        self.assertTrue(log)
        self.assertEqual(log.event_type, "auth_success")
        self.assertEqual(log.user_id.id, self.test_user.id)
        self.assertEqual(log.ip_address, "192.168.1.1")
        self.assertEqual(log.endpoint, "/mcp/auth/validate")

    def test_log_authentication_success(self):
        """Test authentication success logging."""
        log = self.MCPLog.log_authentication(
            success=True,
            user_id=self.test_user.id,
            api_key_used=True,
            ip_address="10.0.0.1",
        )

        self.assertEqual(log.event_type, "auth_success")
        self.assertEqual(log.user_id.id, self.test_user.id)
        self.assertTrue(log.api_key_used)
        self.assertEqual(log.ip_address, "10.0.0.1")

    def test_log_authentication_failure(self):
        """Test authentication failure logging."""
        log = self.MCPLog.log_authentication(
            success=False,
            user_id=None,
            api_key_used=True,
            ip_address="10.0.0.2",
            error_message="Invalid API key",
        )

        self.assertEqual(log.event_type, "auth_failure")
        self.assertFalse(log.user_id)
        self.assertTrue(log.api_key_used)
        self.assertEqual(log.error_message, "Invalid API key")

    def test_log_model_access(self):
        """Test model access logging."""
        log = self.MCPLog.log_model_access(
            model_name="res.partner",
            operation="search",
            user_id=self.test_user.id,
            record_ids=[1, 2, 3],
            endpoint="/mcp/xmlrpc/object",
            http_method="POST",
            duration_ms=125,
            ip_address="192.168.1.100",
        )

        self.assertEqual(log.event_type, "model_access")
        self.assertEqual(log.model_name, "res.partner")
        self.assertEqual(log.operation, "search")
        self.assertEqual(log.record_ids, "1,2,3")
        self.assertEqual(log.duration_ms, 125)

    def test_log_error(self):
        """Test error logging."""
        log = self.MCPLog.log_error(
            error_message="Model not found",
            error_code="E404",
            endpoint="/mcp/models/invalid.model/access",
            model_name="invalid.model",
            operation="access",
            user_id=self.test_user.id,
            ip_address="192.168.1.50",
        )

        self.assertEqual(log.event_type, "error")
        self.assertEqual(log.error_message, "Model not found")
        self.assertEqual(log.error_code, "E404")
        self.assertEqual(log.model_name, "invalid.model")

    def test_log_rate_limit_exceeded(self):
        """Test rate limit exceeded logging."""
        log = self.MCPLog.log_rate_limit_exceeded(
            user_id=self.test_user.id, endpoint="/mcp/models", ip_address="192.168.1.75"
        )

        self.assertEqual(log.event_type, "rate_limit")
        self.assertEqual(log.user_id.id, self.test_user.id)
        self.assertEqual(log.error_message, "Rate limit exceeded")

    def test_log_permission_denied(self):
        """Test permission denied logging."""
        log = self.MCPLog.log_permission_denied(
            model_name="account.move",
            operation="create",
            user_id=self.test_user.id,
            endpoint="/mcp/xmlrpc/object",
            ip_address="192.168.1.90",
        )

        self.assertEqual(log.event_type, "permission_denied")
        self.assertEqual(log.model_name, "account.move")
        self.assertEqual(log.operation, "create")
        self.assertIn("Permission denied", log.error_message)

    def test_log_event_records_without_opt_in_context(self):
        """log_event records with logging enabled and no opt-in context.

        Pins production behaviour: there is no test-mode skip guard, so a plain
        ``mcp.log`` recordset (no ``test_mcp_logging`` context) still writes a row
        whenever ``mcp_server.enable_logging`` is on. Guards against reintroducing
        a test-mode gate that would silently drop audit rows.
        """
        log = (
            self.env["mcp.log"]
            .sudo()
            .log_event(
                "auth_success", user_id=self.test_user.id, ip_address="10.9.8.7"
            )
        )
        self.assertTrue(log)
        self.assertEqual(log.event_type, "auth_success")

    def test_logging_disabled(self):
        """Test that logging is skipped when disabled."""
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.enable_logging", "False"
        )

        log = self.MCPLog.log_event(
            "auth_success", user_id=self.test_user.id, ip_address="192.168.1.1"
        )

        # Should return empty recordset
        self.assertFalse(log)

    def test_data_truncation(self):
        """Test that large data fields are truncated."""
        large_data = "x" * 15000  # Larger than max_text_length (10000)

        log = self.MCPLog.log_event(
            "error",
            error_message=large_data,
            request_data=large_data,
            response_data=large_data,
            user_agent=large_data,
        )

        self.assertTrue(log.error_message.endswith("... [truncated]"))
        self.assertTrue(log.request_data.endswith("... [truncated]"))
        self.assertTrue(log.response_data.endswith("... [truncated]"))
        self.assertTrue(log.user_agent.endswith("... [truncated]"))
        self.assertLessEqual(len(log.error_message), 10020)  # 10000 + '... [truncated]'

    def test_indexed_char_truncation(self):
        """Oversized indexed Char columns are capped so the audit INSERT survives.

        ``model_name``/``tool_name`` are indexed and client-controlled; an
        oversized value would overflow Postgres's ~2700-byte btree index-entry
        limit, fail the INSERT, and (via log_event's savepoint) silently drop the
        audit row -- letting a client evade auditing by padding the ``model``
        argument. Capping them keeps the row.
        """
        oversized = "x" * 5000  # over Postgres's ~2700-byte btree entry limit
        log = self.MCPLog.log_model_access(
            model_name=oversized,
            operation="read",
            tool_name=oversized,
        )
        self.assertTrue(log, "the audit row must be created, not silently dropped")
        self.assertTrue(log.model_name.endswith("... [truncated]"))
        self.assertTrue(log.tool_name.endswith("... [truncated]"))
        self.assertLessEqual(len(log.model_name), 256 + len("... [truncated]"))

    def test_cleanup_old_logs(self):
        """Test cleanup of old log entries."""
        self._clear_logs()
        # Create logs with different ages
        now = datetime.now()

        # Create old log (35 days ago)
        old_log = self.MCPLog.create({"event_type": "auth_success"})
        self._backdate_log(old_log, now - timedelta(days=35))

        # Create recent log (5 days ago)
        recent_log = self.MCPLog.create({"event_type": "auth_success"})
        self._backdate_log(recent_log, now - timedelta(days=5))

        # Create today's log
        today_log = self.MCPLog.create({"event_type": "auth_success"})

        # Clean up logs older than 30 days
        deleted_count = self.MCPLog.cleanup_old_logs(days=30)

        self.assertEqual(deleted_count, 1)
        self.assertFalse(old_log.exists())
        self.assertTrue(recent_log.exists())
        self.assertTrue(today_log.exists())

    def test_cleanup_with_zero_retention(self):
        """Test that cleanup does nothing when retention is 0."""
        log = self.MCPLog.create(
            {
                "event_type": "auth_success",
                "create_date": datetime.now() - timedelta(days=100),
            }
        )

        # Set retention to 0 (keep forever)
        deleted_count = self.MCPLog.cleanup_old_logs(days=0)

        self.assertEqual(deleted_count, 0)
        self.assertTrue(log.exists())

    def test_cleanup_with_config_parameter(self):
        """Test cleanup using config parameter for retention days."""
        self._clear_logs()
        # Set retention to 7 days in config
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.log_retention_days", "7"
        )

        old_log = self.MCPLog.create({"event_type": "auth_success"})
        self._backdate_log(old_log, datetime.now() - timedelta(days=10))
        recent_log = self.MCPLog.create({"event_type": "auth_success"})
        self._backdate_log(recent_log, datetime.now() - timedelta(days=3))

        # Clean up using config parameter
        deleted_count = self.MCPLog.cleanup_old_logs()

        self.assertEqual(deleted_count, 1)
        self.assertFalse(old_log.exists())
        self.assertTrue(recent_log.exists())

    def test_get_summary(self):
        """Test log summary generation."""
        log = self.MCPLog.create(
            {
                "event_type": "model_access",
                "model_name": "res.partner",
                "operation": "search",
                "error_message": "This is a very long error message "
                "that should be truncated in the summary display",
            }
        )

        summary = log.get_summary()
        self.assertIn("model_access", summary)
        self.assertIn("res.partner", summary)
        self.assertIn("search", summary)
        self.assertIn("...", summary)  # Error message should be truncated

    def test_display_name_computation(self):
        """Test display name computation."""
        log = self.MCPLog.create(
            {
                "event_type": "model_access",
                "model_name": "res.partner",
                "operation": "create",
            }
        )

        # Force computation
        log._compute_display_name()

        self.assertIn("Model Access", log.display_name)
        self.assertIn("res.partner", log.display_name)
        self.assertIn("create", log.display_name)

    def test_log_creation_error_handling(self):
        """Test that log creation errors are handled gracefully."""
        with patch.object(
            type(self.MCPLog), "create", side_effect=Exception("Database error")
        ):
            # Should not raise exception, just return empty recordset
            log = self.MCPLog.log_event("auth_success", user_id=self.test_user.id)
            self.assertFalse(log)

    def test_log_event_db_failure_does_not_poison_transaction(self):
        """A DB-level audit failure must not abort the caller's transaction.

        log_event wraps the create in a savepoint, so a failure that reaches the
        database (here a user_id with no matching res.users row -> foreign-key
        violation at INSERT) is rolled back in isolation. Without the savepoint
        the poisoned transaction would discard the caller's already-successful
        writes on the shared request cursor -- silent data loss behind a success
        response.

        Distinct from test_log_creation_error_handling, which patches create() to
        raise a *Python* error before any SQL runs, so the real transaction is
        never actually aborted and the savepoint is not exercised.
        """
        # A caller write that must survive the failed audit. Flush it so it is
        # persisted *before* log_event's savepoint -- a rollback-to-savepoint can
        # then never touch it.
        marker = self.env["res.partner"].create({"name": "audit-savepoint-marker"})
        self.env.flush_all()

        # A user_id past every res.users id violates the mcp_log FK when the row
        # is INSERTed -- a genuine database error, not a pre-flight Python one.
        bad_user_id = 2147483000
        with mute_logger(
            "odoo.addons.mcp_server.models.mcp_log", "odoo.sql_db"
        ):
            result = self.MCPLog.log_event(
                "model_access",
                user_id=bad_user_id,
                model_name="res.partner",
                operation="read",
            )

        # Swallowed: empty recordset, no exception propagated to the caller.
        self.assertFalse(result)
        # The transaction survived -- the cursor is still usable and the caller's
        # write is intact (a poisoned transaction would raise on either
        # statement below).
        self.assertTrue(marker.exists())
        marker.write({"name": "audit-savepoint-marker-after"})
        self.assertEqual(marker.name, "audit-savepoint-marker-after")

    def test_ipv6_address_storage(self):
        """Test that IPv6 addresses can be stored."""
        ipv6_address = "2001:0db8:85a3:0000:0000:8a2e:0370:7334"
        log = self.MCPLog.log_event("auth_success", ip_address=ipv6_address)

        self.assertEqual(log.ip_address, ipv6_address)

    def test_json_data_fields(self):
        """Test storing JSON data in request/response fields."""
        request_data = (
            '{"method": "search", "params": {"domain": [["active", "=", true]]}}'
        )
        response_data = '{"result": [1, 2, 3], "count": 3}'

        log = self.MCPLog.log_event(
            "model_access", request_data=request_data, response_data=response_data
        )

        self.assertEqual(log.request_data, request_data)
        self.assertEqual(log.response_data, response_data)

    def test_crons_repeat_without_limit(self):
        """Both MCP crons repeat forever: Odoo 17 stops a cron after numbercall runs."""
        log_cron = self.env["ir.cron"].search([("name", "=", "MCP Log Cleanup")])
        crons = self.env.ref("mcp_server.ir_cron_oauth_gc") | log_cron
        self.assertEqual(len(crons), 2)
        for cron in crons:
            self.assertEqual(cron.numbercall, -1, cron.name)
            self.assertTrue(cron.active, cron.name)

    def test_log_cron_created_without_limit(self):
        """A missing log cron is created again with numbercall -1."""
        self.env["ir.cron"].search([("name", "=", "MCP Log Cleanup")]).unlink()
        self.MCPLog._register_hook()
        cron = self.env["ir.cron"].search([("name", "=", "MCP Log Cleanup")])
        self.assertEqual(cron.numbercall, -1)
        self.assertFalse(cron.doall)

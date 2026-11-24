"""MCP Log Model for tracking MCP server activity."""

import logging
from datetime import timedelta

from odoo import SUPERUSER_ID, api, fields, models

_logger = logging.getLogger(__name__)


class MCPLog(models.Model):
    _name = "mcp.log"
    _description = "MCP Server Activity Log"
    _order = "create_date desc"
    _rec_name = "event_type"

    # Redeclared to add a DB index: create_date backs both the _order
    # ("create_date desc") and the daily GC range-query (cleanup_old_logs),
    # which the sibling filter fields already index.
    create_date = fields.Datetime(index=True)

    # Basic fields
    event_type = fields.Selection(
        [
            ("auth_success", "Authentication Success"),
            ("auth_failure", "Authentication Failure"),
            ("model_access", "Model Access"),
            ("resource_retrieval", "Resource Retrieval"),
            ("write_operation", "Write Operation"),
            ("error", "Error"),
            ("rate_limit", "Rate Limit Exceeded"),
            ("permission_denied", "Permission Denied"),
        ],
        required=True,
        index=True,
    )

    # User and authentication info
    user_id = fields.Many2one("res.users", string="User", index=True)
    api_key_used = fields.Boolean(string="API Key Used", default=False)
    ip_address = fields.Char(string="IP Address", size=45)  # Size 45 for IPv6

    # How the request authenticated + OAuth attribution. ``auth_method``
    # supersedes the bare ``api_key_used`` boolean (which cannot tell an OAuth
    # request from a legacy session); ``oauth_client_id`` / ``oauth_scope``
    # identify which registered OAuth app acted and with what granted scope.
    # Populated on the native ``/mcp`` operation rows (ir.http stashes them on
    # ``request`` during the bearer handshake); empty on the legacy
    # REST / XML-RPC rows.
    auth_method = fields.Selection(
        [("api_key", "API Key"), ("oauth", "OAuth"), ("session", "Session")],
        index=True,
    )
    oauth_client_id = fields.Many2one(
        "mcp.oauth.client", string="OAuth Client", ondelete="set null", index=True
    )
    oauth_scope = fields.Char()

    # Request details
    endpoint = fields.Char(index=True)
    http_method = fields.Char()
    model_name = fields.Char(string="Model", index=True)
    operation = fields.Char()
    # The MCP tool that produced this row (search_records, get_record, ...) --
    # distinct from ``operation`` (the underlying Odoo op: read/write/create),
    # which cannot tell e.g. search_records from aggregate_records apart.
    tool_name = fields.Char(string="MCP Tool", index=True)
    record_ids = fields.Char(string="Record IDs")

    # Request and response data
    request_data = fields.Text()
    response_data = fields.Text()

    # Error details
    error_message = fields.Text()
    error_code = fields.Char()

    # Performance metrics
    duration_ms = fields.Integer(string="Duration (ms)")

    # Additional metadata
    session_id = fields.Char(string="Session ID")
    user_agent = fields.Text()

    @api.model
    def log_event(self, event_type, **kwargs):
        """Create a log entry for an MCP event."""
        # Skip logging if MCP logging is disabled
        if (
            self.env["ir.config_parameter"]
            .sudo()  # sudo: read a global config flag, not user-scoped data.
            .get_param("mcp_server.enable_logging", "True")
            != "True"
        ):
            return self.env["mcp.log"]

        # Skip only when explicitly requested via context (lets tests opt out).
        if self.env.context.get("skip_mcp_logging"):
            return self.env["mcp.log"]

        # Skip when the cursor is read-only (e.g. HTTP tests): an INSERT would
        # raise "cannot execute INSERT in a read-only transaction".
        if getattr(self.env.cr, "readonly", False):
            return self.env["mcp.log"]

        log_data = {
            "event_type": event_type,
            "user_id": kwargs.get(
                "user_id",
                self.env.user.id if self.env.user.id != SUPERUSER_ID else False,
            ),
            "api_key_used": kwargs.get("api_key_used", False),
            "ip_address": kwargs.get("ip_address"),
            "auth_method": kwargs.get("auth_method"),
            "oauth_client_id": kwargs.get("oauth_client_id"),
            "oauth_scope": kwargs.get("oauth_scope"),
            "endpoint": kwargs.get("endpoint"),
            "http_method": kwargs.get("http_method"),
            "model_name": kwargs.get("model_name"),
            "operation": kwargs.get("operation"),
            "tool_name": kwargs.get("tool_name"),
            "record_ids": kwargs.get("record_ids"),
            "request_data": kwargs.get("request_data"),
            "response_data": kwargs.get("response_data"),
            "error_message": kwargs.get("error_message"),
            "error_code": kwargs.get("error_code"),
            "duration_ms": kwargs.get("duration_ms"),
            "session_id": kwargs.get("session_id"),
            "user_agent": kwargs.get("user_agent"),
        }

        # Truncate large data fields to prevent database issues
        max_text_length = 10000
        for field in ["request_data", "response_data", "error_message", "user_agent"]:
            if log_data.get(field) and len(str(log_data[field])) > max_text_length:
                log_data[field] = (
                    str(log_data[field])[:max_text_length] + "... [truncated]"
                )

        # Cap the short identifier columns too. ``model_name``/``tool_name`` are
        # indexed and client-controlled (the native endpoint forwards the request's
        # ``model`` argument and ``tools/call`` method name straight through), so an
        # oversized value overflows the btree index -> the INSERT fails -> the
        # savepoint below rolls it back and the audit row is silently dropped
        # (audit evasion). ``operation`` can likewise carry a client string. Cap
        # them well under Postgres's ~2700-byte btree index-entry limit.
        max_char_length = 256
        for field in ["model_name", "tool_name", "operation"]:
            if log_data.get(field) and len(str(log_data[field])) > max_char_length:
                log_data[field] = (
                    str(log_data[field])[:max_char_length] + "... [truncated]"
                )

        try:
            # Savepoint so a DB-level failure (e.g. an FK/constraint violation at
            # INSERT) rolls back in isolation instead of poisoning the caller's
            # transaction. On the shared request cursor an unguarded failed INSERT
            # aborts the whole transaction, so a tool call that already succeeded
            # would be silently discarded at end-of-request (data loss behind a
            # success response). flush=True (the default) persists the caller's
            # pending writes *before* the savepoint, so they survive the rollback.
            with self.env.cr.savepoint():
                return self.sudo().create(log_data)  # sudo: audit row, any user.
        except Exception as e:
            # Auditing must never break the request: log the failure (rather than
            # swallow it silently) and return an empty recordset.
            _logger.error("Failed to create MCP log entry: %s", e)
            return self.env["mcp.log"]

    @api.model
    def log_authentication(
        self,
        success,
        user_id=None,
        api_key_used=False,
        ip_address=None,
        error_message=None,
    ):
        """Log authentication attempts."""
        return self.log_event(
            "auth_success" if success else "auth_failure",
            user_id=user_id,
            api_key_used=api_key_used,
            ip_address=ip_address,
            error_message=error_message,
        )

    @api.model
    def log_model_access(
        self,
        model_name,
        operation,
        user_id=None,
        record_ids=None,
        endpoint=None,
        http_method=None,
        duration_ms=None,
        ip_address=None,
        tool_name=None,
        request_data=None,
        response_data=None,
        session_id=None,
        user_agent=None,
        auth_method=None,
        oauth_client_id=None,
        oauth_scope=None,
    ):
        """Log model access operations.

        ``request_data`` / ``response_data`` carry metadata only (argument keys,
        a result-size summary) -- never raw field values -- so no business data
        lands in the audit table. ``auth_method`` / ``oauth_client_id`` /
        ``oauth_scope`` attribute the operation to its front door.
        """
        record_ids_str = ",".join(map(str, record_ids)) if record_ids else None
        return self.log_event(
            "model_access",
            model_name=model_name,
            operation=operation,
            user_id=user_id,
            record_ids=record_ids_str,
            endpoint=endpoint,
            http_method=http_method,
            duration_ms=duration_ms,
            ip_address=ip_address,
            tool_name=tool_name,
            request_data=request_data,
            response_data=response_data,
            session_id=session_id,
            user_agent=user_agent,
            auth_method=auth_method,
            oauth_client_id=oauth_client_id,
            oauth_scope=oauth_scope,
        )

    @api.model
    def log_error(
        self,
        error_message,
        error_code=None,
        endpoint=None,
        model_name=None,
        operation=None,
        user_id=None,
        ip_address=None,
        request_data=None,
    ):
        """Log error events."""
        return self.log_event(
            "error",
            error_message=error_message,
            error_code=error_code,
            endpoint=endpoint,
            model_name=model_name,
            operation=operation,
            user_id=user_id,
            ip_address=ip_address,
            request_data=request_data,
        )

    @api.model
    def log_rate_limit_exceeded(self, user_id, endpoint=None, ip_address=None):
        """Log rate limit exceeded events."""
        return self.log_event(
            "rate_limit",
            user_id=user_id,
            endpoint=endpoint,
            ip_address=ip_address,
            error_message="Rate limit exceeded",
        )

    @api.model
    def log_permission_denied(
        self,
        model_name,
        operation,
        user_id=None,
        endpoint=None,
        ip_address=None,
        error_message=None,
        auth_method=None,
        oauth_client_id=None,
        oauth_scope=None,
        user_agent=None,
        session_id=None,
        tool_name=None,
    ):
        """Log permission denied events."""
        return self.log_event(
            "permission_denied",
            model_name=model_name,
            operation=operation,
            user_id=user_id,
            endpoint=endpoint,
            ip_address=ip_address,
            error_message=error_message
            or f"Permission denied for {operation} on {model_name}",
            auth_method=auth_method,
            oauth_client_id=oauth_client_id,
            oauth_scope=oauth_scope,
            user_agent=user_agent,
            tool_name=tool_name,
            session_id=session_id,
        )

    @api.model
    def cleanup_old_logs(self, days=None):
        """Clean up old log entries based on retention settings.

        :param days: Number of days to retain logs; overrides config if provided.
        """
        if days is None:
            days = int(
                self.env["ir.config_parameter"]
                .sudo()  # sudo: read a global config value, not user-scoped data.
                .get_param("mcp_server.log_retention_days", "30")
            )

        if days <= 0:
            # 0 or negative means keep logs forever
            return 0

        # UTC to match how Odoo stores create_date (naive UTC); datetime.now()
        # would drift by the host's offset on a non-UTC server.
        cutoff_date = fields.Datetime.now() - timedelta(days=days)

        # Delete in bounded batches: mcp.log grows one row per request, so a
        # first cleanup over a large backlog must not materialize the whole
        # recordset (and its ORM cache) in one shot. Each search re-queries the
        # DB, so deleted rows never reappear and the loop terminates.
        domain = [("create_date", "<", cutoff_date)]
        count = 0
        while True:
            batch = self.search(domain, limit=1000)
            if not batch:
                break
            count += len(batch)
            batch.unlink()

        _logger.info("Cleaned up %s MCP log entries older than %s days", count, days)
        return count

    @api.model
    def _register_hook(self):
        """Ensure the log-cleanup cron exists -- create-only, never overwrite.

        ``_register_hook`` runs on *every* registry load (worker boot / upgrade),
        not just install, so it must not touch an existing cron: an admin who
        disabled it or changed its cadence would have that reverted on restart.
        Same "don't clobber admin edits" intent as the sibling
        ``data/oauth_cron.xml`` (via ``noupdate``).
        """
        super()._register_hook()
        if self.env["ir.cron"].search_count([("name", "=", "MCP Log Cleanup")]):
            return
        self.env["ir.cron"].create(
            {
                "name": "MCP Log Cleanup",
                "model_id": self.env["ir.model"].search([("model", "=", "mcp.log")]).id,
                "state": "code",
                "code": "model.cleanup_old_logs()",
                "interval_type": "days",
                "interval_number": 1,
                # Odoo 17 runs a cron numbercall times; -1 means forever.
                "numbercall": -1,
                "doall": False,
                "active": True,
            }
        )

    def get_summary(self):
        """Get a summary of the log entry for display."""
        self.ensure_one()
        summary = f"{self.event_type}"
        if self.model_name:
            summary += f" - {self.model_name}"
        if self.operation:
            summary += f".{self.operation}"
        if self.error_message:
            summary += f" - {self.error_message[:50]}..."
        return summary

    @api.depends("event_type", "model_name", "operation")
    def _compute_display_name(self):
        """Compute display name for tree views."""
        for record in self:
            parts = [
                dict(record._fields["event_type"].selection).get(record.event_type, "")
            ]
            if record.model_name:
                parts.append(record.model_name)
            if record.operation:
                parts.append(record.operation)
            record.display_name = " - ".join(filter(None, parts))

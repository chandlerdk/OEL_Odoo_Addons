from odoo import api, fields, models


class McpModelSelectionWizard(models.TransientModel):
    """Wizard for selecting multiple models to enable for MCP access at once."""

    _name = "mcp.model.selection.wizard"
    _description = "MCP Model Selection Wizard"

    @api.model
    def _get_model_domain(self):
        enabled_model_ids = (
            self.env["mcp.enabled.model"].search([]).mapped("model_id.id")
        )
        domain = [
            ("transient", "=", False),
            ("model", "not like", "ir.%"),
            ("model", "not like", "base_%"),
            # The module's own models (mcp.log, mcp.enabled.model, mcp.oauth.*)
            # are never sensible MCP targets -- exposing mcp.oauth.token would
            # even surface token hashes over MCP -- so keep them unselectable.
            ("model", "not like", "mcp.%"),
        ]
        if enabled_model_ids:
            domain.append(("id", "not in", enabled_model_ids))
        return domain

    model_ids = fields.Many2many(
        "ir.model",
        string="Models",
        required=True,
        domain=lambda self: self._get_model_domain(),
        help="Select models to enable for MCP access",
    )
    allow_read = fields.Boolean(default=True, help="Allow read operations through MCP")
    allow_create = fields.Boolean(
        default=False, help="Allow create operations through MCP"
    )
    allow_write = fields.Boolean(
        string="Allow Update", default=False, help="Allow update operations through MCP"
    )
    allow_unlink = fields.Boolean(
        string="Allow Delete", default=False, help="Allow delete operations through MCP"
    )
    allow_method_calls = fields.Boolean(
        default=False,
        help="Admin opt-in (off by default): let MCP clients invoke this "
        "model's own public business methods through call_model_method. Private "
        "(leading-underscore) methods, the public web_* family and the generic "
        "ORM/CRUD/data-access API (create/read/write/unlink/search/...) are "
        "always blocked; use the per-operation permissions above for CRUD. "
        "Everything runs under the calling user's Odoo access rights.",
    )

    def action_enable_models(self):
        """Enable selected models for MCP access.

        A selected model may already have an mcp.enabled.model row that is
        ARCHIVED (still occupying its ``UNIQUE(model_id)`` slot). Such rows are
        invisible to a default search, so a plain ``create()`` would raise the
        unique constraint. Search with ``active_test=False`` and reactivate the
        archived row (refreshing its permissions) instead of creating a
        duplicate; create only genuinely new models.
        """
        self.ensure_one()
        vals = {
            "allow_read": self.allow_read,
            "allow_create": self.allow_create,
            "allow_write": self.allow_write,
            "allow_unlink": self.allow_unlink,
            "allow_method_calls": self.allow_method_calls,
        }
        existing = (
            self.env["mcp.enabled.model"]
            .with_context(active_test=False)
            .search([("model_id", "in", self.model_ids.ids)])
        )
        existing_by_model = {rec.model_id.id: rec for rec in existing}

        vals_list = []
        for model in self.model_ids:
            record = existing_by_model.get(model.id)
            if not record:
                vals_list.append({"model_id": model.id, **vals})
            elif not record.active:
                record.write({**vals, "active": True})
            # An already-active row is left untouched.
        if vals_list:
            self.env["mcp.enabled.model"].create(vals_list)
        return {"type": "ir.actions.act_window_close"}

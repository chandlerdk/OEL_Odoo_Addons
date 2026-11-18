from odoo import fields, models


class McpOauthBulkConfirmWizard(models.TransientModel):
    """Confirmation dialog for the destructive bulk OAuth actions.

    ``ir.actions.server`` has no ``confirm`` attribute, so the bulk "Revoke"
    (tokens) and "Deactivate" (clients) entries in the list Actions menu open
    this wizard instead of acting immediately -- giving the same confirmation
    step the single-record form buttons already have. The wizard holds no
    records of its own: it acts on the list selection carried in the standard
    ``active_ids`` context key and delegates to the same admin-gated object
    methods.
    """

    _name = "mcp.oauth.bulk.confirm.wizard"
    _description = "Confirm Bulk OAuth Action"

    operation = fields.Selection(
        [("revoke", "Revoke Tokens"), ("deactivate", "Deactivate Clients")],
        required=True,
    )

    def action_confirm(self):
        self.ensure_one()
        record_ids = self.env.context.get("active_ids", [])
        if self.operation == "revoke":
            self.env["mcp.oauth.token"].browse(record_ids).action_revoke()
        else:
            self.env["mcp.oauth.client"].browse(record_ids).action_deactivate()
        return {"type": "ir.actions.act_window_close"}

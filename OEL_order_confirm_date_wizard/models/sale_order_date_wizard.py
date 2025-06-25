from odoo import api, fields, models

class SaleOrderConfirmDateWizard(models.TransientModel):
    _name = "sale.order.confirm.date.wizard"
    _description = "Wizard to Confirm or Update Sale Order Date"

    original_date_order = fields.Datetime(
        string="Original Order Date", readonly=True,
        help="The date on the order before reconfirmation."
    )
    keep_original = fields.Boolean(
        string="Keep the existing order date", default=True
    )
    update_to_today = fields.Boolean(
        string="Update order date to now", default=False
    )

    @api.model
    def default_get(self, fields_list):
        """Populate original_date_order from the active order."""
        res = super().default_get(fields_list)
        order = self.env["sale.order"].browse(self.env.context.get("active_id"))
        res["original_date_order"] = order.date_order
        return res

    @api.onchange("keep_original")
    def _onchange_keep(self):
        if self.keep_original:
            self.update_to_today = False

    @api.onchange("update_to_today")
    def _onchange_update(self):
        if self.update_to_today:
            self.keep_original = False

    def confirm(self):
        """Force the date per choice, clear flag, confirm, then restore."""
        self.ensure_one()
        order = self.env["sale.order"].browse(self.env.context["active_id"])

        # 1) Decide which date we want
        if self.update_to_today:
            desired_date = fields.Datetime.now()
        else:
            desired_date = self.original_date_order

        # 2) Clear the cancelled-flag so the wizard won't reappear
        order.was_cancelled = False

        # 3) Perform the normal confirm (will set date_order itself)
        order.with_context(skip_date_confirm=True).action_confirm()

        # 4) Restore our desired date
        order.date_order = desired_date

        # 5) Refresh the screen
        return {"type": "ir.actions.client", "tag": "reload"}


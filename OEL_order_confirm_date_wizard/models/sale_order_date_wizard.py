from odoo import api, fields, models

class SaleOrderConfirmDateWizard(models.TransientModel):
    _name = "sale.order.confirm.date.wizard"
    _description = "Wizard to Confirm or Update Sale Order Date"

    original_date_order = fields.Datetime(
        string="Original Order Date", readonly=True,
        help="The date on the order before reconfirmation."
    )
    original_delivery_date = fields.Datetime(
        string="Original Delivery Scheduled Date", readonly=True,
        help="The scheduled date of the outgoing delivery before cancellation."
    )
    keep_original = fields.Boolean(
        string="Keep the existing order date", default=True
    )
    update_to_today = fields.Boolean(
        string="Update order date to now", default=False
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        order = self.env["sale.order"].browse(self.env.context.get("active_id"))
        res["original_date_order"] = order.date_order
        res["original_delivery_date"] = order.original_delivery_date
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
        self.ensure_one()
        order = self.env["sale.order"].browse(self.env.context["active_id"])

        # 1) Decide which SO date to use
        if self.update_to_today:
            desired_date = fields.Datetime.now()
        else:
            desired_date = self.original_date_order

        # 2) Clear the cancelled flag so wizard won't reappear
        order.was_cancelled = False

        # 3) Normal confirm (creates new pickings)
        order.with_context(skip_date_confirm=True).action_confirm()

        # 4) Restore desired SO date
        order.date_order = desired_date

        # 5) If keeping original, restore the delivery scheduled date on new pickings
        #    This does NOT touch commitment_date or lead time — only the picking's
        #    scheduled_date field, so native lead time edits still apply going forward.
        if self.keep_original and order.original_delivery_date:
            new_outgoing = order.picking_ids.filtered(
                lambda p: p.picking_type_code == 'outgoing'
                and p.state not in ('done', 'cancel')
            )
            if new_outgoing:
                new_outgoing.write({
                    'scheduled_date': order.original_delivery_date
                })

        # 6) Refresh the screen
        return {"type": "ir.actions.client", "tag": "reload"}

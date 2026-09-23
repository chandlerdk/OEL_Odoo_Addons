from odoo import models, fields

class SaleOrder(models.Model):
    _inherit = "sale.order"

    was_cancelled = fields.Boolean(
        string="Was Cancelled",
        default=False,
        help="Set when an order is cancelled, so we prompt on reconfirm."
    )
    original_delivery_date = fields.Datetime(
        string="Original Delivery Scheduled Date",
        help="Stores the outgoing picking scheduled date before cancellation."
    )

    def action_cancel(self):
        for order in self:
            # Capture scheduled date BEFORE super() cancels the pickings
            outgoing = order.picking_ids.filtered(
                lambda p: p.picking_type_code == 'outgoing'
                and p.state not in ('done', 'cancel')
            )
            if outgoing:
                order.original_delivery_date = outgoing[0].scheduled_date

        res = super().action_cancel()
        self.write({"was_cancelled": True})
        return res

    def action_confirm(self):
        for order in self:
            if order.was_cancelled:
                return {
                    "name": "Confirm Order Date",
                    "type": "ir.actions.act_window",
                    "res_model": "sale.order.confirm.date.wizard",
                    "view_mode": "form",
                    "target": "new",
                    "context": {"active_id": order.id},
                }
        return super().action_confirm()

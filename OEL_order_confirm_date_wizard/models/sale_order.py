from odoo import models, fields

class SaleOrder(models.Model):
    _inherit = "sale.order"

    was_cancelled = fields.Boolean(
        string="Was Cancelled",
        default=False,
        help="Set when an order is cancelled, so we prompt on reconfirm."
    )

    def action_cancel(self):
        # call the original cancel logic
        res = super().action_cancel()
        # mark for reconfirm wizard
        self.write({"was_cancelled": True})
        return res

    def action_confirm(self):
        # if it was cancelled before, pop up our wizard
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
        # otherwise fall back to normal confirm
        return super().action_confirm()


from odoo import models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _notify_get_reply_to(self, default=None):
        """Route all sale order email replies to the customer service inbox."""
        res = super()._notify_get_reply_to(default=default)
        cs_email = self.env['ir.config_parameter'].sudo().get_param(
            'sale.customer_service_reply_to', default=None
        )
        if cs_email:
            for record in self:
                res[record.id] = cs_email
        return res

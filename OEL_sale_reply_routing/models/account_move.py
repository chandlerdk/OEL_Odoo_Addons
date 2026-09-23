from odoo import models


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _notify_get_reply_to(self, default=None):
        """Route invoice and credit note replies to the accounting inbox."""
        res = super()._notify_get_reply_to(default=default)
        accounting_email = self.env['ir.config_parameter'].sudo().get_param(
            'account.accounting_reply_to', default=None
        )
        if accounting_email:
            for record in self:
                if record.move_type in ('out_invoice', 'out_refund', 'in_invoice', 'in_refund'):
                    res[record.id] = accounting_email
        return res

from odoo import models, fields, api

class AccountMove(models.Model):
    _inherit = 'account.move'

    discount_total = fields.Monetary(
        string="Discount Applied",
        compute="_compute_discount_total",
        currency_field='currency_id'
    )
    payment_difference_amount = fields.Float(
        string="Payment Difference",
        help="Stores the payment difference from the payment wizard",
        copy=False
    )

    @api.depends('invoice_line_ids.discount', 'invoice_line_ids.price_unit', 'invoice_line_ids.quantity')
    def _compute_discount_total(self):
        for move in self:
            total_discount = sum(
                line.price_unit * line.quantity * (line.discount / 100)
                for line in move.invoice_line_ids if line.discount
            )
            move.discount_total = total_discount + move.payment_difference_amount

class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'
    

    def _reconcile_payments(self, to_process, edit_mode=False):
        res = super()._reconcile_payments(to_process, edit_mode=False)
        if self.line_ids:
            for line in self.line_ids:
                move = self.env['account.move'].search([('name', '=', line.name)], limit=1)
                if move and self.early_payment_discount_mode:
                    move.write({'payment_difference_amount': self.payment_difference})
        return res
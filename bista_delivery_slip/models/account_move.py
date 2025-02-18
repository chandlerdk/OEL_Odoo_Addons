from odoo import models, fields, api

class AccountMove(models.Model):
    _inherit = 'account.move'

    discount_total = fields.Monetary(
        string="Discount Applied",
        compute="_compute_discount_total",
        currency_field='currency_id'
    )

    @api.depends('invoice_line_ids.discount', 'invoice_line_ids.price_unit', 'invoice_line_ids.quantity')
    def _compute_discount_total(self):
        for move in self:
            total_discount = sum(
                line.price_unit * line.quantity * (line.discount / 100)
                for line in move.invoice_line_ids if line.discount
            )
            move.discount_total = total_discount

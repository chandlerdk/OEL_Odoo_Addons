from odoo import fields, models


class TransactionDetailsWizard(models.TransientModel):
    _name = 'transaction.detail.wizard'
    _description = "Transaction Details Wizard"

    name = fields.Char()
    transaction_lines = fields.One2many('transaction.detail.line', 'transaction_detail_id')


class TransactionDetailsLine(models.TransientModel):
    _name = 'transaction.detail.line'
    _description = "Transaction Detail Line"

    transaction_detail_id = fields.Many2one('transaction.detail.wizard')
    currency_id = fields.Many2one('res.currency', default=lambda self:  self.env.user.currency_id.id)
    name = fields.Char(string='Document #')
    document_type = fields.Char(string="Document Type")
    payment_amount = fields.Monetary(string='Payment Amount')



# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class AccountInvoiceReport(models.Model):
    _inherit = 'account.invoice.report'

    sale_rep_id = fields.Many2one('res.partner', string="Sales Rep", domain=[('sale_rep_id', '=', True)])

    def _select_list(self, table):
        return super()._select_list(table) + [table.move_id.sale_rep_id]

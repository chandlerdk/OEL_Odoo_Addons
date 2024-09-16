# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class AccountInvoiceReport(models.Model):
    _inherit = 'account.invoice.report'

    sale_rep_id = fields.Many2one('res.partner', string="Sales Rep", domain=[('sale_rep_id', '=', True)])

    def _select(self):
        return super()._select() + ", move.sale_rep_id as sale_rep_id"

    def _group_by(self):
        return super()._group_by() + ", move.sale_rep_id"
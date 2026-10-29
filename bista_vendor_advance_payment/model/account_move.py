# -*- encoding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (http://www.bistasolutions.com)
#
##############################################################################

from odoo import fields, models
from odoo.fields import Command


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    purchase_line_ids = fields.Many2many(
        'purchase.order.line',
        'purchase_order_line_invoice_rel',
        'invoice_line_id', 'order_line_id',
        string='Purchase Order Lines', readonly=True, copy=False)

    def _copy_data_extend_business_fields(self, values):
        super()._copy_data_extend_business_fields(values)
        values['purchase_line_ids'] = [Command.set(self.purchase_line_ids.ids)]


class AccountMove(models.Model):
    _inherit = 'account.move'

    def unlink(self):
        prepayment_lines = self.line_ids.purchase_line_ids.filtered(
            lambda line: line.is_prepayment and line.invoice_lines <= self.line_ids)
        res = super().unlink()
        if prepayment_lines:
            prepayment_lines.unlink()
        return res

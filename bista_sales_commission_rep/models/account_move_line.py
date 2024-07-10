# -*- coding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (https://www.bistasolutions.com)
#
##############################################################################

from odoo import api, fields, models

class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    sale_rep_id = fields.Many2one('res.partner', string='Sale Rep', related="move_id.sale_rep_id")

    @api.depends("sale_person_id", "team_id",
                 "commission_id",
                 "commission_percent",
                 "price_total",
                 "partner_id",
                 "product_id")
    def _compute_commission_amount(self):
        for line in self:
            if not line.commission_percent:
                line.commission_amount = 0
                continue

            data = {
                'product_id': line.product_id,
                'partner_id': line.partner_id,
                'quantity': line.quantity,
                'amount_after_tax': line.price_total,
                'amount_before_tax': line.price_subtotal,
                'percentage': line.commission_percent
            }
            amount = 0
            if line.commission_id:
                amount = line.commission_id.calculate_amount(data)
            else:
                sale_commission = self.env['sale.commission']
                rules = []
                if line.sale_rep_id:
                    rules = sale_commission.search([('sale_rep_id', '=', line.sale_rep_id.id)],
                                                   order='priority desc')
                elif line.sale_percent:
                    user = line.sale_person_id
                    rules = sale_commission.search([('user_ids', '=', user.id)],
                                                   order='priority desc')

                for rule in rules:
                    data['percentage'] = rule.percentage
                    amount = rule.calculate_amount(data)
                    if amount:
                        line.commission_id = rule.id if rule else False
                        line.commission_percent = rule.percentage
                        break
            if line.move_id.move_type == 'out_refund':
                amount = -amount
            line.commission_amount = amount

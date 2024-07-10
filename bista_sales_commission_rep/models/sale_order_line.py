# -*- coding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (https://www.bistasolutions.com)
#
##############################################################################

from odoo import api, fields, models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    sale_rep_id = fields.Many2one('res.partner', related="order_id.sale_rep_id", store=True)

    @api.depends("user_id",
                 "sale_rep_id",
                 "team_id",
                 "commission_id",
                 "commission_percent",
                 "price_total",
                 "order_id.partner_id",
                 "product_id")
    def _compute_commission_amount(self):
        for line in self:
            data = {
                'product_id': line.product_id,
                'partner_id': line.order_id.partner_id,
                'quantity': line.product_uom_qty,
                'amount_after_tax': line.price_total,
                'amount_before_tax': line.price_subtotal,
                'percentage': 0

            }
            if not line.validate_commission_rule():
                continue

            if line.commission_id and line.commission_percent != line.commission_id.percentage:
                # Update only Commission Amount
                # Assuming the Difference between same commission object
                # and percentage is due to manual user interference
                data['percentage'] = line.commission_percent
                line.commission_amount = line.commission_id.calculate_amount(data)
                continue

            rules = []
            sale_commission = self.env['sale.commission']
            if line.sale_rep_id:
                rules = sale_commission.search([('sale_rep_id', '=', line.sale_rep_id.id)], order='priority desc')
            else:
                user = line.order_id.user_id
                rules = sale_commission.search([('user_ids', '=', user.id)], order='priority desc')
            for rule in rules:
                data['percentage'] = rule.percentage
                amount = rule.calculate_amount(data)
                if amount:
                    line.commission_amount = amount
                    line.commission_id = rule.id if rule else False
                    line.commission_percent = rule.percentage
                    break

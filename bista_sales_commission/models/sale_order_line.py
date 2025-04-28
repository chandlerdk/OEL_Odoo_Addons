# -*- coding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (https://www.bistasolutions.com)
#
##############################################################################

from odoo import api, fields, models
from dateutil import relativedelta
from odoo.exceptions import UserError


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    commission_id = fields.Many2one('sale.commission')
    commission_percent = fields.Float(string="C% Man")
    in_commission_percent = fields.Float(string="C% In")
    out_commission_percent = fields.Float(string="C% Out")
    commission_amount = fields.Float(compute='_compute_commission_amount', store=True)
    user_id = fields.Many2one('res.users', related="order_id.user_id", store=True)
    team_id = fields.Many2one('crm.team', related="order_id.team_id", store=True)

    @api.depends("user_id", "team_id",
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

            user = line.order_id.user_id
            rules = self.env['sale.commission'].search([('user_ids', '=', user.id)], order='priority desc')
            for rule in rules:
                data['percentage'] = rule.percentage
                amount = rule.calculate_amount(data)
                if amount:
                    line.commission_amount = amount
                    line.commission_id = rule.id if rule else False
                    line.commission_percent = rule.percentage
                    break

    def validate_commission_rule(self):
        if self.is_downpayment:
            # Ignore Adding commission for the Down Payments
            return

        if any(line.move_id.state == 'posted' for line in self.invoice_lines):
            # Do not update if an invoice already exists
            # If user needs to overwrite the commission amount
            # It needs to be done from the Invoice Record
            return

        if self.order_id.state != 'draft':
            # Do not update If sales Order is confirmed
            return

        return True

    def _prepare_invoice_line(self, **optional_values):
        self.ensure_one()
        res = super(SaleOrderLine, self)._prepare_invoice_line(**optional_values)
        if self.commission_id and self.commission_percent:
            res.update({
                'commission_id': self.commission_id.id,
                'commission_amount': self.commission_amount,
                'commission_percent': self.commission_percent,
                'commission_policy': self.commission_id.payout_policy
            })
        return res

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


class SaleOrder(models.Model):
    _inherit = 'sale.order'
    sale_rep_id = fields.Many2one('res.partner', domain=[('is_sale_rep', '=', True)])

    def update_commision_old_order(self,records):
        for order in records:
            for line in order.order_line:
                data = {
                    'product_id': line.product_id,
                    'partner_id': order.partner_id,
                    'quantity': line.product_uom_qty,
                    'amount_after_tax': line.price_total,
                    'amount_before_tax': line.price_subtotal,
                    'percentage': 0
                }

                if line.product_id.detailed_type == 'service':
                    # line.write({'commission_id': 124})
                    rule = self.env['sale.commission'].browse(74)
                    # data['percentage'] = rule.percentage
                    # amount = rule.calculate_amount(data)
                    # if amount:
                    line.write({
                        'commission_amount': 0.00,
                        'commission_id': rule.id,
                        'commission_percent': rule.percentage
                    })
                    continue

                sale_commission = self.env['sale.commission']
                rules = []

                if line.sale_rep_id:
                    rules = sale_commission.search([('sale_rep_id', '=', line.sale_rep_id.id)], order='priority desc')

                else:
                    user = order.user_id
                    rules = sale_commission.search([('user_ids', '=', user.id)], order='priority desc')

                for rule in rules:
                    data['percentage'] = rule.percentage
                    amount = rule.calculate_amount(data)
                    if amount:
                        line.write({
                            'commission_amount': amount,
                            'commission_id': rule.id,
                            'commission_percent': rule.percentage
                        })
                        break
            self.update_commission_on_invoices(order)

    def update_commission_on_invoices(self, order):
            invoices = order.invoice_ids
            for invoice in invoices:
                invoice_lines = self.env['account.move.line'].search([
                    ('move_id', '=', invoice.id),
                    ('is_commission_entry', '=', True)
                ])
                if invoice_lines:
                    line_ids = tuple(invoice_lines.ids)
                    self._cr.execute("DELETE FROM account_move_line WHERE id IN %s", (line_ids,))
                invoice._create_commission_payable()
                if order.sale_rep_id and not invoice.sale_rep_id:
                    invoice.write({'sale_rep_id': order.sale_rep_id.id})
                for line in invoice.invoice_line_ids:
                    if line.product_id:
                        data = {
                            'product_id': line.product_id,
                            'partner_id': invoice.partner_id,
                            'quantity': line.quantity,
                            'amount_after_tax': line.price_total,
                            'amount_before_tax': line.price_subtotal,
                            'percentage': 0
                        }
                        if line.product_id.detailed_type == 'service':
                            rule = self.env['sale.commission'].browse(74)
                            # data['percentage'] = rule.percentage
                            # amount = rule.calculate_amount(data)
                            # if amount:
                            line.write({
                                'commission_amount': 0.00,
                                'commission_id': rule.id,
                                'commission_percent': rule.percentage
                            })
                            continue

                        sale_commission = self.env['sale.commission']
                        rules = []
                        if line.sale_rep_id:
                            rules = sale_commission.search([('sale_rep_id', '=', line.sale_rep_id.id)],
                                                           order='priority desc')
                        else:
                            user = invoice.user_id
                            rules = sale_commission.search([('user_ids', '=', user.id)], order='priority desc')

                        for rule in rules:
                            data['percentage'] = rule.percentage
                            amount = rule.calculate_amount(data)
                            if amount:
                                line.write({
                                    'commission_amount': amount,
                                    'commission_id': rule.id,
                                    'commission_percent': rule.percentage
                                })
                                break

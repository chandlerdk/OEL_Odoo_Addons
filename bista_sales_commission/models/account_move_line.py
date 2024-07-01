# -*- coding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (https://www.bistasolutions.com)
#
##############################################################################

from odoo import api, fields, models
from datetime import date


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    commission_id = fields.Many2one('sale.commission')
    commission_date = fields.Date(copy=False)
    commission_move_id = fields.Many2one('account.move', string="Commission Bill", copy=False)
    reversed_move_id = fields.Many2one('account.move', related="move_id.reversed_entry_id", store=True)
    commission_move_line_id = fields.Many2one('account.move.line', string="Commission Bill Line", copy=False)
    commission_reverse_move_line_id = fields.Many2one('account.move.line',
                                                      string="Commission Reverse Move Line", copy=False)
    commission_amount = fields.Float(compute="_compute_commission_amount", store=True)
    commission_percent = fields.Float()
    commission_user_id = fields.Many2one('res.users', copy=False)
    commission_to_bill = fields.Boolean(compute="_get_commission_state", store=True, copy=False)
    commission_policy = fields.Selection([
        ('invoice', 'Invoice Generated'),
        ('payment', 'Invoice Fully Paid')
    ], required=True, default="payment", readonly=True, copy=False)
    invoice_payment_state = fields.Selection(related="move_id.payment_state", store=True, copy=False)
    commission_payment_state = fields.Selection(related="commission_move_id.payment_state", store=True, copy=False)
    sale_person_id = fields.Many2one('res.users', related="move_id.invoice_user_id", store=True)
    team_id = fields.Many2one('crm.team', related="move_id.team_id", store=True)
    is_commission_entry = fields.Boolean()
    commission_expense_account_id = fields.Many2one('account.account',
                                                    related="commission_id.expense_account_id",
                                                    store=True)
    commission_payout_account_id = fields.Many2one('account.account',
                                                   related="commission_id.payout_account_id",
                                                   store=True)
    current_line_id = fields.Integer()

    @api.depends("sale_person_id", "team_id",
                 "commission_id",
                 "commission_percent",
                 "price_total",
                 "partner_id",
                 "product_id")
    def _compute_commission_amount(self):
        for line in self:
            # Assuming invoice created from sales order always have percentage
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
                user = line.sale_person_id
                rules = self.env['sale.commission'].search([('user_ids', '=', user.id)], order='priority desc')
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

    def generate_bill(self):
        return {
            "name": "Generate Bill",
            "type": "ir.actions.act_window",
            "res_model": "commission.bill.wizard",
            "view_mode": "form",
            "target": "new",
        }

    @api.depends("invoice_payment_state", "commission_payment_state", "commission_policy")
    def _get_commission_state(self):
        paid_state = ['paid', 'in_payment']
        reversed_state = 'reversed'

        for line in self:
            bill = line.commission_move_id
            if bill and bill.state != 'cancel':
                # Ignore If Commission is already paid or bill is generated
                # Bill state is not cancelled
                line.commission_to_bill = False
                continue

            commission_to_bill = False
            if line.commission_policy == 'invoice':
                if line.move_id.state == 'posted':
                    commission_to_bill = True
            elif line.commission_policy == 'payment':
                if line.invoice_payment_state in paid_state:
                    commission_to_bill = True
                    line.commission_date = date.today()
            line.commission_to_bill = commission_to_bill

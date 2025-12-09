# -*- coding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (https://www.bistasolutions.com)
#
##############################################################################

from odoo import api, fields, models, _
from datetime import date
from odoo.exceptions import ValidationError, UserError


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    commission_id = fields.Many2one('sale.commission')
    in_commission_id = fields.Many2one('sale.commission')
    out_commission_id = fields.Many2one('sale.commission')
    commission_date = fields.Date(copy=False)
    commission_move_id = fields.Many2one('account.move', string="Commission Bill", copy=False)
    reversed_move_id = fields.Many2one('account.move', related="move_id.reversed_entry_id", store=True)
    commission_move_line_id = fields.Many2one('account.move.line', string="Commission Bill Line", copy=False)
    commission_reverse_move_line_id = fields.Many2one(
        'account.move.line',
        string="Commission Reverse Move Line",
        copy=False
    )
    commission_amount = fields.Float(
        compute="_compute_commission_amount",
        inverse="_inverse_commission_amount",
        string="C Man Amount",
        store=True
    )
    in_commission_amount = fields.Float(
        compute="_compute_commission_amount",
        inverse="_inverse_commission_amount",
        string="C In Amount",
        store=True
    )
    out_commission_amount = fields.Float(
        compute="_compute_commission_amount",
        inverse="_inverse_commission_amount",
        string="C Out Amount",
        store=True
    )

    in_commission_percent = fields.Float(string="C% In")
    out_commission_percent = fields.Float(string="C% Out")
    commission_percent = fields.Float(string="C% Man")

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
    commission_expense_account_id = fields.Many2one(
        'account.account',
        related="commission_id.expense_account_id",
        store=True
    )
    commission_payout_account_id = fields.Many2one(
        'account.account',
        related="commission_id.payout_account_id",
        store=True
    )
    current_line_id = fields.Integer()
    user_id = fields.Many2one('res.users', related="move_id.invoice_user_id")
    is_commission_billed = fields.Boolean(string="Commission Billed", default=False, copy=False)

    @api.depends(
        "sale_person_id",
        "team_id",
        "price_total",
        "partner_id",
        "product_id"
    )
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
                rules = self.env['sale.commission'].search(
                    [('user_ids', '=', user.id)],
                    order='priority desc'
                )
                for rule in rules:
                    data['percentage'] = rule.percentage
                    amount = rule.calculate_amount(data)
                    if amount:
                        line.commission_id = rule.id if rule else False
                        line.commission_percent = rule.percentage
                        break
            if line.move_id.move_type == 'out_refund':
                amount = -(amount or 0.0)
            line.commission_amount = amount

    def get_commission_rules(self):
        """Return applicable commission rules by type for this line."""
        self.ensure_one()
        sale_commission = self.env['sale.commission']

        return {
            'rep_rule': sale_commission.search([
                ('sale_rep_id', '=', self.sale_rep_id.id),
                ('sale_partner_type', '=', 'sale_rep')
            ], order='sequence', limit=1) if self.sale_rep_id else False,

            'user_rule': sale_commission.search([
                ('user_ids', 'in', self.user_id.id),
                ('sale_partner_type', '=', 'user')
            ], order='sequence', limit=1) if self.user_id else False,

            'team_rule': sale_commission.search([
                ('sale_team_rep', '=', self.team_id.user_id.id),
                ('sale_partner_type', '=', 'sale_team')
            ], order='sequence', limit=1) if self.team_id else False,
        }

    def generate_bill(self):
        grouped_lines = {}
        sale_commission = self.env['sale.commission']
        billed_partners = {}

        for line in self:
            if line.is_commission_billed:
                continue
            rules = line.get_commission_rules()

            for rule_key, rule in rules.items():
                if not rule:
                    continue

                partner = None
                amount_field = None

                if rule_key == 'rep_rule' and line.sale_rep_id and line.commission_amount:
                    partner = line.sale_rep_id
                    amount_field = 'commission_amount'

                elif rule_key == 'user_rule' and line.sale_person_id and line.in_commission_amount:
                    partner = line.sale_person_id.partner_id
                    amount_field = 'in_commission_amount'

                elif rule_key == 'team_rule' and line.team_id and line.out_commission_amount:
                    partner = line.team_id.user_id.partner_id
                    amount_field = 'out_commission_amount'

                if not partner or not amount_field:
                    continue

                # Assign the exact commission rule to the line (based on partner type)
                if rule_key == 'rep_rule':
                    rep_rules = sale_commission.search([
                        ('sale_rep_id', '=', line.sale_rep_id.id),
                        ('sale_partner_type', '=', 'sale_rep')
                    ], order='sequence',limit=1)
                elif rule_key == 'user_rule':
                    rep_rules = sale_commission.search([
                        ('user_ids', 'in', line.user_id.id),
                        ('sale_partner_type', '=', 'user')
                    ], order='sequence',limit=1)
                elif rule_key == 'team_rule':
                    rep_rules = sale_commission.search([
                        ('sale_team_rep', '=', line.team_id.user_id.id),
                        ('sale_partner_type', '=', 'sale_team')
                    ], order='sequence', limit=1)
                else:
                    rep_rules = sale_commission.browse()
                data = {
                    'percentage': 0,
                    'quantity': line.quantity,
                    'amount_after_tax': line.price_total,
                    'amount_before_tax': line.price_subtotal,
                    'product_id': line.product_id,
                    'partner_id': partner,
                }

                for commission_rule in rep_rules:
                    data['percentage'] = commission_rule.percentage
                    amount = commission_rule.calculate_amount(data)
                    if amount:
                        line.commission_id = commission_rule.id
                        key = (partner.id, commission_rule.id, amount_field)
                        grouped_lines.setdefault(key, []).append(line)
                        break
                line.is_commission_billed = True

        def create_bill(partner_id, rule_id, amount_field, lines):
            rule = self.env['sale.commission'].browse(rule_id)
            payout_account = rule.payout_account_id

            if not payout_account:
                raise UserError(f"Payout account not set on commission rule '{rule.name}'.")

            existing_bill = self.env['account.move'].search([
                ('partner_id', '=', partner_id),
                ('move_type', '=', 'in_invoice'),
                ('state', '=', 'draft'),
            ], limit=1)

            invoice_lines = []
            for line in lines:
                invoice_lines.append((0, 0, {
                    'name': f"Com: {line.move_id.name}/{line.name}",
                    'quantity': 1,
                    'price_unit': getattr(line, amount_field),
                    'account_id': payout_account.id,
                    'commission_reverse_move_line_id': line.id,
                }))

            if existing_bill:
                existing_bill.write({
                    'invoice_line_ids': invoice_lines
                })
                billed_partners[partner_id] = existing_bill.name
                for rec in existing_bill.invoice_line_ids:
                    rec.commission_reverse_move_line_id.write({
                        'commission_move_line_id': rec.id,
                        'commission_move_id': existing_bill.id
                    })
                return existing_bill
            else:
                move_vals = {
                    'move_type': 'in_invoice',
                    'is_commission_bill': True,
                    'partner_id': partner_id,
                    'invoice_line_ids': invoice_lines,
                }
                bill = self.env['account.move'].create(move_vals)
                bill._set_next_sequence()
                billed_partners[partner_id] = bill.name
                for rec in bill.invoice_line_ids:
                    rec.commission_reverse_move_line_id.write({
                        'commission_move_line_id': rec.id,
                        'commission_move_id': bill.id
                    })
                return bill

        for (partner_id, rule_id, amount_field), lines in grouped_lines.items():
            create_bill(partner_id, rule_id, amount_field, lines)

        if billed_partners:
            partner_names = ", ".join(billed_partners.values())
            message = _("Vendor Bills created successfully for: %s") % partner_names
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Vendor Bill(s) Created'),
                    'message': message,
                    'sticky': False,
                    'type': 'success',
                }
            }
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('No Vendor Bills Created'),
                'message': _('No eligible lines found or all commissions already billed.'),
                'sticky': False,
                'type': 'warning',
            }
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

    def _inverse_commission_amount(self):
        """Safe no-op inverse for computed commission amount fields.
        This satisfies the inverse= contract on commission_amount,
        in_commission_amount, and out_commission_amount.
        Implement back-propagation only if you want manual edits on amounts
        to recompute related percentages or rules.
        """
        return

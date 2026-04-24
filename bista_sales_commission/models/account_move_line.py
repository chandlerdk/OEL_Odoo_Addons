# -*- coding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (https://www.bistasolutions.com)
#
##############################################################################

from odoo import api, fields, models, _, Command
from datetime import date
from odoo.exceptions import ValidationError, UserError
from odoo.tools.float_utils import float_compare, float_is_zero


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    commission_id = fields.Many2one('sale.commission')
    in_commission_id = fields.Many2one('sale.commission')
    out_commission_id = fields.Many2one('sale.commission')
    commission_date = fields.Date(copy=False)
    commission_move_id = fields.Many2one('account.move', string="Commission Bill", copy=False)
    reversed_move_id = fields.Many2one('account.move', related="move_id.reversed_entry_id", store=True)
    commission_move_line_id = fields.Many2one('account.move.line', string="Commission Bill Line", copy=False)
    # Vendor bill line(s) pointing to this (customer) line — inverse of commission_reverse_move_line_id.
    commission_vendor_bill_line_ids = fields.One2many(
        'account.move.line',
        'commission_reverse_move_line_id',
        string="Commission bill lines",
        copy=False,
        readonly=True,
    )
    # All distinct vendor commission bills (one per bill move; a move may have several lines to this source).
    commission_bill_ids = fields.Many2many(
        'account.move',
        string="Commission bills",
        compute="_compute_commission_bill_ids",
        copy=False,
    )
    commission_reverse_move_line_id = fields.Many2one(
        'account.move.line',
        string="Commission Reverse Move Line",
        copy=False,
        ondelete="set null",
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
        "commission_vendor_bill_line_ids",
        "commission_vendor_bill_line_ids.move_id",
    )
    def _compute_commission_bill_ids(self):
        for line in self:
            line.commission_bill_ids = [Command.set(line.commission_vendor_bill_line_ids.move_id.ids)]

    def _add_commission_bill_link(self, vendor_line):
        """Link this (customer) line to a created vendor bill line: set primary m2o once, never overwrite."""
        self.ensure_one()
        if not vendor_line or not vendor_line.move_id:
            return
        if not vendor_line.commission_reverse_move_line_id or vendor_line.commission_reverse_move_line_id != self:
            vendor_line.sudo().write({'commission_reverse_move_line_id': self.id})
        vals = {}
        if not self.commission_move_line_id:
            vals['commission_move_line_id'] = vendor_line.id
        if not self.commission_move_id:
            vals['commission_move_id'] = vendor_line.move_id.id
        if vals:
            self.sudo().write(vals)

    def _expected_commission_bill_slot_count(self):
        """How many separate commission bill lines this source line may need (man / in / out)."""
        self.ensure_one()
        n = 0
        if self.commission_id and self.commission_amount:
            n += 1
        if self.in_commission_id and self.in_commission_amount:
            n += 1
        if self.out_commission_id and self.out_commission_amount:
            n += 1
        return n

    @api.depends(
        "sale_person_id",
        "team_id",
        "price_total",
        "partner_id",
        "product_id",
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

    def generate_bill(self):
        grouped_lines = {}
        billed_partners = {}

        for line in self:
            # Map commission type → (commission rule, partner, amount)
            commission_map = [
                (line.commission_id, line.sale_rep_id, line.commission_amount),
                (line.in_commission_id, line.sale_person_id.partner_id if line.sale_person_id else False,
                 line.in_commission_amount),
                (line.out_commission_id, line.team_id.user_id.partner_id if line.team_id else False,
                 line.out_commission_amount),
            ]

            for commission_rule, partner, amount in commission_map:
                if not commission_rule or not partner or not amount:
                    continue

                key = (partner.id, commission_rule.id)

                grouped_lines.setdefault(key, []).append({
                    'line': line,
                    'amount': amount,
                    'rule': commission_rule,
                    'partner': partner,
                })

        # -------------------------------------------------------------------------
        # Bill Creation
        # -------------------------------------------------------------------------
        def create_bill(partner_id, rule_id, lines):
            """lines: list of dicts with keys line, amount, rule, partner (same as grouped)."""
            rule = self.env['sale.commission'].browse(rule_id)
            payout_account = rule.payout_account_id

            if not payout_account:
                raise UserError(
                    f"Payout account not configured on commission rule '{rule.name}'."
                )

            existing_bill = self.env['account.move'].search([
                ('partner_id', '=', partner_id),
                ('move_type', '=', 'in_invoice'),
                ('state', '=', 'draft'),
            ], limit=1)

            invoice_line_cmds = []
            for item in lines:
                cline = item['line']
                amount = item['amount']
                c_rule = item['rule']

                invoice_line_cmds.append((0, 0, {
                    'name': f"Com: {cline.move_id.name}/{cline.name}",
                    'quantity': 1,
                    'price_unit': amount,
                    'account_id': payout_account.id,
                    'commission_reverse_move_line_id': cline.id,
                    'commission_id': c_rule.id,
                }))

            if existing_bill:
                n_new = len(invoice_line_cmds)
                existing_bill.write({'invoice_line_ids': invoice_line_cmds})
                bill = existing_bill
                to_process = bill.invoice_line_ids[-n_new:]
            else:
                bill = self.env['account.move'].create({
                    'move_type': 'in_invoice',
                    'is_commission_bill': True,
                    'partner_id': partner_id,
                    'invoice_line_ids': invoice_line_cmds,
                })
                bill._set_next_sequence()
                to_process = bill.invoice_line_ids

            billed_partners[partner_id] = bill.name

            for rec, item in zip(to_process, lines):
                src = item['line']
                c_rule = item['rule']
                fix_vals = {}
                if rec.commission_reverse_move_line_id != src:
                    fix_vals['commission_reverse_move_line_id'] = src.id
                if c_rule and rec.commission_id != c_rule:
                    fix_vals['commission_id'] = c_rule.id
                if fix_vals:
                    rec.write(fix_vals)
                src._add_commission_bill_link(rec)
            return bill

        # -------------------------------------------------------------------------
        # Process groups
        # -------------------------------------------------------------------------
        for (partner_id, rule_id), lines in grouped_lines.items():
            create_bill(partner_id, rule_id, lines)

        # -------------------------------------------------------------------------
        # Notification
        # -------------------------------------------------------------------------
        if billed_partners:
            partner_names = ", ".join(billed_partners.values())
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Vendor Bill(s) Created',
                    'message': f"Vendor Bills created successfully for: {partner_names}",
                    'sticky': False,
                    'type': 'success',
                }
            }

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'No Vendor Bills Created',
                'message': 'No eligible lines found or all commissions already billed.',
                'sticky': False,
                'type': 'warning',
            }
        }

    @api.depends(
        "invoice_payment_state",
        "commission_payment_state",
        "commission_policy",
        "commission_move_id",
        "commission_move_id.state",
        "commission_vendor_bill_line_ids",
        "commission_vendor_bill_line_ids.move_id.state",
        "move_id.state",
    )
    def _get_commission_state(self):
        paid_state = ['paid', 'in_payment']

        for line in self:
            slots = line._expected_commission_bill_slot_count()
            vendor_lines = line.commission_vendor_bill_line_ids
            open_vendor = vendor_lines.filtered(
                lambda a: a.move_id and a.move_id.state not in ('cancel',)
            )
            if slots and len(open_vendor) >= slots:
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

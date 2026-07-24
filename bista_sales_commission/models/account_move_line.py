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
    epd_paid_on_line = fields.Monetary(
        string="EPD (payment j.e. share)",
        compute="_compute_epd_paid_on_line",
        currency_field="currency_id",
        help="Proportional share of early payment discount (EPD) posted on reconciled payment/statement "
        "journal entries, allocated from line untaxed / invoice untaxed.",
    )

    @api.depends(
        "move_id.epd_paid_total",
        "move_id.amount_untaxed",
        "move_id.state",
        "move_id.move_type",
        "move_id.reversed_entry_id",
        "move_id.reversed_entry_id.epd_paid_total",
        "move_id.invoice_line_ids",
        "move_id",
        "price_subtotal",
        "display_type",
        "product_id",
        "sale_line_ids",
    )
    def _compute_epd_paid_on_line(self):
        for line in self:
            line.epd_paid_on_line = 0.0
            move = line.move_id
            if not move or move.state != "posted" or not move.is_invoice(include_receipts=True):
                continue
            if line.display_type != "product":
                continue
            if line not in move.invoice_line_ids:
                continue
            cur = move.currency_id

            # Credit note: prefer matching original invoice line EPD (handles partial refunds).
            if move.move_type == "out_refund" and move.reversed_entry_id:
                origin_line = line._get_origin_invoice_line_for_epd()
                if origin_line and not cur.is_zero(origin_line.epd_paid_on_line or 0.0):
                    o_sub = abs(origin_line.price_subtotal or 0.0)
                    r_sub = abs(line.price_subtotal or 0.0)
                    if not cur.is_zero(o_sub):
                        line.epd_paid_on_line = cur.round(
                            (origin_line.epd_paid_on_line or 0.0) * (r_sub / o_sub)
                        )
                    else:
                        line.epd_paid_on_line = origin_line.epd_paid_on_line or 0.0
                    continue

            if cur.is_zero(move.epd_paid_total or 0.0):
                continue
            untaxed = move.amount_untaxed
            if cur.is_zero(untaxed):
                continue
            # Use abs so credit-note (negative) subtotals still get a positive EPD share magnitude.
            line.epd_paid_on_line = cur.round(
                (move.epd_paid_total or 0.0) * (abs(line.price_subtotal) / abs(untaxed))
            )

    def _get_origin_invoice_line_for_epd(self):
        """Best-effort match of this credit-note line to a line on the reversed invoice."""
        self.ensure_one()
        origin = self.move_id.reversed_entry_id
        if not origin:
            return self.env["account.move.line"]
        candidates = origin.invoice_line_ids.filtered(lambda l: l.display_type == "product")
        if not candidates:
            return self.env["account.move.line"]
        if self.sale_line_ids:
            matched = candidates.filtered(lambda l: l.sale_line_ids & self.sale_line_ids)
            if len(matched) == 1:
                return matched
            if matched:
                matched = matched.filtered(lambda l: l.product_id == self.product_id)
                if len(matched) == 1:
                    return matched
        matched = candidates.filtered(lambda l: l.product_id == self.product_id)
        if len(matched) == 1:
            return matched
        return self.env["account.move.line"]

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
        # float_is_zero so negative credit-note commissions still count as billable slots.
        if self.commission_id and not float_is_zero(self.commission_amount, precision_digits=2):
            n += 1
        if self.in_commission_id and not float_is_zero(self.in_commission_amount, precision_digits=2):
            n += 1
        if self.out_commission_id and not float_is_zero(self.out_commission_amount, precision_digits=2):
            n += 1
        return n

    def _get_commission_vendor_move_type(self):
        """Customer credit notes → vendor credit notes; invoices → vendor bills."""
        self.ensure_one()
        if self.move_id.move_type == 'out_refund':
            return 'in_refund'
        return 'in_invoice'

    def _get_commission_amount_bases(self):
        """Return (amount_before_tax, amount_after_tax) for commission, net of this line EPD share.

        EPD share is a positive magnitude. It reduces the commission base:
        - positive bases (invoice / typical credit note lines): subtract EPD
        - negative bases: add EPD so clawback magnitude still reflects the discount
        Credit notes usually keep positive price_subtotal and apply the sign later
        in _compute_commission_amount; EPD still must reduce that base the same way.
        """
        self.ensure_one()
        cur = self.currency_id
        if not cur:
            cur = self.env.company.currency_id
        ps = self.price_subtotal or 0.0
        pt = self.price_total or 0.0
        epd = 0.0
        if "epd_paid_on_line" in self._fields:
            epd = abs(self.epd_paid_on_line or 0.0)
        if not epd or float_is_zero(epd, precision_rounding=cur.rounding or 0.0001):
            return (ps, pt)

        negative_base = ps < 0 or pt < 0
        if not float_is_zero(pt, precision_rounding=cur.rounding or 0.0001):
            epd_untax = cur.round(epd * (abs(ps) / abs(pt)))
            if negative_base:
                at_net = cur.round(pt + epd)
                bt_net = cur.round(ps + epd_untax)
            else:
                at_net = cur.round(pt - epd)
                bt_net = cur.round(ps - epd_untax)
        else:
            if negative_base:
                bt_net = cur.round(ps + epd)
            else:
                bt_net = cur.round(ps - epd)
            at_net = pt
        return (bt_net, at_net)

    def _get_commission_calc_data(self, rule=None):
        """Data dict for sale.commission.calculate_amount; `rule` is kept for bista_rep compatibility."""
        self.ensure_one()
        b_before, b_after = self._get_commission_amount_bases()
        return {
            "product_id": self.product_id,
            "partner_id": self.partner_id,
            "quantity": self.quantity,
            "amount_after_tax": b_after,
            "amount_before_tax": b_before,
        }

    @api.depends(
        "sale_person_id",
        "team_id",
        "price_total",
        "price_subtotal",
        "epd_paid_on_line",
        "move_id.epd_paid_total",
        "partner_id",
        "product_id",
    )
    def _compute_commission_amount(self):
        for line in self:
            # Assuming invoice created from sales order always have percentage
            if not line.commission_percent:
                line.commission_amount = 0
                continue

            b_before, b_after = line._get_commission_amount_bases()
            data = {
                'product_id': line.product_id,
                'partner_id': line.partner_id,
                'quantity': line.quantity,
                'amount_after_tax': b_after,
                'amount_before_tax': b_before,
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
        created_moves = {}

        for line in self:
            if not line.commission_to_bill:
                continue
            vendor_move_type = line._get_commission_vendor_move_type()
            # Map commission type → (commission rule, partner, amount)
            commission_map = [
                (line.commission_id, line.sale_rep_id, line.commission_amount),
                (line.in_commission_id, line.sale_person_id.partner_id if line.sale_person_id else False,
                 line.in_commission_amount),
                (line.out_commission_id, line.team_id.user_id.partner_id if line.team_id and line.team_id.user_id else False,
                 line.out_commission_amount),
            ]

            for commission_rule, partner, amount in commission_map:
                if not commission_rule or not partner:
                    continue
                if float_is_zero(amount, precision_digits=2):
                    continue

                # Keep bills and vendor credit notes in separate documents.
                key = (partner.id, commission_rule.id, vendor_move_type)

                grouped_lines.setdefault(key, []).append({
                    'line': line,
                    'amount': amount,
                    'rule': commission_rule,
                    'partner': partner,
                    'vendor_move_type': vendor_move_type,
                })

        # -------------------------------------------------------------------------
        # Bill / vendor credit note creation
        # -------------------------------------------------------------------------
        def create_bill(partner_id, rule_id, vendor_move_type, lines):
            """lines: list of dicts with keys line, amount, rule, partner, vendor_move_type."""
            rule = self.env['sale.commission'].browse(rule_id)
            payout_account = rule.payout_account_id

            if not payout_account:
                raise UserError(
                    f"Payout account not configured on commission rule '{rule.name}'."
                )

            existing_bill = self.env['account.move'].search([
                ('partner_id', '=', partner_id),
                ('move_type', '=', vendor_move_type),
                ('is_commission_bill', '=', True),
                ('state', '=', 'draft'),
            ], limit=1)

            invoice_line_cmds = []
            for item in lines:
                cline = item['line']
                # Vendor credit notes use a positive unit price; sign is carried by move type.
                amount = abs(item['amount'])
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
                    'move_type': vendor_move_type,
                    'is_commission_bill': True,
                    'partner_id': partner_id,
                    'invoice_line_ids': invoice_line_cmds,
                })
                bill._set_next_sequence()
                to_process = bill.invoice_line_ids

            created_moves[bill.id] = bill.name

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
        for (partner_id, rule_id, vendor_move_type), lines in grouped_lines.items():
            create_bill(partner_id, rule_id, vendor_move_type, lines)

        # -------------------------------------------------------------------------
        # Notification
        # -------------------------------------------------------------------------
        if created_moves:
            names = ", ".join(created_moves.values())
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Vendor Bill(s) Created',
                    'message': f"Vendor Bills/Credit created successfully for: {names}",
                    'sticky': False,
                    'type': 'success',
                }
            }

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'No Vendor Documents Created',
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

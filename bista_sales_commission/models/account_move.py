from odoo import api, fields, models, Command,_
from odoo.exceptions import UserError, ValidationError

TYPE_REVERSE_MAP = {
    'entry': 'entry',
    'out_invoice': 'out_refund',
    'out_refund': 'entry',
    'in_invoice': 'in_refund',
    'in_refund': 'entry',
    'out_receipt': 'out_refund',
    'in_receipt': 'in_refund',
}

PAYMENT_STATE_SELECTION = [
    ('not_paid', 'Not Paid'),
    ('in_payment', 'In Payment'),
    ('paid', 'Paid'),
    ('partial', 'Partially Paid'),
    ('reversed', 'Reversed'),
    ('invoicing_legacy', 'Invoicing App Legacy'),
]


class AccountMove(models.Model):
    _inherit = 'account.move'

    is_commission_bill = fields.Boolean()
    commission_accrual_move_id = fields.Many2one(
        'account.move',
        string="Commission Accrual Entry",
        copy=False,
        readonly=True,
    )

    def action_view_commission_accrual(self):
        self.ensure_one()
        return {
            'name': 'Commission Accrual Entry',
            'view_mode': 'form',
            'res_model': 'account.move',
            'res_id': self.commission_accrual_move_id.id,
            'type': 'ir.actions.act_window',
        }

    commission_amount = fields.Monetary(
        string="Commission Amount",
        compute="_compute_commissions_total",
        store=True,
        currency_field="currency_id",
    )
    in_commission_amount = fields.Monetary(
        string="Commission Amount",
        compute="_compute_commissions_total",
        store=True,
        currency_field="currency_id",
    )
    out_commission_amount = fields.Monetary(
        string="Commission Amount",
        compute="_compute_commissions_total",
        store=True,
        currency_field="currency_id",
    )
    shipping_city_state = fields.Char(
        string="Ship To (City, State)",
        compute="_compute_shipping_city_state",
        store=True,
    )
    commission_move_id = fields.Many2one(
        'account.move',
        string="Related Bill",
        compute="_compute_commission_move_id",
        store=True
    )
    commission_payment_state = fields.Selection(related="invoice_line_ids.commission_payment_state", store=True,
                                                copy=False)
    commission_policy = fields.Selection([
        ('invoice', 'Invoice Generated'),
        ('payment', 'Invoice Fully Paid')
    ], compute="commission_policy_state", required=True, default="payment", readonly=True, copy=False)
    payment_date = fields.Date(
        string="Payment Date",
        compute="compute_payment_date_final",
    )
    epd_paid_total = fields.Monetary(
        string="EPD (payment j.e.)",
        compute="_compute_epd_paid",
        currency_field="currency_id",
        help="Sum of early payment discount lines (display on payment journal entry) in invoice currency, "
        "for moves reconciled with this invoice.",
    )
    epd_paid_aml_info = fields.Char(
        string="EPD (payment) detail",
        compute="_compute_epd_paid",
        help="Reconciled payment/statement entry names and EPD line amounts in invoice currency.",
    )

    def _reconciled_counterpart_epd_aml(self):
        """AMLs on counterpart moves of receivable/payable lines with EPD (from payment/statement journal)."""
        self.ensure_one()
        if not self.is_invoice(include_receipts=True) or not self:
            return self.env["account.move.line"]
        moves = self._get_reconciled_amls().move_id
        if not moves:
            return self.env["account.move.line"]
        return moves.line_ids.filtered(lambda l: l.display_type == "epd")

    def _get_move_reconciled_receivable_total(self, move):
        """Sum reconciled amounts on receivable/payable lines of ``move`` (currency amounts)."""
        total = 0.0
        recv_lines = move.line_ids.filtered(
            lambda l: l.account_id.account_type in ("asset_receivable", "liability_payable")
        )
        for rl in recv_lines:
            for partial in rl.matched_debit_ids:
                if partial.debit_move_id == rl:
                    total += abs(partial.debit_amount_currency)
                else:
                    total += abs(partial.credit_amount_currency)
            for partial in rl.matched_credit_ids:
                if partial.credit_move_id == rl:
                    total += abs(partial.credit_amount_currency)
                else:
                    total += abs(partial.debit_amount_currency)
        return total

    def _get_invoice_reconciled_amount_on_payment(self, payment_move):
        """Portion of this invoice reconciled with ``payment_move``, in invoice currency."""
        self.ensure_one()
        total = 0.0
        invoice_partials, _exchange = self._get_reconciled_invoices_partials()
        for _partial, amount, counterpart_line in invoice_partials:
            if counterpart_line.move_id == payment_move:
                total += abs(amount)
        return total

    def _get_allocated_epd_from_payment_move(self, payment_move, date_ref):
        """This invoice's proportional share of EPD lines on a reconciled payment move."""
        self.ensure_one()
        cur = self.currency_id
        if not cur:
            return 0.0
        epd_lines = payment_move.line_ids.filtered(lambda l: l.display_type == "epd")
        if not epd_lines:
            return 0.0
        pay_epd_total = self._sum_epd_amls_in_move_currency(epd_lines, self, date_ref)
        if cur.is_zero(pay_epd_total):
            return 0.0
        inv_reconciled = self._get_invoice_reconciled_amount_on_payment(payment_move)
        pay_reconciled = self._get_move_reconciled_receivable_total(payment_move)
        if cur.is_zero(pay_reconciled) or cur.is_zero(inv_reconciled):
            return 0.0
        return cur.round(pay_epd_total * (inv_reconciled / pay_reconciled))

    def _sum_epd_amls_in_move_currency(self, amls, move, date_ref):
        """Net EPD in invoice currency: sum signed EPD lines (base + tax), not sum(abs() per line).

        Odoo posts e.g. base discount 239.05 and tax 11.95; net = 227.10. Summing abs() gave 251.
        """
        self.ensure_one()
        if not amls or not move.currency_id:
            return 0.0
        ref_date = date_ref or move.invoice_date or move.date
        comp = move.company_id
        cur = move.currency_id
        total_signed = 0.0
        for aml in amls:
            if not aml or aml._name != "account.move.line":
                continue
            if aml.currency_id and aml.amount_currency is not None:
                if aml.currency_id == cur:
                    line_amt = cur.round(aml.amount_currency)
                else:
                    line_amt = cur.round(aml.currency_id._convert(aml.amount_currency, cur, comp, ref_date))
                total_signed += line_amt
            else:
                ccur = comp.currency_id
                b = aml.balance
                if ccur == cur:
                    total_signed += ccur.round(b)
                else:
                    total_signed += cur.round(
                        ccur._convert(aml.balance, cur, comp, ref_date or aml.date)
                    )
        # One positive magnitude for total discount in invoice terms (inbound / outbound safe)
        return cur.round(abs(total_signed))

    @api.depends(
        "line_ids.matched_debit_ids",
        "line_ids.matched_credit_ids",
        # debit_move_id / credit_move_id are account.move.line; EPD lines live on the move, not the aml
        "line_ids.matched_debit_ids.debit_move_id.move_id.line_ids.display_type",
        "line_ids.matched_credit_ids.credit_move_id.move_id.line_ids.display_type",
        "line_ids.matched_debit_ids.debit_move_id.move_id.line_ids.amount_currency",
        "line_ids.matched_credit_ids.credit_move_id.move_id.line_ids.amount_currency",
        "currency_id",
        "payment_state",
        "state",
        "move_type",
        "amount_untaxed",
        "reversed_entry_id",
        "reversed_entry_id.epd_paid_total",
        "reversed_entry_id.amount_untaxed",
    )
    def _compute_epd_paid(self):
        for move in self:
            if not move.is_invoice(include_receipts=True) or move.state != "posted" or not move:
                move.epd_paid_total = 0.0
                move.epd_paid_aml_info = False
                continue

            # Customer credit notes do not carry payment EPD lines. Inherit proportional EPD
            # from the reversed invoice so commission clawback matches what was accrued.
            if move.move_type == "out_refund" and move.reversed_entry_id:
                origin = move.reversed_entry_id
                origin_epd = origin.epd_paid_total or 0.0
                cur = move.currency_id
                if cur and not cur.is_zero(origin_epd):
                    o_untax = abs(origin.amount_untaxed or 0.0)
                    r_untax = abs(move.amount_untaxed or 0.0)
                    if not cur.is_zero(o_untax):
                        total = cur.round(origin_epd * (r_untax / o_untax))
                    else:
                        total = cur.round(origin_epd)
                    move.epd_paid_total = total
                    move.epd_paid_aml_info = "From %s: %s" % (origin.name, cur.format(total))
                    continue

            date_ref = move.invoice_date or move.date
            total = 0.0
            parts = []
            seen_payment_ids = set()
            for pmove in move._get_reconciled_amls().move_id:
                if pmove == move or pmove.id in seen_payment_ids:
                    continue
                seen_payment_ids.add(pmove.id)
                sub = move._get_allocated_epd_from_payment_move(pmove, date_ref)
                if move.currency_id.is_zero(sub):
                    continue
                total += sub
                if move.currency_id:
                    sub_fmt = move.currency_id.format(sub)
                else:
                    sub_fmt = str(sub)
                parts.append("%s: %s" % (pmove.name, sub_fmt))
            move.epd_paid_total = move.currency_id.round(total) if move.currency_id else total
            move.epd_paid_aml_info = " | ".join(parts) if parts else False

    @api.depends('line_ids.matched_debit_ids.debit_move_id', 'line_ids.matched_credit_ids.credit_move_id')
    def compute_payment_date_final(self):
        for move in self:
            reconciled_lines = move.line_ids.mapped('matched_debit_ids.debit_move_id') + \
                               move.line_ids.mapped('matched_credit_ids.credit_move_id')
            payment_moves = reconciled_lines.filtered(lambda m: m.payment_id)
            payment_dates = payment_moves.mapped('payment_id.date')
            move.payment_date = max(payment_dates) if payment_dates else False

    @api.depends('line_ids.commission_policy')
    def commission_policy_state(self):
        for move in self:
            move.commission_policy = move.line_ids.mapped('commission_policy')[0]

    @api.depends('line_ids.commission_move_id', 'line_ids.commission_vendor_bill_line_ids')
    def _compute_commission_move_id(self):
        for move in self:
            bills = move.line_ids.mapped('commission_vendor_bill_line_ids.move_id')
            if bills:
                move.commission_move_id = bills[:1]
            else:
                line_with = move.line_ids.filtered('commission_move_id')[:1]
                move.commission_move_id = line_with.commission_move_id

    @api.depends("partner_shipping_id.city", "partner_shipping_id.state_id")
    def _compute_shipping_city_state(self):
        for move in self:
            if move.partner_shipping_id:
                city = move.partner_shipping_id.city or ""
                state = move.partner_shipping_id.state_id.name or ""
                move.shipping_city_state = f"{city}, {state}".strip(", ")
            else:
                move.shipping_city_state = ""

    @api.depends("invoice_line_ids.commission_amount", "invoice_line_ids.in_commission_amount","invoice_line_ids.out_commission_amount")
    def _compute_commissions_total(self):
        for move in self:
            move.commission_amount = sum(move.invoice_line_ids.mapped("commission_amount"))
            move.in_commission_amount = sum(move.invoice_line_ids.mapped("in_commission_amount"))
            move.out_commission_amount = sum(move.invoice_line_ids.mapped("out_commission_amount"))

    def update_commision_on_invoice(self, records):
        # filtered_invoices = records.filtered(
        #     lambda inv: any(not line.commission_id for line in inv.invoice_line_ids)
        # )
        for invoice in records:
            for line in invoice.invoice_line_ids:
                b_before, b_after = line._get_commission_amount_bases()
                data = {
                    'product_id': line.product_id,
                    'partner_id': invoice.partner_id,
                    'quantity': line.quantity,
                    'amount_after_tax': b_after,
                    'amount_before_tax': b_before,
                    'percentage': 0
                }
                if line.product_id.detailed_type == 'service':
                    continue
                sale_commission = self.env['sale.commission']
                rules = []
                if line.sale_rep_id:
                    rules = sale_commission.search([('sale_rep_id', '=', line.sale_rep_id.id)], order='sequence')
                else:
                    user = line.move_id.user_id
                    rules = sale_commission.search([('user_ids', '=', user.id)], order='sequence')

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
            invoice_lines = self.env['account.move.line'].search([
                ('move_id', '=', invoice.id),
                ('is_commission_entry', '=', True)
            ])
            if invoice_lines:
                line_ids = tuple(invoice_lines.ids)
                self._cr.execute("DELETE FROM account_move_line WHERE id IN %s", (line_ids,))

    def button_draft(self):
        ret = super(AccountMove, self).button_draft()
        self._cancel_commission_payable()
        return ret

    def _post(self, soft=True):
        ret = super(AccountMove, self)._post(soft)
        return ret

    def _cancel_commission_payable(self):
        moves = self or self.browse(self.env.context.get("active_ids", []))
        commission_line_ids = moves.mapped("line_ids").filtered(lambda line: line.is_commission_entry)
        commission_line_ids.unlink()

    def action_open_commission_update_wizard(self):
        """Open wizard to change commission % after payment without resetting invoice to draft."""
        self.ensure_one()
        if not self.is_invoice(include_receipts=True):
            raise UserError(_("Commission update is only available on invoices."))
        if self.state != "posted":
            raise UserError(_("Commission update is only available on posted invoices."))

        # Create the transient wizard server-side so move_line_id is already stored
        # (readonly One2many columns are often dropped by the web client on save).
        line_cmds = []
        for line in self.invoice_line_ids.filtered(lambda l: l.display_type == "product"):
            line_cmds.append((0, 0, {
                "move_line_id": line.id,
                "name": line.name,
                "price_subtotal": line.price_subtotal,
                "epd_paid_on_line": line.epd_paid_on_line,
                "commission_percent": line.commission_percent,
                "in_commission_percent": line.in_commission_percent,
                "out_commission_percent": line.out_commission_percent,
                "commission_amount": line.commission_amount,
                "in_commission_amount": line.in_commission_amount,
                "out_commission_amount": line.out_commission_amount,
            }))
        wizard_vals = {
            "move_id": self.id,
            "line_ids": line_cmds,
        }
        if "sale_rep_id" in self._fields:
            wizard_vals["sale_rep_id"] = self.sale_rep_id.id
        wizard = self.env["account.move.commission.update.wizard"].create(wizard_vals)

        return {
            "name": _("Update Commission"),
            "type": "ir.actions.act_window",
            "res_model": "account.move.commission.update.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
            "context": {
                "active_id": self.id,
                "active_model": "account.move",
            },
        }

    def _cancel_commission_accrual(self):
        """Cancel linked commission accrual entry and clear the link (invoice stays posted/paid)."""
        for move in self:
            accrual = move.commission_accrual_move_id
            if not accrual:
                continue
            if accrual.state == "posted":
                accrual.button_draft()
            if accrual.state != "cancel":
                accrual.button_cancel()
            move.commission_accrual_move_id = False

    def _replace_commission_accrual(self):
        """Rebuild accrual JE from current line commission amounts.

        Used after post-payment commission corrections. Does not touch payment reconciliation.
        """
        for move in self:
            if not move.is_invoice(include_receipts=True) or move.state != "posted":
                continue
            payment_move = move._get_reconciled_amls().move_id.filtered(lambda m: m != move)[:1]
            move._cancel_commission_accrual()
            if move.payment_state in ("paid", "in_payment"):
                move._generate_commission_accrual_move(payment_move=payment_move or False)

    def _create_commission_payable(self):
        active_model = self.env.context.get("active_model")
        active_ids = self.env.context.get("active_ids", [])

        moves = self
        if not moves and active_model == 'account.move.line':
            moves = self.env['account.move.line'].browse(active_ids).mapped("move_id")
        elif not moves and active_model == 'account.move':
            moves = self.env['account.move'].browse(active_ids)
        for move in moves:
            if move.state == 'posted':
                continue

            commission_line_ids = []
            invoice_lien_ids = move.invoice_line_ids.filtered(lambda l: l.commission_id and l.commission_amount)
            # for line in invoice_lien_ids:
            #     commission_line_ids.append(
            #         move._get_commission_line_vals(line, line.commission_expense_account_id,
            #                                        debit=line.commission_amount))
            #     commission_line_ids.append(
            #         move._get_commission_line_vals(line, line.commission_payout_account_id,
            #                                        credit=line.commission_amount))
            # move.line_ids = [(0, 0, commission_line) for commission_line in commission_line_ids]

            # Do not add to self, instead we will use `_generate_commission_accrual_move`
            # This old method is kept for backwards compatibility if needed, but not used.
            # move.line_ids = [(0, 0, commission_line) for commission_line in commission_line_ids]

    def _generate_commission_accrual_move(self, payment_move=None):
        self.ensure_one()
        if not self.is_invoice(include_receipts=True):
            return False
            
        commission_line_ids = []
        for line in self.invoice_line_ids:
            # 1. Man Commission
            if line.commission_id and line.commission_amount:
                commission = line.commission_id
                commission_line_ids.append((0, 0, self._get_commission_line_vals(line, commission.expense_account_id, debit=line.commission_amount)))
                commission_line_ids.append((0, 0, self._get_commission_line_vals(line, commission.payout_account_id, credit=line.commission_amount)))

            # 2. In Commission
            if line.in_commission_id and line.in_commission_amount:
                in_commission = line.in_commission_id
                commission_line_ids.append((0, 0, self._get_commission_line_vals(line, in_commission.expense_account_id, debit=line.in_commission_amount)))
                commission_line_ids.append((0, 0, self._get_commission_line_vals(line, in_commission.payout_account_id, credit=line.in_commission_amount)))

            # 3. Out Commission
            if line.out_commission_id and line.out_commission_amount:
                out_commission = line.out_commission_id
                commission_line_ids.append((0, 0, self._get_commission_line_vals(line, out_commission.expense_account_id, debit=line.out_commission_amount)))
                commission_line_ids.append((0, 0, self._get_commission_line_vals(line, out_commission.payout_account_id, credit=line.out_commission_amount)))

        if not commission_line_ids:
            return False

        # Create the separate move for commission
        move_vals = {
            'move_type': 'entry',
            'date': payment_move.date if payment_move else self.date,
            'journal_id': self.journal_id.id,
            'ref': f"Commission Accrual: {self.name}",
            'line_ids': commission_line_ids,
        }
        accrual_move = self.env['account.move'].create(move_vals)
        accrual_move._post(soft=False)
        self.commission_accrual_move_id = accrual_move.id
        return accrual_move

    def _get_commission_line_vals(self, line, account_id, debit=0.0, credit=0.0):
        return {
            'name': f"COM Payable: {line.name}",
            'partner_id': self.partner_id.id,
            'product_id': line.product_id.id,
            'product_uom_id': line.product_uom_id.id,
            'quantity': line.quantity,
            'price_unit': line.price_unit,
            'debit': debit,
            'credit': credit,
            'account_id': account_id.id,
            'is_commission_entry': True,
            'display_type': 'product',
        }

    @api.model
    def create(self, vals_list):
        ret = super(AccountMove, self).create(vals_list)
        for move in ret:
            # Only unlink on customer invoices (e.g., duplicated invoices), not on our separate entry move
            if move.move_type in ('out_invoice', 'out_refund', 'in_invoice', 'in_refund'):
                commission_entries = move.line_ids.filtered(lambda l: l.is_commission_entry)
                commission_entries.unlink()
        return ret

from odoo import api, fields, models, Command

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

    commission_amount = fields.Monetary(
        string="Commission Amount",
        compute="_compute_commission_amount",
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

    payment_state = fields.Selection(
        selection=PAYMENT_STATE_SELECTION,
        string="Payment Status",
        compute='_compute_payment_state', store=True, readonly=True,
        copy=False,
        tracking=True,
    )
    commission_payment_state = fields.Selection(related="invoice_line_ids.commission_payment_state", store=True, copy=False)
    commission_policy = fields.Selection([
        ('invoice', 'Invoice Generated'),
        ('payment', 'Invoice Fully Paid')
    ],compute="commission_policy_state", required=True, default="payment", readonly=True, copy=False)
    payment_date = fields.Date(
        string="Payment Date",
        compute="compute_payment_date_final",
    )

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

    @api.depends('line_ids.commission_move_id')
    def _compute_commission_move_id(self):
        for move in self:
            move.commission_move_id = move.line_ids.filtered('commission_move_id')[:1].commission_move_id

    @api.depends("partner_shipping_id.city", "partner_shipping_id.state_id")
    def _compute_shipping_city_state(self):
        for move in self:
            if move.partner_shipping_id:
                city = move.partner_shipping_id.city or ""
                state = move.partner_shipping_id.state_id.name or ""
                move.shipping_city_state = f"{city}, {state}".strip(", ")
            else:
                move.shipping_city_state = ""

    @api.depends("line_ids.commission_amount")
    def _compute_commission_amount(self):
        for move in self:
            move.commission_amount = sum(move.line_ids.mapped("commission_amount"))



    def update_commision_on_invoice(self,records):
        # filtered_invoices = records.filtered(
        #     lambda inv: any(not line.commission_id for line in inv.invoice_line_ids)
        # )
        for invoice in records:
            for line in invoice.invoice_line_ids:
                data = {
                    'product_id': line.product_id,
                    'partner_id': invoice.partner_id,
                    'quantity': line.quantity,
                    'amount_after_tax': line.price_total,
                    'amount_before_tax': line.price_subtotal,
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
            invoice._create_commission_payable()

    def button_draft(self):
        ret = super(AccountMove, self).button_draft()
        self._cancel_commission_payable()
        return ret

    def _post(self, soft=True):
        self._create_commission_payable()
        ret = super(AccountMove, self)._post(soft)
        return ret

    def _cancel_commission_payable(self):
        moves = self or self.browse(self.env.context.get("active_ids", []))
        commission_line_ids = moves.mapped("line_ids").filtered(lambda line: line.is_commission_entry)
        commission_line_ids.unlink()

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
            for line in invoice_lien_ids:
                commission_line_ids.append(
                    move._get_commission_line_vals(line, line.commission_expense_account_id,
                                                   debit=line.commission_amount))
                commission_line_ids.append(
                    move._get_commission_line_vals(line, line.commission_payout_account_id,
                                                   credit=line.commission_amount))
            move.line_ids = [(0, 0, commission_line) for commission_line in commission_line_ids]

    def _get_commission_line_vals(self, line, account_id, debit=0.0, credit=0.0):
        return {
            'name': f"COM Payable: {line.name}",
            'move_id': self.id,
            'partner_id': self.partner_id.id,
            'product_id': line.product_id.id,
            'product_uom_id': line.product_uom_id.id,
            'quantity': line.quantity,
            'price_unit': line.price_unit,
            'debit': debit,
            'credit': credit,
            'account_id': account_id.id,
            'is_commission_entry': True,
            'display_type': 'cogs'
        }

    @api.model
    def create(self, vals_list):
        ret = super(AccountMove, self).create(vals_list)
        for move in ret:
            commission_entries = move.line_ids.filtered(lambda l: l.is_commission_entry)
            commission_entries.unlink()
        return ret

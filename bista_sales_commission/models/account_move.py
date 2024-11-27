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


class AccountMove(models.Model):
    _inherit = 'account.move'

    is_commission_bill = fields.Boolean()



    def update_commision_on_invoice(self,records):
        filtered_invoices = records.filtered(
            lambda inv: any(not line.commission_id for line in inv.invoice_line_ids)
        )
        for invoice in filtered_invoices:
            print("innnnnn",invoice)
            for line in invoice.invoice_line_ids.filtered(lambda l: not l.commission_id):
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
                    print("dataaaaaaaaaa",data)
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
            # if move.state == 'posted':
            #     continue

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

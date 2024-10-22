# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.


from odoo import models, fields, api, _
from odoo.tools.misc import formatLang, format_date
from num2words import num2words

INV_LINES_PER_STUB = 9

class AccountPayment(models.Model):
    _inherit = "account.payment"

    @staticmethod
    def amount_to_fraction(amount):
        """ Convert the amount to a formatted string as dollars and cents """
        if amount:
            # Split the amount into dollars and cents
            dollars, cents = divmod(round(amount * 100), 100)

            # Convert dollars to words
            dollars_in_words = num2words(dollars, to='cardinal', lang='en').replace(",", "").replace("and",
                                                                                                     "and").strip()

            # Capitalize the first letter
            dollars_in_words = dollars_in_words.capitalize()

            # Format cents to ensure two digits
            cents_str = f"{cents:02d}"

            if dollars == 0:
                return f"{cents_str}/100"
            elif cents == 0:
                return f"{dollars_in_words} and 00/100"
            else:
                return f"{dollars_in_words} and {cents_str}/100"

        return "Zero and 00/100"

    def _check_build_page_info(self, i, p):
        multi_stub = self.company_id.account_check_printing_multi_stub
        return {
            'sequence_number': self.check_number,
            'manual_sequencing': self.journal_id.check_manual_sequencing,
            'date': format_date(self.env, self.date),
            'partner_id': self.partner_id,
            'partner_name': self.partner_id.name,
            'currency': self.currency_id,
            'state': self.state,
            'amount': formatLang(self.env, self.amount, currency_obj=self.currency_id) if i == 0 else 'VOID',
            'amount_in_word': self.amount_to_fraction(self.amount),
            'memo': self.ref,
            'stub_cropped': not multi_stub and len(self.move_id._get_reconciled_invoices()) > INV_LINES_PER_STUB,
            # If the payment does not reference an invoice, there is no stub line to display
            'stub_lines': p,
        }


    def _check_make_stub_pages(self):
        """ The stub is the summary of paid invoices. It may spill on several pages, in which case only the check on
            first page is valid. This function returns a list of stub lines per page.
        """
        self.ensure_one()

        def prepare_vals(invoice, partials,account_moves):
            number = invoice.ref if invoice.ref else ''
            if invoice.is_outbound() or invoice.move_type == 'in_receipt':
                invoice_sign = 1
                partial_field = 'debit_amount_currency'
            else:
                invoice_sign = -1
                partial_field = 'credit_amount_currency'

            if invoice.currency_id.is_zero(invoice.amount_residual):
                amount_residual_str = '-'
            else:
                amount_residual_str = formatLang(self.env, invoice_sign * invoice.amount_residual,
                                                 currency_obj=invoice.currency_id)


            return {
                'due_date': format_date(self.env, invoice.invoice_date),
                'date':format_date(self.env, invoice.date),
                'account_moves': account_moves,
                'name':invoice.name,
                'ref':invoice.ref,
                'number': number,
                'amount_total': formatLang(self.env, invoice_sign * invoice.amount_total,
                                           currency_obj=invoice.currency_id),
                'amount_residual': amount_residual_str,
                'amount_paid': formatLang(self.env, invoice_sign * sum(partials.mapped(partial_field)),
                                          currency_obj=self.currency_id),
                'currency': invoice.currency_id,
            }

        # Decode the reconciliation to keep only invoices.
        term_lines = self.line_ids.filtered(
            lambda line: line.account_id.account_type in ('asset_receivable', 'liability_payable'))

        invoices = (
                    term_lines.matched_debit_ids.debit_move_id.move_id + term_lines.matched_credit_ids.credit_move_id.move_id) \
            .filtered(lambda x: x.is_outbound() or x.move_type == 'in_receipt')


        invoices = invoices.sorted(lambda x: x.invoice_date_due or x.date)

        # Group partials by invoices.
        invoice_map = {invoice: self.env['account.partial.reconcile'] for invoice in invoices}
        for partial in term_lines.matched_debit_ids:
            invoice = partial.debit_move_id.move_id
            if invoice in invoice_map:
                invoice_map[invoice] |= partial
        for partial in term_lines.matched_credit_ids:
            invoice = partial.credit_move_id.move_id
            if invoice in invoice_map:
                invoice_map[invoice] |= partial

        reconciled_lines = self.move_id.line_ids._all_reconciled_lines().filtered(
            lambda l: (l.matched_debit_ids or l.matched_credit_ids) and l.debit > 0
        )
        account_move_lines = reconciled_lines.filtered(lambda l: l.move_id.move_type != 'entry')
        account_move_ids = self.env['account.move.line'].browse(account_move_lines.ids)
        move_id = self.env['account.move']
        for line in account_move_ids:
            move_id |= line.move_id

        # Prepare stub_lines.
        if 'out_refund' in invoices.mapped('move_type'):
            stub_lines = [{'header': True, 'name': "Bills"}]
            stub_lines += [prepare_vals(invoice, partials, move_id)
                           for invoice, partials in invoice_map.items()
                           if invoice.move_type == 'in_invoice']
            stub_lines += [{'header': True, 'name': "Refunds"}]
            stub_lines += [prepare_vals(invoice, partials,move_id)
                           for invoice, partials in invoice_map.items()
                           if invoice.move_type == 'out_refund']
        else:
            stub_lines = [prepare_vals(invoice, partials,move_id)
                          for invoice, partials in invoice_map.items()
                          if invoice.move_type in ('in_invoice', 'in_receipt')]

        # Crop the stub lines or split them on multiple pages
        if not self.company_id.account_check_printing_multi_stub:
            # If we need to crop the stub, leave place for an ellipsis line
            num_stub_lines = len(stub_lines) > INV_LINES_PER_STUB and INV_LINES_PER_STUB - 1 or INV_LINES_PER_STUB
            stub_pages = [stub_lines[:num_stub_lines]]
        else:
            stub_pages = []
            i = 0
            while i < len(stub_lines):
                # Make sure we don't start the credit section at the end of a page
                if len(stub_lines) >= i + INV_LINES_PER_STUB and stub_lines[i + INV_LINES_PER_STUB - 1].get('header'):
                    num_stub_lines = INV_LINES_PER_STUB - 1 or INV_LINES_PER_STUB
                else:
                    num_stub_lines = INV_LINES_PER_STUB
                stub_pages.append(stub_lines[i:i + num_stub_lines])
                i += num_stub_lines

        return stub_pages

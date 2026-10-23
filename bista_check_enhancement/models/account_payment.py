# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import _, models
from odoo.tools.misc import formatLang, format_date
from num2words import num2words

INV_LINES_PER_STUB = 9


class AccountPayment(models.Model):
    _inherit = "account.payment"

    @staticmethod
    def amount_to_fraction(amount):
        """Convert the amount to a formatted string as dollars and cents."""
        if amount:
            dollars, cents = divmod(round(amount * 100), 100)
            dollars_in_words = num2words(dollars, to='cardinal', lang='en').replace(",", "").strip()
            dollars_in_words = dollars_in_words.capitalize()
            cents_str = f"{int(cents):02d}"

            if dollars == 0:
                return f"{cents_str}/100"
            if cents == 0:
                return f"{dollars_in_words} and 00/100"
            return f"{dollars_in_words} and {cents_str}/100"

        return "Zero and 00/100"

    def _check_build_page_info(self, i, p):
        # Extend account_check_printing and l10n_us_check_printing so blank-check
        # keys stay available, then apply this module's wording and memo.
        page = super()._check_build_page_info(i, p)
        page.update({
            'amount_in_word': self.amount_to_fraction(self.amount),
            'memo': self.memo,
        })
        return page

    def _check_make_stub_pages(self):
        """The stub is the summary of paid invoices. It may spill on several pages, in which case only the check on
        the first page is valid. This function returns a list of stub lines per page.

        Stub lines keep the keys printed by this module (date, name, ref, amount_total)
        on top of the standard check-printing values.
        """
        self.ensure_one()

        def prepare_vals(invoice, partials=None, current_amount=0):
            if invoice.is_outbound(include_receipts=True):
                invoice_sign = 1
                partial_field = 'debit_amount_currency'
            else:
                invoice_sign = -1
                partial_field = 'credit_amount_currency'

            if invoice.currency_id.is_zero(invoice.amount_residual):
                amount_residual_str = '-'
            else:
                amount_residual_str = formatLang(
                    self.env, invoice_sign * invoice.amount_residual, currency_obj=invoice.currency_id,
                )

            if current_amount:
                amount_paid = current_amount
            else:
                amount_paid = sum((partials or self.env['account.partial.reconcile']).mapped(partial_field))

            return {
                'due_date': format_date(self.env, invoice.invoice_date_due),
                'date': format_date(self.env, invoice.date),
                'name': invoice.name or '',
                'ref': invoice.ref or '',
                'number': invoice.ref or '',
                'amount_total': formatLang(
                    self.env, invoice_sign * invoice.amount_total, currency_obj=invoice.currency_id,
                ),
                'amount_residual': amount_residual_str,
                'amount_paid': formatLang(
                    self.env, invoice_sign * amount_paid, currency_obj=self.currency_id,
                ),
                'currency': invoice.currency_id,
            }

        if self.move_id:
            term_lines = self.move_id.line_ids.filtered(
                lambda line: line.account_id.account_type in ('asset_receivable', 'liability_payable')
            )
            invoices = (
                term_lines.matched_debit_ids.debit_move_id.move_id
                + term_lines.matched_credit_ids.credit_move_id.move_id
            ).filtered(lambda move: move.is_outbound(include_receipts=True))
            invoice_map = {invoice: self.env['account.partial.reconcile'] for invoice in invoices}
            for partial in term_lines.matched_debit_ids:
                invoice = partial.debit_move_id.move_id
                if invoice in invoice_map:
                    invoice_map[invoice] |= partial
            for partial in term_lines.matched_credit_ids:
                invoice = partial.credit_move_id.move_id
                if invoice in invoice_map:
                    invoice_map[invoice] |= partial
            remaining = 0
        else:
            invoices = self.invoice_ids.filtered(lambda move: move.is_outbound(include_receipts=True))
            invoice_map = {}
            remaining = self.amount

        stub_lines = []
        type_groups = {
            ('in_invoice', 'in_receipt'): _("Bills"),
            ('out_refund',): _("Refunds"),
        }
        invoices = invoices.filtered(lambda move: any(move.move_type in group for group in type_groups))
        invoices_grouped = invoices.grouped(
            lambda move: next(group for group in type_groups if move.move_type in group)
        )
        for type_group, grouped_invoices in invoices_grouped.items():
            grouped_invoices = iter(grouped_invoices.sorted(lambda move: move.invoice_date_due or move.date))
            if len(invoices_grouped) > 1:
                stub_lines.append({'header': True, 'name': type_groups[type_group]})
            if self.move_id:
                for invoice in grouped_invoices:
                    stub_lines.append(prepare_vals(invoice, partials=invoice_map[invoice]))
            else:
                while remaining and (invoice := next(grouped_invoices, None)):
                    current_amount = min(remaining, invoice.currency_id._convert(
                        invoice.amount_residual,
                        self.currency_id,
                        self.company_id,
                        self.date or invoice.date,
                    ))
                    stub_lines.append(prepare_vals(invoice, current_amount=current_amount))
                    remaining -= current_amount

        if not self.company_id.account_check_printing_multi_stub:
            num_stub_lines = INV_LINES_PER_STUB - 1 if len(stub_lines) > INV_LINES_PER_STUB else INV_LINES_PER_STUB
            stub_pages = [stub_lines[:num_stub_lines]]
        else:
            stub_pages = []
            index = 0
            while index < len(stub_lines):
                if (
                    len(stub_lines) >= index + INV_LINES_PER_STUB
                    and stub_lines[index + INV_LINES_PER_STUB - 1].get('header')
                ):
                    num_stub_lines = INV_LINES_PER_STUB - 1
                else:
                    num_stub_lines = INV_LINES_PER_STUB
                stub_pages.append(stub_lines[index:index + num_stub_lines])
                index += num_stub_lines

        return stub_pages

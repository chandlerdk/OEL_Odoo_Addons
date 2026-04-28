# -*- coding: utf-8 -*-
from odoo import api, models


class AccountMoveSend(models.AbstractModel):
    _inherit = 'account.move.send'

    @api.model
    def _prepare_invoice_pdf_report(self, invoice, invoice_data):
        """Override to use custom credit memo report for out_refund moves."""
        if invoice.invoice_pdf_report_id:
            return

        if invoice.move_type == 'out_refund':
            report_ref = 'OEL_credit_memo_report.action_report_credit_memo'
        else:
            report_ref = 'account.account_invoices'

        content, _report_format = self.env['ir.actions.report']\
            .with_company(invoice.company_id)\
            .with_context(from_account_move_send=True)\
            ._render(report_ref, invoice.ids)

        invoice_data['pdf_attachment_values'] = {
            'raw': content,
            'name': invoice._get_invoice_report_filename(),
            'mimetype': 'application/pdf',
            'res_model': invoice._name,
            'res_id': invoice.id,
            'res_field': 'invoice_pdf_report_file',
        }

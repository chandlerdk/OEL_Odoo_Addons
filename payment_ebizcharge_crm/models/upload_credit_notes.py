# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError
import logging
from io import BytesIO
import base64

_logger = logging.getLogger(__name__)


class UploadCreditNotes(models.Model):
    _name = 'upload.credit.notes'
    _description = "Upload Credit Notes"

    def _get_logs_domain(self):
        if self.ebiz_profile_id == '0':
            all_profiles = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_active', '=', True), '|', ('company_ids', '=', False),
                 ('company_ids', 'in', self._context.get('allowed_company_ids'))])
            instances = all_profiles.ids
        else:
            instances = [int(self.ebiz_profile_id)]
        return [('partner_id.ebiz_profile_id', 'in', instances)]

    def get_default_company(self):
        companies = self.env['ebizcharge.instance.config'].search(
            [('is_active', '=', True), '|', ('company_ids', '=', False), (
                'company_ids', 'in', self._context.get('allowed_company_ids'))]).mapped('company_ids').ids
        return companies

    def _get_all_instance(self):
        all_list = [("0", "All")]
        models = self.env['ebizcharge.instance.config'].search([('is_valid_credential', '=', True),
                                                                ('is_active', '=', True), '|',
                                                                ('company_ids', '=', False),
                                                                ('company_ids', 'in', self.env.companies.ids)])
        instance = [(str(model.id), model.name) for model in models]
        return all_list + instance

    name = fields.Char(default='Upload Credit Notes')
    add_filter = fields.Boolean(string='Filters')
    invoice_lines = fields.One2many('list.credit.notes', 'sync_invoice_id', copy=True, )
    logs_line = fields.One2many('logs.credit.notes', 'sync_log_id', copy=True,
                                domain=lambda self: self._get_logs_domain())
    company_ids = fields.Many2many('res.company', compute='compute_company', default=get_default_company)
    ebiz_profile_id = fields.Selection(selection=_get_all_instance)

    @api.depends('ebiz_profile_id')
    def compute_company(self):
        self.company_ids = self._context.get('allowed_company_ids')

    def js_flush_customer(self, ecom_side=None):
        rec = self.env['upload.credit.notes'].search([])
        # if rec:
        #     rec.ebiz_profile_id = False

    def create_default_records(self):
        profile_obj = self.env['ebizcharge.instance.config']
        profile = profile_obj.get_upload_instance(active_model='upload.credit.notes', active_id=self)
        if profile:
            self.ebiz_profile_id = profile
        credit_notes_obj = self.env['list.credit.notes']
        account_obj = self.env['account.move']
        if self.ebiz_profile_id == '0':
            all_profiles = profile_obj.search(
                [('is_valid_credential', '=', True), ('is_active', '=', True), '|', ('company_ids', '=', False),
                 ('company_ids', 'in', self._context.get('allowed_company_ids'))])
            list_of_invoices = account_obj.search(
                [("move_type", "=", "out_refund"), ("state", "=", 'posted'),
                 ('partner_id.ebiz_profile_id', 'in', all_profiles.ids)])
        else:
            list_of_invoices = account_obj.search(
                [("move_type", "=", "out_refund"), ("state", "=", 'posted'),
                 ('partner_id.ebiz_profile_id', '=', int(self.ebiz_profile_id))])
        credit_notes_obj.search([]).unlink()
        list_of_dict = []
        for invoice in list_of_invoices:
            is_order = credit_notes_obj.search([('invoice', '=', invoice.id)])
            if not is_order:
                list_of_dict.append((0, 0, {
                    'invoice': invoice.id,
                    'partner_id': invoice.partner_id.id,
                    'customer_id': str(invoice.partner_id.id),
                    "currency_id": self.env.user.currency_id.id,
                    'sync_invoice_id': self.id,
                }))
        if list_of_dict:
            self.invoice_lines = list_of_dict

    @api.model
    def read(self, fields, load='_classic_read'):
        self.create_default_records()
        resp = super(UploadCreditNotes, self).read(fields, load)
        return resp

    def upload_invoice(self, *args, **kwargs):
        try:
            res_ids = []
            for record in kwargs['values']:
                res_ids.append(record['id'])
            filter_record = self.env['list.credit.notes'].browse(res_ids).exists()
            if not filter_record:
                raise UserError('Please select a record first!')
            else:
                list_ids = []
                for record in filter_record:
                    list_ids.append(record.invoice_id)

                return record.invoice.with_context(
                    {'credit': 'credit_notes'}).sync_multi_customers_from_upload_invoices(list_ids)

        except Exception as e:
            raise UserError(e)

    def export_orders(self, *args, **kwargs):
        res_ids = []
        for record in kwargs['values']:
            res_ids.append(record['id'])
        filter_record = self.env['list.credit.notes'].browse(res_ids).exists()
        if not filter_record:
            raise UserError('Please select a record first!')

        column_names = ['Number', 'Customer', 'Customer ID', 'Invoice Total', 'Balance Remaining', 'Invoice Date',
                        'Due Date', 'Upload Date & Time', 'Sync Status']

        worksheet, workbook, header_style, text_center = self.env['ebizcharge.instance.config'].export_generic_method(
            sheet_name='Credit_notes',
            columns=column_names)
        i = 4
        for record in filter_record:
            worksheet[0].write(i, 1, record.invoice.name or '', text_center)
            worksheet[0].write(i, 2, record.partner_id.name or '', text_center)
            worksheet[0].write(i, 3, record.customer_id or '', text_center)
            worksheet[0].write(i, 4, record.amount_total_signed or '', text_center)
            worksheet[0].write(i, 5, record.amount_residual_signed or 0, text_center)
            worksheet[0].write(i, 6, str(record.invoice_date) if record.invoice_date else '', text_center)
            worksheet[0].write(i, 7, str(record.invoice_date_due) if record.invoice_date_due else '', text_center)
            worksheet[0].write(i, 8, str(record.last_sync_date) if record.last_sync_date else '', text_center)
            worksheet[0].write(i, 9, record.sync_status or '', text_center)
            i = i + 1

        fp = BytesIO()
        workbook.save(fp)
        export_id = self.env['bill.excel'].create(
            {'excel_file': base64.encodebytes(fp.getvalue()), 'file_name': 'Credit_notes.xls'})

        return {
            'type': 'ir.actions.act_url',
            'url': 'web/content/?model=bill.excel&field=excel_file&download=true&id=%s&filename=Credit_notes.xls' % (
                export_id.id),
            'target': 'new', }

    def export_logs(self, *args, **kwargs):
        res_ids = []
        for record in kwargs['values']:
            res_ids.append(record['id'])
        filter_record = self.env['logs.credit.notes'].browse(res_ids).exists()
        if not filter_record:
            raise UserError('Please select a record first!')

        column_names = ['Number', 'Customer', 'Customer ID', 'Invoice Total', 'Balance Remaining', 'Invoice Date',
                        'Due Date', 'Upload Date & Time', 'Sync Status']

        worksheet, workbook, header_style, text_center = self.env['ebizcharge.instance.config'].export_generic_method(
            sheet_name='Credit_notes Logs',
            columns=column_names)
        i = 4

        for record in filter_record:
            worksheet[0].write(i, 1, record.invoice.name or '', text_center)
            worksheet[0].write(i, 2, record.partner_id.name or '', text_center)
            worksheet[0].write(i, 3, record.customer_id or '', text_center)
            worksheet[0].write(i, 4, record.amount_total_signed or '', text_center)
            worksheet[0].write(i, 5, record.amount_residual_signed or 0, text_center)
            worksheet[0].write(i, 6, str(record.invoice_date) or '', text_center)
            worksheet[0].write(i, 7, str(record.invoice_date_due) or '', text_center)
            worksheet[0].write(i, 8, str(record.last_sync_date) or '', text_center)
            worksheet[0].write(i, 9, str(record.sync_status) or '', text_center)
            i = i + 1

        fp = BytesIO()
        workbook.save(fp)
        export_id = self.env['bill.excel'].create(
            {'excel_file': base64.encodebytes(fp.getvalue()), 'file_name': 'Credit_notes Logs.xls'})

        return {
            'type': 'ir.actions.act_url',
            'url': 'web/content/?model=bill.excel&field=excel_file&download=true&id=%s&filename=Credit_notes Logs.xls' % (
                export_id.id),
            'target': 'new', }

    def clear_logs(self, *args, **kwargs):
        res_ids = []
        for record in kwargs['values']:
            res_ids.append(record['id'])
        filter_record = self.env['logs.credit.notes'].browse(res_ids).exists()
        if not filter_record:
            raise UserError('Please select a record first!')
        else:
            list_of_records = []
            for record in filter_record:
                list_of_records.append(record.id)

            text = f"Are you sure you want to clear {len(kwargs['values'])} credit note(s) from the Log?"
            wizard = self.env['wizard.delete.upload.logs'].create({"record_id": self.id,
                                                                   "record_model": 'credit note',
                                                                   "text": text})
            action = self.env.ref('payment_ebizcharge_crm.wizard_delete_upload_logs').read()[0]
            action['res_id'] = wizard.id

            action['context'] = dict(
                list_of_records=list_of_records,
                model='logs.credit.notes',
            )
            return action


class ListCreditNotes(models.Model):
    _name = 'list.credit.notes'
    _description = "List Credit Notes"
    _order = 'create_date desc'

    sync_invoice_id = fields.Many2one('upload.credit.notes', string='Partner Reference', required=True,
                                      ondelete='cascade', index=True, copy=False)

    invoice = fields.Many2one('account.move', string='Number')
    invoice_id = fields.Integer(string='Invoice ID', related='invoice.id')
    state = fields.Char(string='State')
    partner_id = fields.Many2one('res.partner', string='Customer')
    customer_id = fields.Char(string='Customer ID')
    amount_total_signed = fields.Monetary(string='Amount Total', related='invoice.amount_total')
    amount_residual_signed = fields.Monetary(string='Balance Remaining', related='invoice.amount_residual')
    amount_untaxed = fields.Monetary(string='Tax Excluded', related='invoice.amount_untaxed_signed')
    currency_id = fields.Many2one("res.currency", string="Currency", readonly=True, required=True)
    invoice_date_due = fields.Date('Due Date', related='invoice.invoice_date_due')
    last_sync_date = fields.Datetime('Upload Date & Time', related='invoice.last_sync_date')
    sync_status = fields.Char('Sync Status', related='invoice.sync_response')
    invoice_date = fields.Date('Invoice Date', related='invoice.invoice_date')


class LogCreditNotes(models.Model):
    _name = 'logs.credit.notes'
    _description = "Logs Credit Notes"
    _order = 'last_sync_date desc'

    sync_log_id = fields.Many2one('upload.credit.notes', string='Partner Reference',
                                  ondelete='cascade', index=True, copy=False)
    name = fields.Char(string='Number')
    invoice = fields.Many2one('account.move', string='Invoice Number')
    partner_id = fields.Many2one("res.partner", string='Customer')
    customer_id = fields.Char(string='Customer ID')
    currency_id = fields.Many2one("res.currency", string="Currency", readonly=True, required=True)
    amount_untaxed = fields.Monetary(string='Tax Excluded')
    amount_total_signed = fields.Monetary(string='Invoice Total')
    amount_residual_signed = fields.Monetary(string='Balance Remaining')
    invoice_date_due = fields.Date('Due Date')
    invoice_date = fields.Date('Invoice Date')
    last_sync_date = fields.Datetime('Upload Date & Time')
    sync_status = fields.Char('Sync Status')

# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError
import logging
from io import BytesIO
import base64
from .ebiz_charge import message_wizard
from odoo.exceptions import ValidationError
base64.encodestring = base64.encodebytes
from datetime import datetime, timedelta

_logger = logging.getLogger(__name__)


class InvoicePaymentLinkBulk(models.Model):
    _name = 'inv.payment.link.bulk'
    _description = "Invoice Payment Link Bulk"

    def _default_get_start(self):
        return self.env['ebizcharge.instance.config'].get_document_download_start_date()

    def _default_get_end_date(self):
        today = datetime.now() + timedelta(days=1)
        return today.date()

    def get_default_company(self):
        companies = self.env['ebizcharge.instance.config'].search([('is_active', '=', True), '|', ('company_ids', '=', False),
               ('company_ids', 'in', self.env['res.company'].browse(self._context.get('allowed_company_ids')).ids)]).mapped('company_ids').ids
        return companies

    name = fields.Char(string='Generate Payment Links for Invoices', default="Generate Payment Links for Invoices")
    partner_id = fields.Many2one('res.partner', string='Select Customer', domain="[('ebiz_internal_id', '!=', False), "
                                                                                 "('ebiz_profile_id', '=', "
                                                                                 "ebiz_profile_id)]")
    invoice_lines = fields.One2many('inv.payment.link.bulk.line', 'sync_invoice_id', copy=True)
    add_filter = fields.Boolean(string='Filters')
    number = fields.Char(string='Number')
    generated_link_status = fields.Selection(
        [('both_generated_and_not_generated', 'Both Generated & Not Generated'), ('generated', 'Generated'),
         ('not_generated', 'Not Generated')],
        string='Generated Link Status', required=True, default='both_generated_and_not_generated')
    start_date = fields.Date(string='From Date')
    end_date = fields.Date(string='To Date')
    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config', string='EBizCharge Profile')
    company_ids = fields.Many2many('res.company', compute='compute_company', default=get_default_company)

    @api.depends('ebiz_profile_id')
    def compute_company(self):
        self.company_ids = self.env['res.company'].browse(self._context.get('allowed_company_ids')).ids

    def action_generate_payment_link(self, *args, **kwargs):
        try:
            if len(kwargs['values']) == 0:
                raise UserError('Please select a record first!')
            if all(bool(val['generated_link']) for val in kwargs['values'] if not bool(val['generated_link'])):
                raise UserError('A generated link already exists.')
            if any(bool(val['generated_link']) for val in kwargs['values'] if bool(val['generated_link'])):
                text = (f"Links will only be generated for selected records without existing links. Are you sure you want "
                        f"to continue?")
                wizard = self.env['wizard.generate.select.payment.link'].create({"record_id": self.id,
                                                                                 "record_model": self._name,
                                                                                 "text": text})
                action = self.env.ref('payment_ebizcharge_crm.wizard_generate_select_payment_link_action').read()[0]
                action['res_id'] = wizard.id
                action['context'] = dict(
                    self.env.context,
                    kwargs_values=kwargs['values'],
                )
                return action
            else:
                profile = False
                payment_lines = []

                if kwargs['values']:
                    vals = [val for val in kwargs['values'] if not val['generated_link']]
                    for inv in vals:
                        search_invoice = self.env['account.move'].search([('id', '=', inv['invoice'][0])])
                        if search_invoice:
                            if not search_invoice.save_payment_link:
                                payment_line = {
                                    "invoice_id": int(search_invoice.id),
                                    "name": search_invoice.name,
                                    "customer_name": search_invoice.partner_id.id,
                                    "amount_due": search_invoice.amount_residual_signed,
                                    "amount_residual_signed": search_invoice.amount_residual_signed,
                                    "amount_total_signed": search_invoice.amount_total,
                                    "request_amount": search_invoice.amount_residual_signed,
                                    "odoo_payment_link": search_invoice.odoo_payment_link,
                                    "currency_id": self.env.user.currency_id.id,
                                    "email_id": search_invoice.partner_id.email,
                                    "ebiz_profile_id": search_invoice.partner_id.ebiz_profile_id.id,
                                }
                                payment_lines.append([0, 0, payment_line])
                        profile = search_invoice.partner_id.ebiz_profile_id.id
                wiz = self.env['wizard.ebiz.generate.link.payment.bulk'].with_context(
                    profile=profile).create(
                    {'payment_lines': payment_lines,
                     'ebiz_profile_id': profile})
                action = self.env.ref('payment_ebizcharge_crm.wizard_generate_link_form_views_action').read()[0]
                action['res_id'] = wiz.id
                action['context'] = self.env.context
                return action
        except Exception as e:
            raise ValidationError(e)

    def action_remove_paylink(self, *args, **kwargs):
        try:
            if len(kwargs['values']) == 0:
                raise UserError('Please select a record first!')
            # if not len(kwargs['values']) == 1 and not kwargs['values'][0]['generated_link']:
            if not any(bool(val['generated_link']) for val in kwargs['values'] if bool(val['generated_link'])):
                raise UserError('A generated link does not exist.')
            print(any(bool(val['generated_link']) for val in kwargs['values'] if bool(val['generated_link'])))
            text = (f"Removing generated links will cancel existing payment links. A new link would need to be generated again."
                    f"\nAre you sure you want to continue?")
            wizard = self.env['wizard.exist.payment.link'].create({"record_id": self.id,
                                                                   "record_model": self._name, "text": text})
            action = self.env.ref('payment_ebizcharge_crm.wizard_exist_payment_link_action').read()[0]
            action['res_id'] = wizard.id
            action['context'] = dict(
                self.env.context,
                kwargs_values=kwargs['values'],
            )
            return action
        except Exception as e:
            raise ValidationError(e)

    def create_default_records(self):
        try:
            if self.start_date and self.end_date:
                if not self.start_date <= self.end_date:
                    self.env["inv.payment.link.bulk.line"].search([]).unlink()
                    return message_wizard('From Date should be lower than the To date!', 'Invalid Date')
            default_instance = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_default', '=', True), ('is_active', '=', True), '|',
                 ('company_ids', '=', False),
                 ('company_ids', 'in', self.env['res.company'].browse(self._context.get('allowed_company_ids')).ids)],
                limit=1)
            default_instance.action_update_profiles('inv.payment.link.bulk')
            if not self.ebiz_profile_id:
                if default_instance:
                    if not default_instance.company_ids:
                        self.ebiz_profile_id = default_instance.id
                    elif default_instance.company_ids and default_instance.company_ids.ids in self.env[
                        'res.company'].browse(
                        self._context.get('allowed_company_ids')).ids:
                        self.ebiz_profile_id = default_instance.id
                    else:
                        self.ebiz_profile_id = self.env['ebizcharge.instance.config'].search(
                            [('is_valid_credential', '=', True), ('is_active', '=', True), '|',
                             ('company_ids', '=', False),
                             ('company_ids', 'in', self.env.company.ids)], limit=1).id
                else:
                    self.ebiz_profile_id = self.env['ebizcharge.instance.config'].search(
                        [('is_valid_credential', '=', True), ('is_active', '=', True), '|', ('company_ids', '=', False),
                         ('company_ids', 'in', self.env.company.ids)], limit=1).id
                self.start_date = self._default_get_start()
                self.end_date = self._default_get_end_date()
                self.generated_link_status = 'both_generated_and_not_generated'
            invoices_filters = [('payment_state', '!=', 'paid'),
                                ('state', '=', 'posted'),
                                ('ebiz_invoice_status', '!=', 'pending'),
                                ('amount_residual', '>', 0),
                                ('move_type', 'not in', ['out_refund', 'in_invoice'])]

            if self.generated_link_status == 'generated':
                invoices_filters.append(('save_payment_link', '!=', False))

            if self.generated_link_status == 'not_generated':
                invoices_filters.append(('save_payment_link', '=', False))

            if self.end_date:
                invoices_filters.append(('date', '<=', self.end_date))

            if self.start_date:
                invoices_filters.append(('date', '>=', self.start_date))

            if self.partner_id:
                invoices_filters.append(('partner_id', '=', self.partner_id.id))

            if self.ebiz_profile_id:
                invoices_filters.append(('partner_id.ebiz_profile_id', '=', self.ebiz_profile_id.id))

            if self.number:
                invoices_filters.append(('name', 'ilike', self.number))

            invoices = self.env['account.move'].search(invoices_filters)
            if invoices:
                self.env["inv.payment.link.bulk.line"].search([]).unlink()
                list_of_trans = []
                if invoices:
                    for invoice in invoices:
                        dict1 = {
                            'invoice': invoice.id,
                            'partner_id': invoice.partner_id.id,
                            'customer_id': str(invoice.partner_id.id),
                            "currency_id": self.env.user.currency_id.id,
                            'sync_invoice_id': self.env.ref('payment_ebizcharge_crm.my_record_08').id,
                        }
                        list_of_trans.append(dict1)
                    self.env['inv.payment.link.bulk.line'].create(list_of_trans)
            else:
                self.env["inv.payment.link.bulk.line"].search([]).unlink()
        except Exception as e:
            raise ValidationError(e)

    def read(self, fields=None, load='_classic_read'):
        if self.ids:
            self.create_default_records()
        resp = super(InvoicePaymentLinkBulk, self).read(fields, load=load)
        return resp

    def export_invoices(self, *args, **kwargs):
        res_ids = []
        for record in kwargs['values']:
            res_ids.append(record['id'])
        filter_record = self.env['inv.payment.link.bulk.line'].browse(res_ids).exists()
        if not filter_record:
            raise UserError('Please select a record first!')
        column_names = ['Customer ID', 'Customer', 'Number', 'Invoice Date', 'Invoice Total', 'Balance Remaining',
                        'Request Amount', 'Generated Link']
        worksheet, workbook, header_style, text_center = self.env['ebizcharge.instance.config'].export_generic_method(sheet_name='Invoices',
                                                                                           columns=column_names)
        i = 4
        for record in filter_record:
            worksheet[0].write(i, 1, record.customer_id or '', text_center)
            worksheet[0].write(i, 2, record.partner_id.name or '', text_center)
            worksheet[0].write(i, 3, record.invoice.name or '', text_center)
            worksheet[0].write(i, 4, str(record.invoice_date) or '', text_center)
            worksheet[0].write(i, 5, record.amount_total_signed or '', text_center)
            worksheet[0].write(i, 6, record.amount_residual_signed or 0, text_center)
            worksheet[0].write(i, 7, record.request_amount or 0, text_center)
            worksheet[0].write(i, 8, record.generated_link or '', text_center)
            i = i + 1
        fp = BytesIO()
        workbook.save(fp)
        export_id = self.env['bill.excel'].create(
            {'excel_file': base64.encodestring(fp.getvalue()), 'file_name': 'Generate_Payment_Link.xls'})
        return {
            'type': 'ir.actions.act_url',
            'url': 'web/content/?model=bill.excel&field=excel_file&download=true&id=%s&filename=Generate_Payment_Link.xls' % (
                export_id.id),
            'target': 'new', }


class InvoicePaymentLinkBulkLine(models.Model):
    _name = 'inv.payment.link.bulk.line'
    _order = 'invoice asc'
    _description = "Sync Batch Processing"

    sync_invoice_id = fields.Many2one('inv.payment.link.bulk', string='Partner Reference', required=True,
                                      ondelete='cascade', index=True, copy=False)
    invoice = fields.Many2one('account.move', string='Number')
    invoice_id = fields.Integer(string='Invoice ID', related='invoice.id')
    state = fields.Char(string='State')
    partner_id = fields.Many2one('res.partner', string='Customer')
    customer_id = fields.Char(string='Customer ID')
    amount_total_signed = fields.Monetary(string='Amount Total', related='invoice.amount_total')
    amount_residual_signed = fields.Monetary(string='Balance Remaining', related="invoice.amount_residual_signed")
    amount_untaxed = fields.Monetary(string='Tax Excluded', related='invoice.amount_untaxed_signed')
    generated_link = fields.Char(string='Generated Link', related='invoice.save_payment_link')
    odoo_payment_link = fields.Boolean(string='Odoo Generated Link', related='invoice.odoo_payment_link')
    currency_id = fields.Many2one("res.currency", string="Currency", readonly=True, required=True)
    invoice_date_due = fields.Date('Due Date', related='invoice.invoice_date_due')
    last_sync_date = fields.Datetime('Upload Date & Time', related='invoice.last_sync_date')
    sync_status = fields.Char('Sync Status', related='invoice.sync_response')
    invoice_date = fields.Date('Invoice Date', related='invoice.invoice_date')
    date_time = fields.Datetime(string='Date Time')
    amount = fields.Monetary(currency_field='currency_id')
    request_amount = fields.Float(string='Request Amount', compute='_compute_req_app_amount')

    def _compute_req_app_amount(self):
        for line in self:
            if line.invoice:
                line.request_amount = line.invoice.request_amount if line.invoice.request_amount>0  else line.invoice.amount_residual
            else:
                line.request_amount = 0

    @api.onchange('request_amount')
    def check_request_amount(self):
        for rec in self:
            if rec.request_amount > rec.amount_residual_signed:
                raise UserError('Request Amount cannot be greater than the Balance Remaining.')
            elif rec.request_amount < 0:
                raise UserError('Request Amount cannot be negative.')

    @api.depends('invoice.amount_residual')
    def compute_bal_amount(self):
        for rec in self:
            rec.amount_residual_signed = rec.invoice.amount_residual - rec.request_amount if rec.invoice.amount_residual > 0 else 0

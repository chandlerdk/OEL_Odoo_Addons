# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError
import logging
from io import BytesIO
import base64

_logger = logging.getLogger(__name__)


class UploadCustomers(models.Model):
    _name = 'upload.customers'
    _description = "Upload Customers"

    def _get_all_instance(self):
        all_list = [("0", "All")]
        profiles = self.env['ebizcharge.instance.config'].search([('is_valid_credential', '=', True),
                                                                  ('is_active', '=', True), '|',
                                                                  ('company_ids', '=', False),
                                                                  ('company_ids', 'in', self.env.companies.ids)])
        instance = [(str(profile.id), profile.name) for profile in profiles]
        return all_list + instance

    def get_default_company(self):
        companies = self.env['ebizcharge.instance.config'].search(
            [('is_active', '=', True), '|', ('company_ids', '=', False), (
                'company_ids', 'in', self._context.get('allowed_company_ids'))]).mapped('company_ids').ids
        return companies

    def domain_users(self):
        return [('user_id', '=', self.env.user.id)]

    def _get_logs_domain(self):
        if self.ebiz_profile_id == '0':
            all_profiles = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_active', '=', True), '|', ('company_ids', '=', False),
                 ('company_ids', 'in', self.env.companies.ids)])
            instances = all_profiles.ids
        else:
            instances = [int(self.ebiz_profile_id)]
        return [('customer_name.ebiz_profile_id', 'in', instances)]

    logs_line = fields.One2many('logs.of.customers', 'sync_log_id', copy=True,
                                domain=lambda self: self._get_logs_domain())
    company_ids = fields.Many2many('res.company', compute='compute_company', default=get_default_company)
    name = fields.Char(default='Upload Customers')
    add_filter = fields.Boolean(string='Filters')
    transaction_history_line = fields.One2many('list.of.customers', 'sync_transaction_id', copy=True)
    ebiz_profile_id = fields.Selection(selection=_get_all_instance)

    @api.depends('ebiz_profile_id')
    def compute_company(self):
        self.company_ids = self._context.get('allowed_company_ids')

    def create_default_records(self):
        profile_obj = self.env['ebizcharge.instance.config']
        profile = profile_obj.get_upload_instance(active_model='upload.customers', active_id=self)
        if profile:
            self.ebiz_profile_id = profile
        customer_list = self.env['list.of.customers']
        self.env["list.of.customers"].search([]).unlink()
        partner_obj = self.env['res.partner']
        if self.ebiz_profile_id == '0':
            all_profiles = profile_obj.search(
                [('is_valid_credential', '=', True), ('is_active', '=', True), '|', ('company_ids', '=', False),
                 ('company_ids', 'in', self._context.get('allowed_company_ids'))])
            list_of_customers = partner_obj.search([('ebiz_profile_id', 'in', all_profiles.ids)])
        else:
            list_of_customers = partner_obj.search([('ebiz_profile_id', '=', int(self.ebiz_profile_id))])
        list_of_upload_customers = customer_list.search([])

        if (len(list_of_customers)) != (len(list_of_upload_customers)):
            list_of_dict = []
            for customer in list_of_customers:
                if customer.customer_rank > 0 and customer.active:
                    is_customer = customer_list.search([('customer_id', '=', str(customer.id))])
                    if not is_customer:
                        list_of_dict.append((0, 0, {
                            'customer_name': customer.id,
                            'customer_id': str(customer.id),
                            'sync_transaction_id': self.id,
                        }))
            if list_of_dict:
                self.transaction_history_line = list_of_dict

    @api.model
    def read(self, fields, load='_classic_read'):
        self.create_default_records()
        resp = super(UploadCustomers, self).read(fields, load)
        return resp

    def js_flush_customer(self, *args, **kwargs):
        rec = self.env['upload.customers'].search([])
        # if rec:
        #     rec.ebiz_profile_id = False

    def upload_customers(self, *args, **kwargs):
        try:
            res_ids = []
            for record in kwargs['values']:
                res_ids.append(record['id'])
            filter_record = self.env['list.of.customers'].browse(res_ids).exists()
            if not filter_record:
                raise UserError('Please select a record first!')
            else:
                list_ids = []
                for record in filter_record:
                    list_ids.append(int(record.customer_id))
                return record.customer_name.sync_multi_customers_from_upload_customers(list_ids)
        except Exception as e:
            raise UserError(e)

    def export_customers(self, *args, **kwargs):
        res_ids = []
        for record in kwargs['values']:
            res_ids.append(record['id'])
        filter_record = self.env['list.of.customers'].browse(res_ids).exists()
        if not filter_record:
            raise UserError('Please select a record first!')

        column_names = ['Customer ID #', 'Customer', 'Email', 'Phone', 'Street', 'City', 'Country',
                        'Upload Date & Time', 'Upload Status']

        worksheet, workbook, header_style, text_center = self.env['ebizcharge.instance.config'].export_generic_method(sheet_name='Customers',
                                                                               columns=column_names)
        i = 4
        for record in filter_record:
            worksheet[0].write(i, 1, record.customer_id or '', text_center)
            worksheet[0].write(i, 2, record.customer_name.name or '', text_center)
            worksheet[0].write(i, 3, record.email_id or '', text_center)
            worksheet[0].write(i, 4, record.customer_phone or '', text_center)
            worksheet[0].write(i, 5, record.street or '', text_center)
            worksheet[0].write(i, 6, record.customer_city or '', text_center)
            worksheet[0].write(i, 7, record.country.name or '', text_center)
            worksheet[0].write(i, 8, str(record.last_sync_date or ''), text_center)
            worksheet[0].write(i, 9, record.sync_status or '', text_center)
            i = i + 1

        fp = BytesIO()
        workbook.save(fp)
        export_id = self.env['bill.excel'].create(
            {'excel_file': base64.encodebytes(fp.getvalue()), 'file_name': 'Customers.xls'})
        return {
            'type': 'ir.actions.act_url',
            'url': 'web/content/?model=bill.excel&field=excel_file&download=true&id=%s&filename=Customers.xls' % (
                export_id.id),
            'target': 'new', }

    def delete_customers(self, *args, **kwargs):
        res_ids = []
        for record in kwargs['values']:
            res_ids.append(record['id'])
        filter_record = self.env['list.of.customers'].browse(res_ids).exists()
        if not filter_record:
            raise UserError('Please select a record first!')
        else:
            list_of_customer = []
            list_of_records = []
            for record in filter_record:
                ebiz_customer = self.env['res.partner'].search([('id', '=', record.customer_id)])
                if ebiz_customer.ebiz_internal_id:
                    list_of_customer.append(record.customer_id)
                    list_of_records.append(record.id)

            if list_of_customer:
                text = f"Are you sure you want to deactivate {len(kwargs['values'])} customer(s) in Odoo and EBizCharge Hub?"
                wizard = self.env['wizard.inactive.customers'].create({"record_id": self.id,
                                                                       "record_model": self._name,
                                                                       "text": text})
                action = self.env.ref('payment_ebizcharge_crm.wizard_delete_inactive_customers').read()[0]
                action['res_id'] = wizard.id

                action['context'] = dict(
                    self.env.context,
                    kwargs_values=list_of_customer,
                    list_of_records=list_of_records,
                )
                return action

            else:
                raise UserError('Selected customer(s) must be synced prior to being deactivated.')

    def clear_logs(self, *args, **kwargs):
        res_ids = []
        for record in kwargs['values']:
            res_ids.append(record['id'])
        filter_record = self.env['logs.of.customers'].browse(res_ids).exists()
        if not filter_record:
            raise UserError('Please select a record first!')
        else:
            list_of_records = []
            for record in filter_record:
                list_of_records.append(record.id)

            text = f"Are you sure you want to clear {len(kwargs['values'])} customer(s) from the Log?"
            wizard = self.env['wizard.delete.upload.logs'].create({"record_id": self.id,
                                                                   "record_model": 'customer',
                                                                   "text": text})
            action = self.env.ref('payment_ebizcharge_crm.wizard_delete_upload_logs').read()[0]
            action['res_id'] = wizard.id

            action['context'] = dict(
                list_of_records=list_of_records,
                model='logs.of.customers',
            )

            return action

    def export_logs(self, *args, **kwargs):
        res_ids = []
        for record in kwargs['values']:
            res_ids.append(record['id'])
        filter_record = self.env['logs.of.customers'].browse(res_ids).exists()
        if not filter_record:
            raise UserError('Please select a record first!')

        column_names = ['Customer ID #', 'Customer', 'Email', 'Phone', 'Upload Date & Time', 'Upload Status']
        worksheet, workbook, header_style, text_center = self.env['ebizcharge.instance.config'].export_generic_method(sheet_name='Customer Logs',
                                                                               columns=column_names)
        i = 4
        for record in filter_record:
            worksheet[0].write(i, 1, record.customer_id or '', text_center)
            worksheet[0].write(i, 2, record.customer_name.name or '', text_center)
            worksheet[0].write(i, 3, record.email_id or '', text_center)
            worksheet[0].write(i, 4, record.customer_phone or '', text_center)
            worksheet[0].write(i, 5, str(record.last_sync_date or ''), text_center)
            worksheet[0].write(i, 6, record.sync_status or '', text_center)
            i = i + 1

        fp = BytesIO()
        workbook.save(fp)
        export_id = self.env['bill.excel'].create(
            {'excel_file': base64.encodebytes(fp.getvalue()), 'file_name': 'Customer Logs.xls'})

        return {
            'type': 'ir.actions.act_url',
            'url': 'web/content/?model=bill.excel&field=excel_file&download=true&id=%s&filename=Customer Logs.xls' % (
                export_id.id),
            'target': 'new', }


class BillExcel(models.TransientModel):
    _name = "bill.excel"
    _description = "Bill Excel"

    excel_file = fields.Binary('Excel File')
    file_name = fields.Char('Excel Name', size=64)


class ListOfCustomers(models.Model):
    _name = 'list.of.customers'
    _description = "List of Customers"
    _order = 'create_date desc'

    sync_transaction_id = fields.Many2one('upload.customers', string='Partner Reference', required=True,
                                          ondelete='cascade', index=True, copy=False)
    name = fields.Char(string='Number')
    customer_name = fields.Many2one('res.partner', string='Customer')
    customer_id = fields.Char(string='Customer ID')
    email_id = fields.Char(string='Email', related='customer_name.email')
    customer_phone = fields.Char('Phone', related='customer_name.phone')
    customer_city = fields.Char('City', related='customer_name.city')
    street = fields.Char('Street', related='customer_name.street')
    country = fields.Many2one('res.country', 'Country', related='customer_name.country_id')
    sync_status = fields.Char(string='Sync Status', related='customer_name.sync_response')
    last_sync_date = fields.Datetime(string="Upload Date & Time", related='customer_name.last_sync_date')


class LogsOfCustomers(models.Model):
    _name = 'logs.of.customers'
    _description = "Logs of Customers"
    _order = 'last_sync_date desc'

    sync_log_id = fields.Many2one('upload.customers', string='Partner Reference',
                                  ondelete='cascade', index=True, copy=False)
    name = fields.Char(string='Customer')
    customer_name = fields.Many2one('res.partner', string='Customer Name')
    customer_id = fields.Char(string='Customer ID')
    email_id = fields.Char(string='Email')
    customer_phone = fields.Char('Phone')
    sync_status = fields.Char(string='Sync Status')
    last_sync_date = fields.Datetime(string="Upload Date & Time")
    street = fields.Char('Address')
    user_id = fields.Many2one('res.users', 'User')

# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError
import logging
from io import BytesIO
import base64

_logger = logging.getLogger(__name__)


class UploadProducts(models.Model):
    _name = 'upload.products'
    _description = "Upload Products"

    def _get_logs_domain(self):
        if self.ebiz_profile_id == '0':
            all_profiles = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_active', '=', True), '|', ('company_ids', '=', False),
                 ('company_ids', 'in', self._context.get('allowed_company_ids'))])
            instances = all_profiles.ids
        else:
            instances = [int(self.ebiz_profile_id)]
        return [('product_name.ebiz_profile_id', 'in', instances)]

    def _get_all_instance(self):
        all_list = [("0", "All")]
        models = self.env['ebizcharge.instance.config'].search([('is_valid_credential', '=', True),
                                                                ('is_active', '=', True), '|',
                                                                ('company_ids', '=', False),
                                                                ('company_ids', 'in', self.env.companies.ids)])
        instance = [(str(model.id), model.name) for model in models]
        return all_list + instance

    def get_default_company(self):
        companies = self.env['ebizcharge.instance.config'].search(
            [('is_active', '=', True), '|', ('company_ids', '=', False), (
                'company_ids', 'in', self._context.get('allowed_company_ids'))]).mapped('company_ids').ids
        return companies

    name = fields.Char(default='Upload Products')
    add_filter = fields.Boolean(string='Filters')
    transaction_history_line = fields.One2many('list.of.products', 'sync_transaction_id', copy=True)
    logs_line = fields.One2many('logs.of.products', 'sync_log_id', copy=True, domain=lambda self: self._get_logs_domain())
    ebiz_profile_id = fields.Selection(selection=_get_all_instance)
    company_ids = fields.Many2many('res.company', compute='compute_company', default=get_default_company)

    @api.depends('ebiz_profile_id')
    def compute_company(self):
        self.company_ids = self._context.get('allowed_company_ids')

    def js_flush_customer(self, ecom_side=None):
        rec = self.env['upload.products'].search([])
        # if rec:
        #     rec.ebiz_profile_id = False

    def create_default_records(self):
        profile_obj = self.env['ebizcharge.instance.config']
        profile = profile_obj.get_upload_instance(active_model='upload.products', active_id=self)
        if profile:
            self.ebiz_profile_id = profile
        self.env['list.of.products'].search([]).unlink()
        product_obj = self.env['product.template']
        if self.ebiz_profile_id == '0':
            all_profiles = profile_obj.search(
                [('is_valid_credential', '=', True), ('is_active', '=', True), '|', ('company_ids', '=', False),
                 ('company_ids', 'in', self._context.get('allowed_company_ids'))])
            list_of_upload_products = product_obj.search(
                [('ebiz_profile_id', 'in', all_profiles.ids)])
        else:
            list_of_upload_products = product_obj.search([('ebiz_profile_id', '=', int(self.ebiz_profile_id))])

        list_of_dict = []
        for product in list_of_upload_products:
            is_product = self.env['list.of.products'].search([('product_id', '=', product.id)])
            if not is_product:
                list_of_dict.append((0, 0, {
                    'product_name': product.id,
                    'sync_transaction_id': self.id,
                }))
        if list_of_dict:
            self.transaction_history_line = list_of_dict

    @api.model
    def read(self, fields, load='_classic_read'):
        self.create_default_records()
        resp = super(UploadProducts, self).read(fields, load)
        return resp

    def upload_products(self, *args, **kwargs):
        try:
            res_ids = []
            for record in kwargs['values']:
                res_ids.append(record['id'])
            filter_record = self.env['list.of.products'].browse(res_ids).exists()
            if not filter_record:
                raise UserError('Please select a record first!')
            else:
                odoo_products = self.env['product.template']
                list_ids = []
                for record in filter_record:
                    list_ids.append(record.product_id)
                return odoo_products.add_update_to_ebiz(list_ids)
        except Exception as e:
            raise UserError(e)

    def export_products(self, *args, **kwargs):
        res_ids = []
        for record in kwargs['values']:
            res_ids.append(record['id'])
        filter_record = self.env['list.of.products'].browse(res_ids).exists()
        if not filter_record:
            raise UserError('Please select a record first!')
        column_names = ['Product', 'Internal Reference', 'Sales Price', 'Cost', 'Quantity On Hand', 'Type',
                        'Upload Date & Time', 'Upload Status']

        worksheet, workbook, header_style, text_center = self.env['ebizcharge.instance.config'].export_generic_method(sheet_name='Products',
                                                                               columns=column_names)
        i = 4
        for record in filter_record:
            worksheet[0].write(i, 1, record.product_name.name or '', text_center)
            worksheet[0].write(i, 2, record.internal_reference or '', text_center)
            worksheet[0].write(i, 3, record.sales_price or '', text_center)
            worksheet[0].write(i, 4, record.cost or 0, text_center)
            worksheet[0].write(i, 5, record.quantity or 0, text_center)
            worksheet[0].write(i, 6, record.type or '', text_center)
            worksheet[0].write(i, 7, str(record.last_sync_date) if record.last_sync_date else '', text_center)
            worksheet[0].write(i, 8, record.sync_status or '', text_center)
            i = i + 1

        fp = BytesIO()
        workbook.save(fp)
        export_id = self.env['bill.excel'].create(
            {'excel_file': base64.encodebytes(fp.getvalue()), 'file_name': 'Products.xls'})

        return {
            'type': 'ir.actions.act_url',
            'url': 'web/content/?model=bill.excel&field=excel_file&download=true&id=%s&filename=Products.xls' % (
                export_id.id),
            'target': 'new', }

    def clear_logs(self, *args, **kwargs):
        res_ids = []
        for record in kwargs['values']:
            res_ids.append(record['id'])
        filter_record = self.env['logs.of.products'].browse(res_ids).exists()
        if not filter_record:
            raise UserError('Please select a record first!')
        else:
            list_of_records = []
            for record in filter_record:
                list_of_records.append(record.id)

            text = f"Are you sure you want to clear {len(kwargs['values'])} product(s) from the Log?"
            wizard = self.env['wizard.delete.upload.logs'].create({"record_id": self.id,
                                                                   "record_model": 'product',
                                                                   "text": text})
            action = self.env.ref('payment_ebizcharge_crm.wizard_delete_upload_logs').read()[0]
            action['res_id'] = wizard.id
            action['context'] = dict(
                list_of_records=list_of_records,
                model='logs.of.products',
            )
            return action

    def export_logs(self, *args, **kwargs):
        res_ids = []
        for record in kwargs['values']:
            res_ids.append(record['id'])
        filter_record = self.env['logs.of.products'].browse(res_ids).exists()
        if not filter_record:
            raise UserError('Please select a record first!')
        column_names = ['Product', 'Internal Reference', 'Sales Price', 'Cost', 'Quantity On Hand', 'Type',
                        'Upload Date & Time', 'Upload Status']
        worksheet, workbook, header_style, text_center = self.env['ebizcharge.instance.config'].export_generic_method(sheet_name='Products Logs',
                                                                               columns=column_names)
        i = 4
        for record in filter_record:
            worksheet[0].write(i, 1, record.product_name.name or '', text_center)
            worksheet[0].write(i, 2, record.internal_reference or '', text_center)
            worksheet[0].write(i, 3, record.sales_price or '', text_center)
            worksheet[0].write(i, 4, record.cost or 0, text_center)
            worksheet[0].write(i, 5, record.quantity or 0, text_center)
            worksheet[0].write(i, 6, record.type or '', text_center)
            worksheet[0].write(i, 7, str(record.last_sync_date) if record.last_sync_date else '', text_center)
            worksheet[0].write(i, 8, record.sync_status or '', text_center)
            i = i + 1

        fp = BytesIO()
        workbook.save(fp)
        export_id = self.env['bill.excel'].create(
            {'excel_file': base64.encodebytes(fp.getvalue()), 'file_name': 'Products Logs.xls'})

        return {
            'type': 'ir.actions.act_url',
            'url': 'web/content/?model=bill.excel&field=excel_file&download=true&id=%s&filename=Products Logs.xls' % (
                export_id.id),
            'target': 'new', }


class ListOfProducts(models.Model):
    _name = 'list.of.products'
    _description = "List of Products"
    _order = 'create_date desc'

    sync_transaction_id = fields.Many2one('upload.products', string='Product Reference', required=True,
                                          ondelete='cascade', index=True, copy=False)
    name = fields.Char(string='Number')
    product_name = fields.Many2one('product.template', string='Name')
    product_id = fields.Integer(string='Product ID', related='product_name.id')
    internal_reference = fields.Char(string='Internal Reference', related='product_name.default_code')
    sales_price = fields.Float(string='Sales Price', related='product_name.list_price')
    cost = fields.Float('Cost', related='product_name.standard_price')
    quantity = fields.Float('Quantity On Hand', related='product_name.qty_available')
    type = fields.Selection(string='Product Type', related='product_name.type')
    ebiz_product_id = fields.Char('EBiz Product Internal ID', related='product_name.ebiz_product_internal_id')
    sync_status = fields.Char(string='Sync Status', related='product_name.sync_status')
    last_sync_date = fields.Datetime(string="Upload Date & Time", related='product_name.last_sync_date')
    currency_id = fields.Many2one('res.currency', 'Currency',
                                  default=lambda self: self.env.user.company_id.currency_id.id, required=True)


class LogsOfProducts(models.Model):
    _name = 'logs.of.products'
    _description = "Logs of Products"
    _order = 'last_sync_date desc'

    sync_log_id = fields.Many2one('upload.products', string='Product Reference',
                                  ondelete='cascade', index=True, copy=False)
    name = fields.Char(string='Name')
    product_name = fields.Many2one('product.template', string='Product Name')
    product_id = fields.Integer(string='Product ID', related='product_name.id')
    sales_price = fields.Float(string='Sales Price')
    cost = fields.Float('Cost')
    internal_reference = fields.Char(string='Internal Reference')
    quantity = fields.Float('Quantity On Hand')
    type = fields.Selection([('consu', 'Consumable'), ('service', 'Service'), ('product', 'Storable Product')],
                            'Product Type')
    sync_status = fields.Char(string='Sync Status')
    last_sync_date = fields.Datetime(string="Upload Date & Time")
    currency_id = fields.Many2one('res.currency', 'Currency',
                                  default=lambda self: self.env.user.company_id.currency_id.id, required=True)
    user_id = fields.Many2one('res.users', 'User')

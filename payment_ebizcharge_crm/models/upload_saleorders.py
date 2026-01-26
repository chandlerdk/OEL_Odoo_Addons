# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError
import logging
from io import BytesIO
import base64

_logger = logging.getLogger(__name__)


class UploadSaleOrders(models.Model):
    _name = 'upload.sale.orders'
    _description = "Upload Sale Orders"

    def get_default_company(self):
        companies = self.env['ebizcharge.instance.config'].search(
            [('is_active', '=', True), '|', ('company_ids', '=', False), (
                'company_ids', 'in', self._context.get('allowed_company_ids'))]).mapped('company_ids').ids
        return companies

    @api.depends('ebiz_profile_id')
    def compute_company(self):
        self.company_ids = self._context.get('allowed_company_ids')

    def domain_users(self):
        return [('user_id', '=', self.env.user.id)]

    def _get_logs_domain(self):
        if self.ebiz_profile_id == '0':
            all_profiles = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_active', '=', True), '|', ('company_ids', '=', False),
                 ('company_ids', 'in', self._context.get('allowed_company_ids'))])
            instances = all_profiles.ids
        else:
            instances = [int(self.ebiz_profile_id)]
        return [('customer_name.ebiz_profile_id', 'in', instances)]

    def _get_all_instance(self):
        all_list = [("0", "All")]
        models = self.env['ebizcharge.instance.config'].search([('is_valid_credential', '=', True),
                                                                ('is_active', '=', True), '|',
                                                                ('company_ids', '=', False),
                                                                ('company_ids', 'in', self.env.companies.ids)])
        instance = [(str(model.id), model.name) for model in models]
        return all_list + instance

    company_ids = fields.Many2many('res.company', compute='compute_company', default=get_default_company)
    logs_line = fields.One2many('logs.of.orders', 'sync_log_id', copy=True
                                , domain=lambda self: self._get_logs_domain())
    name = fields.Char(default='Upload Sales Orders')
    add_filter = fields.Boolean(string='Filters')
    transaction_history_line = fields.One2many('list.of.orders', 'sync_transaction_id', copy=True)
    ebiz_profile_id = fields.Selection(selection=_get_all_instance)

    def js_flush_customer(self, ecom_side=None):
        rec = self.env['upload.sale.orders'].search([])
        # if rec:
        #     rec.ebiz_profile_id = False

    def create_default_records(self):
        profile_obj = self.env['ebizcharge.instance.config']
        profile = profile_obj.get_upload_instance(active_model='upload.sale.orders', active_id=self)
        if profile:
            self.ebiz_profile_id = profile
        self.env['list.of.orders'].search([]).unlink()
        list_of_upload_orders = self.env['list.of.orders'].search([])
        sale_obj = self.env['sale.order']
        if self.ebiz_profile_id == '0':
            all_profiles = profile_obj.search(
                [('is_valid_credential', '=', True), ('is_active', '=', True), '|', ('company_ids', '=', False),
                 ('company_ids', 'in', self._context.get('allowed_company_ids'))])
            list_of_orders = sale_obj.search(
                [('partner_id.ebiz_profile_id', 'in', all_profiles.ids)])
        else:
            list_of_orders = sale_obj.search([('partner_id.ebiz_profile_id', '=', int(self.ebiz_profile_id))])
        if (len(list_of_orders)) != (len(list_of_upload_orders)):
            list_of_dict = []
            for order in list_of_orders:
                is_order = self.env['list.of.orders'].search([('order_id', '=', order.id)])
                if not is_order:
                    list_of_dict.append((0, 0, {
                        'order_no': order.id,
                        'customer_name': order.partner_id.id,
                        'customer_id': str(order.partner_id.id),
                        "currency_id": self.env.user.currency_id.id,
                        'sync_transaction_id': self.id,
                    }))
            if list_of_dict:
                self.transaction_history_line = list_of_dict

    @api.model
    def read(self, fields, load='_classic_read'):
        self.create_default_records()
        resp = super(UploadSaleOrders, self).read(fields, load)
        return resp

    def upload_orders(self, *args, **kwargs):
        try:
            res_ids = []
            for record in kwargs['values']:
                res_ids.append(record['id'])
            filter_record = self.env['list.of.orders'].browse(res_ids).exists()
            if not filter_record:
                raise UserError('Please select a record first!')
            else:
                list_ids = []
                for record in filter_record:
                    list_ids.append(record.order_id)
                return record.order_no.sync_multi_customers_from_upload_saleorders(list_ids)

        except Exception as e:
            raise UserError(e)

    def export_orders(self, *args, **kwargs):
        res_ids = []
        for record in kwargs['values']:
            res_ids.append(record['id'])
        filter_record = self.env['list.of.orders'].browse(res_ids).exists()
        if not filter_record:
            raise UserError('Please select a record first!')
        column_names = ['Order Number', 'Customer', 'Customer ID', 'Order Total', 'Balance Remaining', 'Order Date',
                        'Upload Date & Time', 'Upload Status']
        worksheet, workbook, header_style, text_center = self.env['ebizcharge.instance.config'].export_generic_method(sheet_name='Sales Orders',
                                                                                         columns=column_names)
        i = 4
        for record in filter_record:
            worksheet[0].write(i, 1, record.order_no.name or '', text_center)
            worksheet[0].write(i, 2, record.customer_name.name or '', text_center)
            worksheet[0].write(i, 3, record.customer_id or '', text_center)
            worksheet[0].write(i, 4, record.amount_total or '', text_center)
            worksheet[0].write(i, 5, record.amount_due or 0, text_center)
            worksheet[0].write(i, 6, str(record.order_date) or '', text_center)
            worksheet[0].write(i, 7, str(record.last_sync_date) or '', text_center)
            worksheet[0].write(i, 8, record.sync_status or '', text_center)
            i = i + 1

        fp = BytesIO()
        workbook.save(fp)
        export_id = self.env['bill.excel'].create(
            {'excel_file': base64.encodebytes(fp.getvalue()), 'file_name': 'Sales Orders.xls'})

        return {
            'type': 'ir.actions.act_url',
            'url': 'web/content/?model=bill.excel&field=excel_file&download=true&id=%s&filename=Sales Orders.xls' % (
                export_id.id),
            'target': 'new', }

    def export_logs(self, *args, **kwargs):
        res_ids = []
        for record in kwargs['values']:
            res_ids.append(record['id'])
        filter_record = self.env['logs.of.orders'].browse(res_ids).exists()
        if not filter_record:
            raise UserError('Please select a record first!')

        column_names = ['Order Number', 'Customer', 'Customer ID', 'Order Total', 'Balance Remaining',
                        'Order Date', 'Upload Date & Time', 'Upload Status']

        worksheet, workbook, header_style, text_center = self.env['ebizcharge.instance.config'].export_generic_method(sheet_name='SalesOrders Logs',
                                                                                         columns=column_names)
        i = 4
        for record in filter_record:
            worksheet[0].write(i, 1, record.order_no.name or '', text_center)
            worksheet[0].write(i, 2, record.customer_name.name or '', text_center)
            worksheet[0].write(i, 3, record.customer_id or '', text_center)
            worksheet[0].write(i, 4, record.amount_total or '', text_center)
            worksheet[0].write(i, 5, record.amount_due or 0, text_center)
            worksheet[0].write(i, 6, str(record.order_date) or '', text_center)
            worksheet[0].write(i, 7, str(record.last_sync_date) or '', text_center)
            worksheet[0].write(i, 8, record.sync_status or '', text_center)
            i = i + 1

        fp = BytesIO()
        workbook.save(fp)
        export_id = self.env['bill.excel'].create(
            {'excel_file': base64.encodebytes(fp.getvalue()), 'file_name': 'SalesOrders Logs.xls'})

        return {
            'type': 'ir.actions.act_url',
            'url': 'web/content/?model=bill.excel&field=excel_file&download=true&id=%s&filename=SalesOrders Logs.xls' % (
                export_id.id),
            'target': 'new', }

    def clear_logs(self, *args, **kwargs):
        res_ids = []
        for record in kwargs['values']:
            res_ids.append(record['id'])
        filter_record = self.env['logs.of.orders'].browse(res_ids).exists()
        if not filter_record:
            raise UserError('Please select a record first!')
        else:
            list_of_records = []
            for record in filter_record:
                list_of_records.append(record.id)

            text = f"Are you sure you want to clear {len(kwargs['values'])} sales order(s) from the Log?"
            wizard = self.env['wizard.delete.upload.logs'].create({"record_id": self.id,
                                                                   "record_model": 'sales order',
                                                                   "text": text})
            action = self.env.ref('payment_ebizcharge_crm.wizard_delete_upload_logs').read()[0]
            action['res_id'] = wizard.id
            action['context'] = dict(
                list_of_records=list_of_records,
                model='logs.of.orders',
            )
            return action


class ListOfOrders(models.Model):
    _name = 'list.of.orders'
    _order = 'create_date desc'
    _description = "List of Orders"

    sync_transaction_id = fields.Many2one('upload.sale.orders', string='Partner Reference', required=True,
                                          ondelete='cascade', index=True, copy=False)

    order_no = fields.Many2one('sale.order', string='Order No')
    order_id = fields.Integer(string='Order Number', related="order_no.id")
    customer_name = fields.Many2one('res.partner', string='Customer')
    customer_id = fields.Char(string='Customer ID')
    amount_total = fields.Monetary(string='Order Total', related='order_no.amount_total')
    amount_due = fields.Monetary(string='Balance Remaining', related='order_no.amount_due_custom')
    currency_id = fields.Many2one("res.currency", string="Currency", readonly=True, required=True)
    order_date = fields.Datetime('Order Date', related='order_no.date_order')
    sync_status = fields.Char(string='Sync Status', related='order_no.sync_response')
    last_sync_date = fields.Datetime(string="Upload Date & Time", related='order_no.last_sync_date')


class LogsOfOrders(models.Model):
    _name = 'logs.of.orders'
    _order = 'last_sync_date desc'
    _description = "Logs of Orders"

    sync_log_id = fields.Many2one('upload.sale.orders', string='Partner Reference',
                                  ondelete='cascade', index=True, copy=False)

    order_no = fields.Many2one('sale.order', string='Order Number')
    customer_name = fields.Many2one('res.partner', string='Customer')
    customer_id = fields.Char(string='Customer ID')
    amount_total = fields.Monetary(string='Order Total')
    amount_due = fields.Monetary(string='Balance Remaining')
    currency_id = fields.Many2one("res.currency", string="Currency", readonly=True, required=True)
    order_date = fields.Datetime('Order Date')
    sync_status = fields.Char(string='Upload Status')
    last_sync_date = fields.Datetime(string="Upload Date & Time")
    user_id = fields.Many2one('res.users', 'User')

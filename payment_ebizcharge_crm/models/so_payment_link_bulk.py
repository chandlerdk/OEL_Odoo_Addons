# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
import logging
from .ebiz_charge import message_wizard
from io import BytesIO
import base64
from datetime import datetime, timedelta

base64.encodestring = base64.encodebytes

_logger = logging.getLogger(__name__)


class SaleOrderPaymentLinkBulk(models.Model):
    _name = 'sale.order.payment.link.bulk'
    _description = "Sale Order Payment Link Bulk"

    def _default_get_start(self):
        return self.env['ebizcharge.instance.config'].get_document_download_start_date()

    def _default_get_end_date(self):
        today = datetime.now() + timedelta(days=1)
        return today.date()

    def get_default_company(self):
        companies = self.env['ebizcharge.instance.config'].search(
            [('is_active', '=', True), '|', ('company_ids', '=', False), (
                'company_ids', 'in',
                self.env['res.company'].browse(self._context.get('allowed_company_ids')).ids)]).mapped(
            'company_ids').ids
        return companies

    name = fields.Char(string='Generate Payment Links for Sales Orders',
                       default="Generate Payment Links for Sales Orders")
    company_ids = fields.Many2many('res.company', compute='compute_company', default=get_default_company)
    partner_id = fields.Many2one('res.partner', string='Select Customer', domain="[('ebiz_internal_id', '!=', False), "
                                                                                 "('ebiz_profile_id', '=', "
                                                                                 "ebiz_profile_id)]")
    sale_order_lines = fields.One2many('sale.order.payment.link.bulk.line', 'sync_order_id', copy=True)
    add_filter = fields.Boolean(string='Filters')
    number = fields.Char(string='Number')
    generated_link_status = fields.Selection(
        [('both_generated_and_not_generated', 'Both Generated & Not Generated'), ('generated', 'Generated'),
         ('not_generated', 'Not Generated')],
        string='Generated Link Status', required=True, default='both_generated_and_not_generated')
    start_date = fields.Date(string='From Date')
    end_date = fields.Date(string='To Date')
    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config', string='EBizCharge Profile')

    @api.depends('ebiz_profile_id')
    def compute_company(self):
        self.company_ids = self.env['res.company'].browse(self._context.get('allowed_company_ids')).ids

    def action_generate_payment_link(self, *args, **kwargs):
        try:
            if len(kwargs['values']) == 0:
                raise UserError('Please select a record first!')
            if all(bool(val['generated_link']) for val in kwargs['values'] if not bool(val['generated_link'])):
                raise UserError('A generated link already exists. Remove the current link to proceed with generating a new link.')
            if any(bool(val['generated_link']) for val in kwargs['values'] if bool(val['generated_link'])):
                text = (f"Links will only be generated for selected records without existing links. Are you sure you want "
                        f"to continue?")
                wizard = self.env['wizard.generate.so.select.payment.link'].create({"record_id": self.id,
                                                                                    "record_model": self._name,
                                                                                    "text": text})
                action = self.env.ref('payment_ebizcharge_crm.wizard_generate_so_select_payment_link_action').read()[0]
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
                        search_so = self.env['sale.order'].search([('id', '=', inv['order_id'][0])])
                        if search_so:
                            if not search_so.save_payment_link:
                                payment_line = {
                                    "order_id": int(search_so.id),
                                    "partner_id": search_so.partner_id.id,
                                    "transaction_type": search_so.partner_id.ebiz_profile_id.gpl_pay_sale,
                                    "amount_total_signed": search_so.amount_total,
                                    "request_amount": search_so.ebiz_order_amount_residual,
                                    "so_payment_link": search_so.odoo_payment_link,
                                    "currency_id": self.env.user.currency_id.id,
                                    "email_id": search_so.partner_id.email,
                                    "ebiz_profile_id": search_so.partner_id.ebiz_profile_id.id,
                                }
                                payment_lines.append([0, 0, payment_line])
                                profile = search_so.partner_id.ebiz_profile_id.id
                    wiz = self.env['wizard.generate.so.link.payment'].with_context(
                        profile=profile).create(
                        {'payment_lines': payment_lines,
                         'ebiz_profile_id': profile})
                    action = \
                    self.env.ref('payment_ebizcharge_crm.wizard_generate_so_link_form_views_action').read()[0]
                    action['res_id'] = wiz.id
                    action['context'] = self.env.context
                    return action
        except Exception as e:
            raise ValidationError(e)

    def action_remove_payment_link(self, *args, **kwargs):
        try:
            if len(kwargs['values']) == 0:
                raise UserError('Please select a record first!')
            if not any(bool(val['generated_link']) for val in kwargs['values'] if bool(val['generated_link'])):
                raise UserError('A generated link does not exist.')
            print(any(bool(val['generated_link']) for val in kwargs['values'] if bool(val['generated_link'])))
            text = (f"Removing generated links will cancel existing payment links. A new link would need to be generated again."
                    f"\nAre you sure you want to continue?")
            wizard = self.env['wizard.so.exist.payment.link'].create({"record_id": self.id,
                                                                      "record_model": self._name,
                                                                      "text": text})
            action = self.env.ref('payment_ebizcharge_crm.wizard_so_exist_payment_link_action').read()[0]
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
                    self.env["sale.order.payment.link.bulk.line"].search([]).unlink()
                    return message_wizard('From Date should be lower than the To date!', 'Invalid Date')
            default_instance = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_default', '=', True), ('is_active', '=', True), '|',
                 ('company_ids', '=', False),
                 ('company_ids', 'in', self.env['res.company'].browse(self._context.get('allowed_company_ids')).ids)],
                limit=1)
            default_instance.action_update_profiles('sale.order.payment.link.bulk')
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

            order_filters = [('ebiz_order_amount_residual', '>', 0.0), ('invoice_status', '!=', 'invoiced'), ('state', '!=', 'cancel'), ('website_id', '=', False)]

            if self.number:
                order_filters.append(('name', 'ilike', self.number))

            if self.generated_link_status == 'generated':
                order_filters.append(('save_payment_link', '!=', False))

            if self.generated_link_status == 'not_generated':
                order_filters.append(('save_payment_link', '=', False))

            if self.end_date:
                order_filters.append(('date_order', '<=', self.end_date))

            if self.start_date:
                order_filters.append(('date_order', '>=', self.start_date))

            if self.partner_id:
                order_filters.append(('partner_id', '=', self.partner_id.id))

            if self.ebiz_profile_id:
                order_filters.append(('partner_id.ebiz_profile_id', '=', self.ebiz_profile_id.id))

            sale_orders = self.env['sale.order'].search(order_filters)
            sale_payment_link_obj = self.env["sale.order.payment.link.bulk.line"]
            if sale_orders:
                sale_payment_link_obj.search([]).unlink()
                list_of_trans = []
                if sale_orders:
                    for order in sale_orders:
                        if order.ebiz_order_amount_residual > 0.0:
                            transaction_type = self.ebiz_profile_id.gpl_pay_sale
                            if order.save_payment_link:
                                transaction_type = order.transaction_type if order.transaction_type else self.ebiz_profile_id.gpl_pay_sale
                            dict1 = {
                                'order_id': order.id,
                                'transaction_type': transaction_type,
                                'partner_id': order.partner_id.id,
                                'customer_id': order.partner_id.id,
                                "currency_id": self.env.user.currency_id.id,
                                'sync_order_id': self.id,
                            }
                            list_of_trans.append(dict1)
                    sale_payment_link_obj.create(list_of_trans)
            else:
                sale_payment_link_obj.search([]).unlink()
        except Exception as e:
            raise ValidationError(e)

    def read(self, fields, load='_classic_read'):
        self.create_default_records()
        resp = super(SaleOrderPaymentLinkBulk, self).read(fields, load)
        return resp

    def export_sale_order(self, *args, **kwargs):
        res_ids = []
        for record in kwargs['values']:
            res_ids.append(record['id'])
        filter_record = self.env['sale.order.payment.link.bulk.line'].browse(res_ids).exists()
        if not filter_record:
            raise UserError('Please select a record first!')
        column_names = ['Customer ID', 'Customer', 'Number', 'Order Date', 'Order Total', 'Balance Remaining',
                        'Request Amount', 'Generated Link']
        worksheet, workbook, header_style, text_center = self.env['ebizcharge.instance.config'].export_generic_method(sheet_name='Sale Orders',
                                                                                      columns=column_names)
        i = 4
        for record in filter_record:
            worksheet[0].write(i, 1, record.partner_id.id or '', text_center)
            worksheet[0].write(i, 2, record.partner_id.name or '', text_center)
            worksheet[0].write(i, 3, record.order_id.name or '', text_center)
            worksheet[0].write(i, 4, str(record.date_order) or '', text_center)
            worksheet[0].write(i, 5, record.amount_total_signed or '', text_center)
            worksheet[0].write(i, 6, record.amount_residual_signed or 0, text_center)
            worksheet[0].write(i, 7, record.request_amount or 0, text_center)
            worksheet[0].write(i, 8, record.generated_link or '', text_center)
            i = i + 1
        fp = BytesIO()
        workbook.save(fp)
        export_id = self.env['bill.excel'].create(
            {'excel_file': base64.encodestring(fp.getvalue()), 'file_name': 'Generate_Sale_Payment_Link.xls'})
        return {
            'type': 'ir.actions.act_url',
            'url': 'web/content/?model=bill.excel&field=excel_file&download=true&id=%s&filename'
                   '=Generate_Sale_Payment_Link.xls' % (
                       export_id.id),
            'target': 'new', }


class SaleOrderPaymentLinkBulkLine(models.Model):
    _name = 'sale.order.payment.link.bulk.line'
    _order = 'order_id asc'
    _description = "Sale Order Payment Link Bulk Line"


    sync_order_id = fields.Many2one('sale.order.payment.link.bulk', string='Partner Reference',
                                    ondelete='cascade', index=True, copy=False)
    order_id = fields.Many2one('sale.order', string='Number')
    customer_id = fields.Char(string='Customer ID', )
    partner_id = fields.Many2one('res.partner', string='Customer')
    amount_total_signed = fields.Monetary(string='Amount Total', related='order_id.amount_total')
    currency_id = fields.Many2one("res.currency", string="Currency", readonly=True)
    date_order = fields.Datetime('Quotation Date', related='order_id.date_order')
    transaction_type = fields.Selection(
        [('pre_auth', 'Pre-Auth'), ('deposit', 'Deposit'),
         ],
        string='Transaction Type' )

    date_time = fields.Datetime(string='Date Time')
    generated_link = fields.Char(string='Generated Link', related='order_id.save_payment_link')
    so_payment_link = fields.Boolean(string='Generated Links', related='order_id.odoo_payment_link')
    request_amount = fields.Float(string='Request Amount')
    amount_residual_signed = fields.Float(string='Balance Remaining', compute='compute_bal_amount')
    # transaction_type = fields.Selection(related='order_id.transaction_type')


    @api.depends('order_id.ebiz_amount_residual', 'order_id.invoice_status')
    def compute_bal_amount(self):
        for rec in self:
            rec.amount_residual_signed = rec.order_id.ebiz_order_amount_residual if rec.order_id.invoice_status != 'invoiced' else 0
            rec.request_amount = rec.order_id.request_amount if rec.order_id.request_amount > 0.0 else rec.order_id.ebiz_order_amount_residual

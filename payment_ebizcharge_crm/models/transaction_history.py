# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime, timedelta
import logging
from io import BytesIO
import base64
from .ebiz_charge import message_wizard

_logger = logging.getLogger(__name__)


class TransactionHeader(models.TransientModel):
    _name = 'transaction.header'
    _description = "Transaction Header"

    def _default_get_start(self):
        return self.env['ebizcharge.instance.config'].get_document_download_start_date()

    def _default_get_end_date(self):
        today = datetime.now() + timedelta(days=1)
        return today.date()

    def _default_location_id(self):
        return self.env['ebizcharge.instance.config']._default_instance_id()

    def get_default_company(self):
        companies = self.env['ebizcharge.instance.config'].search(
            [('is_active', '=', True), '|', ('company_ids', '=', False), (
                'company_ids', 'in', self._context.get('allowed_company_ids'))]).mapped('company_ids').ids
        return companies

    name = fields.Char(string='Transaction History', default="Transaction History")
    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config', string='EBizCharge Profile',
                                      default=_default_location_id)

    start_date = fields.Date(string='From Date', default=_default_get_start)
    end_date = fields.Date(string='To Date', default=_default_get_end_date)
    is_adjustment_field = fields.Char(string='Adjustment')
    transaction_lines = fields.One2many('transaction.history', 'transaction_id')
    is_surcharge_enabled = fields.Boolean(string="Surcharge Enabled")
    total_amount = fields.Float(string="Total Amount")
    total_payment_amount = fields.Float(string="Total Payment Amount", compute='_compute_total_amount')
    total_surcharge_amount = fields.Float(string="Total Surcharge Amount", compute='_compute_total_amount')
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.user.id)
    company_ids = fields.Many2many('res.company', compute='compute_company', default=get_default_company)
    add_filter = fields.Boolean(string='Filters')

    @api.depends('ebiz_profile_id', 'start_date', 'end_date', 'transaction_lines.amount')
    def _compute_total_amount(self):
        self.total_amount = sum(self.transaction_lines.mapped('amount'))
        self.total_payment_amount = sum(self.transaction_lines.mapped('subtotal'))
        self.total_surcharge_amount = sum(self.transaction_lines.mapped('surcharge_amount'))

    @api.depends('ebiz_profile_id')
    def compute_company(self):
        self.company_ids = self._context.get('allowed_company_ids')

    @api.model
    def default_get(self, default_fields):
        rec = super(TransactionHeader, self).default_get(default_fields)
        if 'ebiz_profile_id' in rec and 'start_date' in rec and 'end_date' in rec:
            profile_obj = self.env['ebizcharge.instance.config']
            instance = profile_obj.search(
                [('is_valid_credential', '=', True), ('is_active', '=', True)])
            if rec['ebiz_profile_id']:
                instance = profile_obj.browse([rec['ebiz_profile_id']])
            instance.action_update_profiles('transaction.header')
            start_date = rec['start_date']
            end_date = rec['end_date']
            filters_list = []
            if start_date and end_date:
                filters_list.append(
                    {'FieldName': 'created', 'ComparisonOperator': 'gt', 'FieldValue': str(start_date)})
                filters_list.append(
                    {'FieldName': 'created', 'ComparisonOperator': 'lt', 'FieldValue': str(end_date)})
            list_of_instance_trans = self.get_instance_transaction(filters_list, instance)

            list_of_transaction = [(5, 0, 0)]
            if list_of_instance_trans:
                list_of_instance_trans = sorted(list_of_instance_trans, key=lambda d: d['actual_date'], reverse=True)
                for trans in list_of_instance_trans:
                    line = (0, 0, trans)
                    list_of_transaction.append(line)
                rec.update({
                    'transaction_lines': list_of_transaction,
                })
        return rec

    def search_transaction(self):
        try:
            filters_list = []
            if not self.ebiz_profile_id:
                raise UserError('Please select an EBizCharge Merchant Account before refreshing the table.')

            if self.ebiz_profile_id:
                self.is_surcharge_enabled = self.ebiz_profile_id.is_surcharge_enabled

            if not self.start_date and not self.end_date:
                raise UserError('No Option Selected!')

            if self.start_date and self.end_date:
                if not self.start_date <= self.end_date:
                    return message_wizard('From Date should be lower than the To date!', 'Invalid Date')

            if self.start_date and self.end_date:
                start_date = datetime.combine(self.start_date, datetime.min.time())
                end_date = datetime.combine(self.end_date, datetime.max.time())
                filters_list.append(
                    {'FieldName': 'created', 'ComparisonOperator': 'gt', 'FieldValue': str(start_date)})
                filters_list.append(
                    {'FieldName': 'created', 'ComparisonOperator': 'lt', 'FieldValue': str(end_date)})
            list_of_trans = self._get_transactions_data(filters_list)
            list_of_trans = list(map(lambda x: dict(x, **{'transaction_id': self.id}), list_of_trans))
            sync_history = self.env["transaction.history"]
            if list_of_trans:
                sync_history.search([]).unlink()
                sync_history.create(list_of_trans)
            else:
                sync_history.search([]).unlink()

        except Exception as e:
            _logger.exception(e)
            raise UserError(e)

    def _get_transactions_data(self, filters):
        list_of_instance_trans = self.get_instance_transaction(filters)
        return list_of_instance_trans

    def get_instance_transaction(self, filters, ebiz_profile=None):
        try:
            list_of_trans = []
            if not ebiz_profile:
                if self.ebiz_profile_id:
                    instances = self.ebiz_profile_id
                else:
                    instances = self.env['ebizcharge.instance.config'].search(
                        [('is_valid_credential', '=', True), ('is_active', '=', True)])
            else:
                instances = ebiz_profile
            for instance in instances:
                avs_model = self.env['ebiz.avs.histry.tags']
                cvv2_model = self.env['ebiz.cvv.histry.tags']                
                ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
                params = {
                    'securityToken': ebiz._generate_security_json(),
                    'filters': {'SearchFilter': filters},
                    'matchAll': True,
                    'countOnly': False,
                    'start': 0,
                    'limit': 100000,
                    'sort': 'DateTime'
                }
                transaction_history = ebiz.client.service.SearchTransactions(**params)['Transactions']
                if transaction_history and transaction_history['TransactionObject']:
                    for transaction in transaction_history['TransactionObject']:
                        if transaction['Details']['Invoice'] not in ['Token', 'PM'] and transaction['Details'][
                            'Amount'] != 0.05 and transaction['Response']['Result'] != 'Error':
                            odoo_image = ''
                            c_type = ''
                            payment_method = False
                            if transaction['CreditCardData']['CardNumber']:
                                payment_method = c_type + ' ending in ' + transaction['CreditCardData']['CardNumber'][
                                                                          12:] if \
                                    transaction['CreditCardData']['CardNumber'] else ''
                            elif transaction['CheckData']:
                                payment_method = 'ACH ending in ' + transaction['CheckData']['Account'][-4:] if \
                                    transaction['CheckData']['Account'] else ''
                            surcharge_amount = self.get_surcharge_data(transaction)
                            partner_id = False
                            if transaction['CustomerID'] and transaction['CustomerID'].isnumeric():
                                try:
                                    partner_id = self.env['res.partner'].browse(int(transaction['CustomerID'])).exists()
                                except Exception as e:
                                    partner_id = False
                            invoice = self.env['account.move'].search([('name', '=', transaction['Details']['Invoice'])], limit=1)
                            sale = self.env['sale.order'].search([('name', '=', transaction['Details']['Invoice'])], limit=1)
                            if invoice:
                                partner_id = invoice.partner_id
                            if sale:
                                partner_id = sale.partner_id

                            email = transaction['BillingAddress']['Email'] if transaction['BillingAddress'][
                                                                          'Email'] not in [None,
                                                                                           'False'] else ''
                            if email=='':
                                email = partner_id.email if partner_id else ''
                            cust_id = ''
                            if transaction['CustomerID']:
                                cust_id = transaction['CustomerID']
                            elif partner_id:
                                cust_id = partner_id.id
                                
                                                           
                            dict1 = {
                                'partner_id': partner_id.id if partner_id else False,
                                'customer_id': cust_id,
                                'invoice_id': transaction['Details']['Invoice'],
                                'tax': transaction['Details']['Tax'], 
                                'ref_no': transaction['Response']['RefNum'],
                                'avs_resp':  transaction['Response']['AvsResultCode'] if transaction['Response']['AvsResultCode'] else 'N/A',
                                'cvv2_resp':  transaction['Response']['CardCodeResultCode'] if transaction['Response']['CardCodeResultCode'] else 'N/A',                                
                                'account_holder': transaction['AccountHolder'],
                                'date_time': transaction['DateTime'],
                                'actual_date': datetime.strptime(transaction['DateTime'], '%Y-%m-%d %H:%M:%S'),
                                "currency_id": self.env.user.currency_id.id,
                                'transaction_type': transaction['TransactionType'],
                                'batch_id': transaction['Response']['BatchRefNum'],
                                'card_no': payment_method,
                                'card_no_ecom': 'ending in ' + transaction['CreditCardData']['CardNumber'][12:] if
                                transaction['CreditCardData']['CardNumber'] else '',
                                'payment_method_icon': odoo_image,
                                'status': transaction['Response']['Status'],
                                'transaction_status': transaction['Response']['Result'],
                                'auth_code': transaction['Response']['AuthCode'],
                                'source': transaction['Source'],
                                'email_id': email,
                                'custnumber': transaction['Response']['CustNum'] or '',
                                'ebiz_profile_id': instance.id,
                                'subtotal': transaction['Response']['AuthAmount'] - surcharge_amount,
                                'surcharge_percentage': str(instance.surcharge_percentage) + '%',
                                'surcharge_amount': surcharge_amount,
                                'amount': transaction['Response']['AuthAmount'],
                            }
                            list_of_trans.append(dict1)
            return list_of_trans
        except Exception as e:
            _logger.exception(e)
            raise UserError(e)

    def get_surcharge_data(self, transaction):
        amount = 0
        if 'LineItems' in transaction and transaction['LineItems'] and 'LineItem' in transaction['LineItems'] and any(
                d['ProductName'] == 'Surcharge' for d in transaction['LineItems']['LineItem']):
            for rec in transaction['LineItems']['LineItem']:
                if rec['ProductName'] == 'Surcharge':
                    amount += float(rec['UnitPrice'])
        return amount

    def action_open_history(self):
        profile_obj = self.env['ebizcharge.instance.config']
        profile = int(profile_obj.get_upload_instance(active_model='transaction.header', active_id=self))
        if profile:
            self.ebiz_profile_id = profile
        else:
            profile = False
        rec = self.env['transaction.header'].create({'ebiz_profile_id': profile})
        if rec.ebiz_profile_id:
            rec.search_transaction()
        return {
            "name": _("Transaction Details"),
            "type": "ir.actions.act_window",
            "res_model": "transaction.header",
            "res_id": rec.id,
            'view_id': self.env.ref('payment_ebizcharge_crm.form_view_transaction_history', False).id,
            "view_mode": "form",
            "target": "inline",
        }

    def export_transactions(self, *args, **kwargs):
        if len(kwargs['values']) == 0:
            raise UserError('Please select a record first!')
        records = kwargs['values']

        column_names = ['Date & Time', 'Customer ID', 'Number', 'Email', 'Amount', 'Name on Card/Account',
                        'Payment Method', 'Transaction Type', 'Status', 'Result', 'Reference Number', 'Auth Code',
                        'Source']

        worksheet, workbook, header_style, text_center = self.env['ebizcharge.instance.config'].export_generic_method(
            sheet_name='Transaction History',
            columns=column_names)
        i = 4
        for record in records:
            worksheet[0].write(i, 1, record["date_time"] or '', text_center)
            worksheet[0].write(i, 2, record['customer_id'] or '', text_center)
            worksheet[0].write(i, 3, record['invoice_id'] or '', text_center)
            worksheet[0].write(i, 4, record['email_id'] or '', text_center)
            worksheet[0].write(i, 5, record['amount'] or 0, text_center)
            worksheet[0].write(i, 6, record['account_holder'] or '', text_center)
            worksheet[0].write(i, 7, record['card_no'] or '', text_center)
            worksheet[0].write(i, 8, record['transaction_type'] or '', text_center)
            worksheet[0].write(i, 9, record['status'] or '', text_center)
            worksheet[0].write(i, 10, record['transaction_status'] or '', text_center)
            worksheet[0].write(i, 11, record['ref_no'] or '', text_center)
            worksheet[0].write(i, 12, record['auth_code'] or '', text_center)
            worksheet[0].write(i, 13, record['source'] or '', text_center)
            i = i + 1

        fp = BytesIO()
        workbook.save(fp)
        export_id = self.env['bill.excel'].create(
            {'excel_file': base64.encodebytes(fp.getvalue()), 'file_name': 'Credit_notes.xls'})

        return {
            'type': 'ir.actions.act_url',
            'url': 'web/content/?model=bill.excel&field=excel_file&download=true&id=%s&filename=Transaction_History.xls' % (
                export_id.id),
            'target': 'new', }

    @api.model
    def get_card_type_selection(self):
        icons = self.env['payment.method'].search([]).read(['name'])
        icons_dict = {}
        for icon in icons:
            if not icon['name'][0] in icons_dict:
                icons_dict[icon['name'][0]] = icon['name']
        sel = list(icons_dict.items())
        return sel

    def action_open_email_wizard(self, *args, **kwargs):
        try:
            if len(kwargs['values']) == 0:
                raise UserError('Please select a record first!')

            filter_record = kwargs['values']
            if not all(rec['ebiz_profile_id'][0] == filter_record[0]['ebiz_profile_id'][0] for rec in
                       filter_record):
                raise UserError('Please select transaction of same EBizCharge Profile.')
            instance = self.env['ebizcharge.instance.config'].browse([filter_record[0]['ebiz_profile_id'][0]])
            transactions = filter_record
            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            receipts = ebiz.client.service.GetEmailTemplates(**{
                'securityToken': ebiz._generate_security_json(),
            })
            if receipts:
                email_obj = self.env['email.receipt']
                for template in receipts:
                    odoo_temp = email_obj.search(
                        [('receipt_id', '=', template['TemplateInternalId']), ('instance_id', '=', instance.id)])
                    if not odoo_temp:
                        if template['TemplateTypeId'] == 'TransactionReceiptMerchant' or template[
                                                        'TemplateTypeId'] == 'TransactionReceiptCustomer':
                            email_obj.create({
                                'name': template['TemplateName'],
                                'receipt_subject': template['TemplateSubject'],
                                'receipt_id': template['TemplateInternalId'],
                                'target': template['TemplateDescription'],
                                'content_type': template['TemplateTypeId'],
                                'instance_id': instance.id,
                            })
            return {'type': 'ir.actions.act_window',
                    'name': _('Email Receipt'),
                    'res_model': 'wizard.email.receipts.bulk',
                    'target': 'new',
                    "views": [[False, 'form']],
                    'context': {
                        'default_ebiz_profile_id': filter_record[0]['ebiz_profile_id'][0],
                        'transaction_ids': transactions,
                    }}
        except Exception as e:
            _logger.exception(e)
            raise UserError(e)

    def credit_or_void(self, *args, **kwargs):
        try:
            if len(kwargs['values']) == 0:
                raise UserError('Please select a record first!')

            filter_record = kwargs['values']
            list_of_success = 'Ref Num           :   Status\n\n'
            resp_lines = []
            success = 0
            failed = 0
            partner_obj = self.env['res.partner']
            ebiz_obj = self.env['ebiz.charge.api']
            profile_obj = self.env['ebizcharge.instance.config']
            for line in filter_record:
                resp_line = {}
                customer = partner_obj.browse([int(line['customer_id'])]).exists()
                if customer and customer.ebiz_profile_id:
                    instance = customer.ebiz_profile_id
                else:
                    instance = profile_obj.browse([int(line['ebiz_profile_id'][0])]).exists()

                ebiz = ebiz_obj.get_ebiz_charge_obj(instance=instance)
                # implementation of web form payment as applied
                filters_list = [] 
                current_pay = []
                filters_list.append(
                    {'FieldName': 'InvoiceNumber', 'ComparisonOperator': 'eq',
                     'FieldValue': line['invoice_id']})
                today = datetime.now()
                end = today + timedelta(days=1)
                start = today + timedelta(days=-7)
                received_payments = ebiz.client.service.SearchEbizWebFormReceivedPayments(**{
                    'securityToken': ebiz._generate_security_json(),
                    'fromPaymentRequestDateTime': str(start.date()),
                    'toPaymentRequestDateTime': str(end.date()),
                    'start': 0,
                    'limit': 10000,
                    "filters": {'SearchFilter': filters_list},
                })
                if received_payments:
                    current_pay = received_payments

                resp_line.update({
                    'customer_name': customer.name,
                    'customer_id': line['customer_id'],
                    'ref_num': line['ref_no'],
                })
                if line['transaction_status'] != "Approved" or line['transaction_type'] == "Credit":
                    continue

                if line['transaction_type'] == 'Voided Sale':
                    continue

                if line['status'] in ["Pending", "Settled", "Submitted"]:
                    if 'Check' in line['transaction_type']:

                        if line['transaction_type'] == 'Check (Credit)':
                            continue

                        if line['status'] == 'Pending':
                            command = 'Void'
                        else:
                            command = 'Credit'
                        resp = self.execute_transaction(line['ref_no'], {'command': command}, line , transaction_histry_amt=line['subtotal'], transaction_histry_tax=line['tax'])
                        if command=='Void' and resp['ResultCode'] not in ["D", "E"]:
                            trans_ids = self.env['payment.transaction'].search([('provider_reference','=',line['ref_no'])], limit=1)
                            if trans_ids.state not in ('cancel', 'error'):
                                trans_ids.state='draft'
                                trans_ids._set_canceled()
                    else:
                        if line['status'] == 'Pending':
                            command = 'Void'
                        else:
                            command = 'Credit'
                        invoice = self.env['account.move'].search([('name','=',line['invoice_id'].replace(' ',''))], limit=1)
                        sale = self.env['sale.order'].search([('name','=',line['invoice_id'].replace(' ', ''))], limit=1)
                        resp = ebiz.execute_transaction(line['ref_no'], {'command': command},
                                                        transaction_histry_amt=line['subtotal'], transaction_histry_tax=line['tax'], invoice=invoice, sale=sale)

                        if command=='Void' and resp['ResultCode'] not in ["D", "E"]:
                            trans_ids = self.env['payment.transaction'].search([('provider_reference','=',line['ref_no'])], limit=1)
                            if trans_ids.state not in ('cancel', 'error') and invoice.payment_state not in ('not_paid','reversed','invoicing_legacy'):
                                trans_ids.state='draft' 
                                trans_ids._set_canceled()
                else:
                    continue

                if resp['ResultCode'] == 'A' and  command=='Credit':
                    self.action_create_credit_note(line)
                    for payment_ebiz in current_pay:
                        if line['source'] in ('email pay','Email Pay', 'Email Payment Form', 'email payment form') and current_pay!=False:
                            resp = ebiz.client.service.MarkEbizWebFormPaymentAsApplied(**{
                               'securityToken': ebiz._generate_security_json(),
                               'paymentInternalId': payment_ebiz.PaymentInternalId,
                            })
                            invoice = self.env['account.move'].search([('name','=', line['invoice_id'].replace(' ', '')) ], limit=1)
                            sale = self.env['sale.order'].search([('name','=', line['invoice_id'].replace(' ', '')) ], limit=1)
                            if invoice:
                                invoice.save_payment_link = False
                                invoice.request_amount = 0
                                invoice.last_request_amount = 0  
                            if sale:
                                sale.save_payment_link = False
                                sale.request_amount = 0
                                sale.last_request_amount = 0 

                    list_of_success += f'{line["ref_no"]}     :   Success\n'
                    resp_line['status'] = 'Success'
                    resp_line['type'] = command
                    success += 1
                elif resp['ResultCode'] == 'A':
                    #self.action_create_credit_note(line)
                    for payment_ebiz in current_pay:
                        if line['source'] in ('email pay','Email Pay', 'Email Payment Form', 'email payment form') and current_pay!=False:
                            resp = ebiz.client.service.MarkEbizWebFormPaymentAsApplied(**{
                               'securityToken': ebiz._generate_security_json(),
                               'paymentInternalId': payment_ebiz.PaymentInternalId,
                            })
                            sale = self.env['sale.order'].search([('name','=', line['invoice_id'].replace(' ', '')) ], limit=1)
                            if invoice:
                                invoice.save_payment_link = False
                                invoice.request_amount = 0
                                invoice.last_request_amount = 0  
                            if sale:
                                sale.save_payment_link = False
                                sale.request_amount = 0
                                sale.last_request_amount = 0   

                    list_of_success += f'{line["ref_no"]}     :   Success\n'
                    resp_line['status'] = 'Success'
                    resp_line['type'] = command
                    success += 1
                else:
                    list_of_success += f'{line["ref_no"]}     :   Failed ({resp["Error"]})!\n'
                    resp_line['status'] = 'Failed due to '+ resp["Error"]
                    resp_line['type'] = command
                    failed += 1

                resp_lines.append([0, 0, resp_line])

            if success == 0 and failed == 0:
                raise UserError('Please select valid transaction for credit or void.')

            wizard = self.env['wizard.transaction.history.message'].create({'name': 'Message', 'lines_ids': resp_lines,
                                                                            'success_count': success,
                                                                            'failed_count': failed, })
            if self:
                self.search_transaction()
            return {
                'type': 'ir.actions.act_window',
                'name': _('Credit/Void'),
                'res_model': 'wizard.transaction.history.message',
                'res_id': wizard.id,
                'target': 'new',
                'view_mode': 'form',
                'views': [[False, 'form']],
                'context': self._context
            }

        except Exception as e:
            _logger.exception(e)
            raise UserError(e)

    def action_create_credit_note(self, transaction):
        try:
            invoice = self.env['account.move'].search([('name', '=', transaction['invoice_id'])], limit=1)
            if invoice:
                refund_wizard = self.env['account.move.reversal'].with_context(
                    active_model='account.move', active_ids=invoice.ids).create({
                        'reason': 'EBizCharge Refund',
                        'journal_id': invoice.journal_id.id,
                })
                res = refund_wizard.reverse_moves()
                refund = self.env['account.move'].browse(res['res_id'])
                refund.action_post()
                self.env['account.payment.register'].with_context(from_transaction_history=True, active_model='account.move',
                                                                  active_ids=refund.ids).create(
                                                                    {'payment_date': refund.date, })._create_payments()

            else:
                provider = self.env['payment.provider'].search(
                    [('company_id', '=', self.env.company.id), ('code', '=', 'ebizcharge')])
                if provider:
                    invoice_line_vals = [(0, 0, {
                        'name': transaction['invoice_id'] + '-' + "EBizCharge",
                        'quantity': 1,
                        'price_unit': transaction['amount'],
                    })]
                    move_vals = {
                        'move_type': 'out_refund',
                        'partner_id': int(transaction['customer_id']),
                        'invoice_date': fields.Date.today(),
                        'payment_reference': transaction['invoice_id'] + '-' + "EBizCharge",
                        'invoice_line_ids': invoice_line_vals,
                    }
                    refund = self.env['account.move'].create(move_vals)
                    refund.action_post()
                    self.env['account.payment.register'].with_context(from_transaction_history=True,
                                                                      active_model='account.move',
                                                                      active_ids=refund.ids).create(
                        {'payment_date': refund.date, 'journal_id': provider.journal_id.id})._create_payments()
            return True
        except Exception as e:
            return False

    def execute_transaction(self, ref_num, kwargs, trans_id, transaction_histry_tax=None, transaction_histry_amt=None):
        partner = self.env['res.partner'].browse([int(trans_id['customer_id'])])
        try:
            instance = None
            if partner.ebiz_profile_id:
                instance = partner.ebiz_profile_id
            if instance:
                ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
                params = {
                    'securityToken': ebiz._generate_security_json(),
                    'tran': {
                        'Command': kwargs['command'],
                        'Details': self._get_transaction_details(trans_id, transaction_histry_tax=transaction_histry_tax, transaction_histry_amt=transaction_histry_amt),
                        'RefNum': ref_num,
                        'IsRecurring': False,
                        'IgnoreDuplicate': False,
                        'CustReceipt': True,
                        "CustomerID": partner.id,
                    }
                }
                return ebiz.client.service.runTransaction(**params)
        except Exception as e:
            raise UserError(e)

    def _get_transaction_details(self, trans_id, transaction_histry_tax=None, transaction_histry_amt=None):
        return {
            'OrderID': "",
            'Invoice': trans_id['invoice_id'],
            'PONum': "",
            'Description': 'CheckCredit',
            'Amount': trans_id['amount'],
            'Tax': transaction_histry_tax if transaction_histry_tax!=None else 0,
            'Shipping': 0,
            'Discount': 0,
            'Subtotal': transaction_histry_amt if transaction_histry_amt!=None else trans_id['amount'],
            'AllowPartialAuth': False,
            'Tip': 0,
            'NonTax': True,
            'Duty': 0
        }

    def _get_customer_address(self, partner):
        name_array = partner.name.split(' ')
        first_name = name_array[0]
        if len(name_array) >= 2:
            last_name = " ".join(name_array[1:])
        else:
            last_name = ""
        address = {
            "FirstName": first_name,
            "LastName": last_name,
            "CompanyName": partner.name if partner.company_type == "company" else partner.parent_id.name or "",
            "Address1": partner.street or "",
            "Address2": partner.street2 or "",
            "City": partner.city or "",
            "State": partner.state_id.code or "",
            "ZipCode": partner.zip or "",
            "Country": partner.country_id.code or "US"
        }
        return address

    def run_customer_transaction_without_invoice(self, trans_id, command, method_id):
        instance = None
        partner = self.env['res.partner'].browse([int(trans_id['customer_id'])])
        if partner.ebiz_profile_id:
            instance = partner.ebiz_profile_id
        ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
        payment_token = self.env['payment.transaction'].sudo().search([('provider_reference', '=', trans_id['ref_no'])]).token_id
        params = {
            "securityToken": ebiz._generate_security_json(),
            "custNum": trans_id['custnumber'],
            "paymentMethodID": method_id,
            "tran": {
                "isRecurring": False,
                "IgnoreDuplicate": False,
                "Software": 'Odoo CRM',
                "MerchReceipt": True,
                "CustReceiptName": '',
                "CustReceiptEmail": '',
                "CustReceipt": False,
                "ClientIP": '',
                "CardCode": payment_token.card_code,
                "Command": command,
                "Details": {
                    'OrderID': "",
                    'Invoice': trans_id['invoice_id'],
                    'PONum': "",
                    'Description': command,
                    'Amount': trans_id['amount'],
                    'Tax': 0,
                    'Shipping': 0,
                    'Discount': 0,
                    'Subtotal': trans_id['amount'],
                    'AllowPartialAuth': False,
                    'Tip': 0,
                    'NonTax': True,
                    'Duty': 0
                },
            },
        }
        return ebiz.client.service.runCustomerTransaction(**params)

    def _get_filter_object(self, field_name, operator, value):
        return {
            'FieldName': field_name,
            'ComparisonOperator': operator,
            'FieldValue': value
        }

class TransactionHistory(models.TransientModel):
    _name = 'transaction.history'
    _order = 'actual_date desc'
    _description = "Transaction History"

    start_date = fields.Date(string='From Date')
    end_date = fields.Date(string='To Date')
    transaction_id = fields.Many2one('transaction.header')
    partner_id = fields.Many2one('res.partner')
    batch_id = fields.Char('Batch ID')
    select_date = fields.Date(string='Select Date')
    add_filter = fields.Boolean(string='Filters')
    customer_id = fields.Char(string='Customer ID')
    invoice_id = fields.Char(string='Number')
    tax = fields.Char(string='Tax')
    ref_no = fields.Char(string='Reference Number')
    avs_resp = fields.Char( string='AVS')
    cvv2_resp = fields.Char(string='CVV2')    
    account_holder = fields.Char(string='Name On Card/Account')
    date_time = fields.Char(string='Date & Time')
    actual_date = fields.Datetime(string='Actual Date')
    currency_id = fields.Many2one('res.currency', string='Company Currency')
    amount = fields.Float(string='Amount')
    subtotal = fields.Float(string='Subtotal')
    transaction_type = fields.Char(string='Transaction Type')
    transaction_status = fields.Char(string='Result')
    card_no = fields.Char(string='Payment Method')
    status = fields.Char(string='Status')
    email_id = fields.Char(string='Email')
    auth_code = fields.Char(string='Auth Code')
    source = fields.Char(string='Source')
    card_no_ecom = fields.Char(string='Payment Method Ecom')
    payment_method_icon = fields.Many2one('payment.method')
    custnumber = fields.Char('Customer Number')
    image = fields.Binary("Image", related="payment_method_icon.image")
    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config', string='EBizCharge Profile')
    surcharge_amount = fields.Float(string='Surcharge Amount')
    surcharge_percentage = fields.Char(string='Surcharge %')

    def action_transaction_show_details(self):
        self.ensure_one()
        lines = self.get_details(self.ref_no)
        return {
            "name": _("Transaction Details"),
            "type": "ir.actions.act_window",
            "res_model": "transaction.detail.wizard",
            'view_id': self.env.ref('payment_ebizcharge_crm.transaction_detail_wizard_form', False).id,
            "view_mode": "form",
            "target": "new",
            "context": {"default_transaction_lines": lines},
        }

    def get_details(self, ref_no):
        instance = self.transaction_id.ebiz_profile_id
        ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
        transactions = ebiz.client.service.GetTransactionDetails(
            **{'securityToken': ebiz._generate_security_json(),
               'transactionRefNum': ref_no})

        line_vals = []
        document_type = 'Quick Payment'
        invoice = self.env['account.move'].search([('name', '=', self.invoice_id.replace(' ','') )], limit=1)
        sale = self.env['sale.order'].search([('name', '=', self.invoice_id.replace(' ','') )], limit=1)
        if invoice:
            document_type = 'Invoice'
        if sale:
            document_type = 'Sales Order'
        if transactions and 'LineItems' in transactions and transactions['LineItems'] and 'LineItem' in transactions['LineItems']:
            is_multi_line = 0
            doc_type = 'Sales Order'
            for transaction in transactions['LineItems']['LineItem']:
                if transactions['Details']['Invoice'] == 'Multiple':
                    is_multi_line = 1

                    if transaction['SKU'] != 'Charge':
                        invoice = self.env['account.move'].search([('name', '=', transaction['SKU'].replace(' ', ''))],
                                                                  limit=1)
                        sale = self.env['sale.order'].search([('name', '=', transaction['SKU'].replace(' ', ''))], limit=1)
                        if invoice:
                            document_type = 'Invoice'
                        if sale:
                            document_type = 'Sales Order'
                        
                        line_vals.append((0, 0, {
                            'name': transaction['SKU'],
                            'document_type': document_type,
                            'payment_amount': float(transaction['Qty']) * float(transaction['UnitPrice']),
                        }))
                else:
                    is_multi_line = 0
                    if 'Inv' in transaction['Description'] or 'INV' in transactions['Details']['Invoice']:
                        doc_type = 'Invoice'

            if is_multi_line == 0:
                line_vals.append((0, 0, {
                    'name': transactions['Details']['Invoice'],
                    'document_type': document_type,
                    'payment_amount': float(transactions['Details']['Amount']),
                }))
        if line_vals==[]:
            line_vals.append((0, 0, {
                'name': self.invoice_id,
                'document_type': document_type,
                'payment_amount': self.subtotal,
            }))      
        return line_vals

    def from_js(self):
        self.env['transaction.history'].search([]).unlink()


class ListSyncHistory(models.TransientModel):
    _name = 'sync.history.transaction'
    _description = "Sync History Transaction"
    _order = 'date_time asc'

    sync_date = fields.Datetime('Execution Date/Time', required=True, default=fields.Datetime.now)
    sync_transaction_id = fields.Many2one('transaction.history', string='Partner Reference', required=True,
                                          ondelete='cascade', index=True, copy=False)
    name = fields.Char(string='Name')
    customer_id = fields.Char(string='Customer ID')
    invoice_id = fields.Char(string='Number')
    order_id = fields.Char(string='Order Number')
    ref_no = fields.Char(string='Reference Number')
    ref_no_op = fields.Char(string='Reference Number Op')
    account_holder = fields.Char(string='Name On Card/Account')
    date_time = fields.Char(string='Date & Time')
    currency_id = fields.Many2one('res.currency', string='Company Currency')
    amount = fields.Float(string='Amount')
    tax = fields.Char(string='Tax')
    transaction_type = fields.Char(string='Transaction Type')
    transaction_type_op = fields.Char(string='Transaction Type Op')
    transaction_status = fields.Char(string='Result')
    transaction_status_op = fields.Char(string='Result Op')
    card_no = fields.Char(string='Payment Method')
    status = fields.Char(string='Status')
    status_op = fields.Char(string='Status Op')
    field_name = fields.Char(string='Field Name')
    check_box = fields.Boolean('Select')
    email_id = fields.Char(string='Email')
    auth_code = fields.Char(string='Auth Code')
    source = fields.Char(string='Source')
    image = fields.Binary("Image", help="This field holds the image used for this payment method")


class EbizAvsHistryTags(models.TransientModel):
    _name = 'ebiz.avs.histry.tags'
    _description = "Sync History AVS"

    name = fields.Char(string='Name')

class EbizCVVHistryTags(models.TransientModel):
    _name = 'ebiz.cvv.histry.tags'
    _description = "Sync History CVV"

    name = fields.Char(string='Name')


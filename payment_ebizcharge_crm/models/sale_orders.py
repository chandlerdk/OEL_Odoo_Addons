# -*- coding: utf-8 -*-
from odoo import models, fields, api, _, SUPERUSER_ID
from odoo.exceptions import ValidationError, UserError
import logging
from datetime import datetime , timedelta

from .ebiz_charge import message_wizard
from ..utils import strtobool

_logger = logging.getLogger(__name__)



class SaleAdvancePaymentInv(models.TransientModel):
    _inherit = 'sale.advance.payment.inv'

    is_pay_link = fields.Boolean(string='Pay Link')

    @api.onchange('is_pay_link')
    def onchange_sale_order_ids(self):
        for line in self:
            pay_link = False
            for so in self.sale_order_ids:
                if so.partner_id.ebiz_profile_id:
                    ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=so.partner_id.ebiz_profile_id)
                    filters_list = []
                    filters_list.append(
                        {'FieldName': 'InvoiceNumber', 'ComparisonOperator': 'eq', 'FieldValue': so.name})

                    today = datetime.now()
                    end = today + timedelta(days=1)
                    start = today + timedelta(days=-365)
                    received_payments = ebiz.client.service.SearchEbizWebFormReceivedPayments(**{
                        'securityToken': ebiz._generate_security_json(),
                        'fromPaymentRequestDateTime': str(start.date()),
                        'toPaymentRequestDateTime': str(end.date()),
                        'start': 0,
                        'limit': 10000,
                        "filters": {'SearchFilter': filters_list},
                    })
                    if not received_payments and so.save_payment_link:
                        pay_link = True
            line.is_pay_link = pay_link




class SaleOrderInh(models.Model):
    _inherit = 'sale.order'

    def _get_default_ebiz_auto_sync(self):
        ebiz_auto_sync_sale_order = False
        if self.partner_id.ebiz_profile_id:
            ebiz_auto_sync_sale_order = self.partner_id.ebiz_profile_id.ebiz_auto_sync_sale_order
        return ebiz_auto_sync_sale_order

    ebiz_internal_id = fields.Char('Ebiz Internal Id', copy=False)
    is_pre_auth = fields.Boolean(string='Pre-Auth', compute='_compute_pre_auth')
    ebiz_amount_residual = fields.Float(string='EBiz Amount Residual', compute='_compute_pre_auth')
    ebiz_order_amount_residual = fields.Float(string='EBizz Amount Residual', compute='_compute_order_pre_auth')
    ebiz_auto_sync = fields.Boolean(compute="_compute_ebiz_auto_sync", default=_get_default_ebiz_auto_sync)
    done_transaction_ids = fields.Many2many('payment.transaction', compute='_compute_done_transaction_ids',
                                            string='Authorized Transaction', copy=False, readonly=True)

    payment_internal_id = fields.Char(string='EBiz Email Response', copy=False)
    ebiz_transaction_ref = fields.Char('EBiz Transaction Ref', compute="_compute_trans_ref")
    is_invoice_paid = fields.Boolean(compute="_compute_invoice_payment_status")
    sync_status = fields.Char(string="EBizCharge Upload Status", compute="_compute_sync_status")
    sync_response = fields.Char(string="Sync Status", copy=False)
    last_sync_date = fields.Datetime(string="Upload Date & Time", copy=False)

    receipt_status = fields.Boolean(compute="_compute_receipt_status", default=False)
    amount_due_custom = fields.Monetary(compute="_compute_amount_due", string='Amount Due')
    currency_id = fields.Many2one("res.currency", string="Currency", readonly=True)
    ebiz_app_trans_internal_id = fields.Char("EBiz Application Transaction Id", copy=False)
    ebiz_application_transaction_ids = fields.One2many('ebiz.application.transaction', 'sale_order_id')
    customer_id = fields.Char("Customer Id", compute="_compute_customer_id")
    save_payment_link = fields.Char(string='Save Payment Link', copy=False)
    odoo_payment_link = fields.Boolean(string='Payment Link', copy=False, default=False)
    odoo_payment_link_doc = fields.Char(string='Payment Link Doc', copy=False)
    request_amount = fields.Float(string='Request Amount', copy=False)
    last_request_amount = fields.Float(string='Last Request Amount', copy=False)
    is_email_request = fields.Boolean(string='Email Pay sent', copy=False)
    ebiz_invoice_status = fields.Selection([
        ('default', ''),
        ('pending', 'Pending'),
        ('received', 'Received'),
        ('partially_received', 'Partially Received'),
        ('delete', 'Deleted'),
        ('applied', 'Applied'),
    ], string='Email Pay Status', default='default', readonly=True, copy=False, index=True)

    log_status_emv = fields.Char(string="Logs EMV", tracking=True, copy=False)
    emv_transaction_id = fields.Many2one('emv.device.transaction', string='Transaction ID', copy=False)
    transaction_type = fields.Selection([
        ('pre_auth', 'Pre-Authorize'),
        ('deposit', 'Deposit'),
    ], string='Transaction Type', index=True, copy=False)


    @api.constrains('invoice_status')
    def check_invoice_status(self):
        for so in self:
            if so.partner_id.ebiz_profile_id  and so.invoice_status=='invoiced':
                ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=so.partner_id.ebiz_profile_id)
                filters_list = []
                filters_list.append(
                    {'FieldName': 'InvoiceNumber', 'ComparisonOperator': 'eq', 'FieldValue': so.name})

                today = datetime.now()
                end = today + timedelta(days=1)
                start = today + timedelta(days=-365)
                received_payments = ebiz.client.service.SearchEbizWebFormReceivedPayments(**{
                    'securityToken': ebiz._generate_security_json(),
                    'fromPaymentRequestDateTime': str(start.date()),
                    'toPaymentRequestDateTime': str(end.date()),
                    'start': 0,
                    'limit': 10000,
                    "filters": {'SearchFilter': filters_list},
                })
                if not received_payments and  so.save_payment_link and so.payment_internal_id:
                    pay_link_deletion = ebiz.client.service.DeleteEbizWebFormPayment(**{
                        'securityToken': ebiz._generate_security_json(),
                        'paymentInternalId': so.payment_internal_id,
                    })
                    if so and so.save_payment_link:
                        message_log = 'EBizCharge Payment Link invalidated: ' + str(so.save_payment_link)
                        so.message_post(body=message_log)
                    if pay_link_deletion:
                        so.save_payment_link = False
                        so.odoo_payment_link = False
                        so.request_amount = 0
                if so.partner_id.ebiz_profile_id.apply_sale_pay_inv:

                    if received_payments:
                        for item in received_payments:
                            resp = ebiz.client.service.MarkEbizWebFormPaymentAsApplied(**{
                                'securityToken': ebiz._generate_security_json(),
                                'paymentInternalId': item['PaymentInternalId'],
                            })
                            if resp and resp['Status'] == 'Success':
                                payment_acq = self.env['payment.provider'].search(
                                    [('company_id', '=',
                                      so.company_id.id if so.company_id else so.env.company.id),
                                     ('code', '=', 'ebizcharge')], limit=1)
                                ebiz_method_tran = self.env['payment.method'].search(
                                    [('code', '=', 'ebizcharge')], limit=1)
                                ebiz_method = self.env['account.payment.method.line'].search(
                                    [('journal_id', '=', payment_acq.journal_id.id),
                                     ('payment_method_id.code', '=', 'ebizcharge')], limit=1)
                                payment = False
                                transactions = ebiz.client.service.GetTransactionDetails(
                                    **{'securityToken': ebiz._generate_security_json(),
                                       'transactionRefNum': item['RefNum']})
                                transaction_type = 'pre_auth'
                                if transactions['TransactionType'] not in ('Auth Only', 'Authonly'):
                                    payment = self.env['account.payment'].sudo().create({
                                        'journal_id': payment_acq.journal_id.id,
                                        'payment_method_id': ebiz_method.payment_method_id.id,
                                        'payment_method_line_id': ebiz_method.id,
                                        'partner_id': so.partner_id.id,
                                        'payment_reference': item['RefNum'],
                                        'amount': item['PaidAmount'],
                                        'partner_type': 'customer',
                                        'payment_type': 'inbound',
                                        'ref': item['InvoiceNumber'] if item['InvoiceNumber'] else '',
                                    })
                                    transaction_type = 'deposit'

                                ebiz_transaction = self.env['payment.transaction'].sudo().create({
                                    'provider_id': payment_acq.sudo().id,
                                    'payment_method_id': ebiz_method_tran.id,
                                    'provider_reference': item['RefNum'],
                                    'reference': item['InvoiceNumber'] if item['InvoiceNumber'] else '',
                                    'amount': item['PaidAmount'],
                                    'currency_id': payment_acq.company_id.currency_id.id,
                                    'partner_id': so.partner_id.id,
                                    'token_id': False,
                                    'operation': 'offline',
                                    'transaction_type': transaction_type,
                                    'sale_order_ids': [so.id],
                                    'invoice_ids': [(6, 0, so.invoice_ids.ids)],
                                    'payment_id': payment.id if payment else False,
                                })  # In sudo mode to allow writing on callback fields
                                so.save_payment_link = False
                                so.odoo_payment_link = False
                                so.request_amount = 0
                                if payment:
                                    payment.payment_transaction_id = ebiz_transaction.id
                                    # payment.transaction_ids = [(6, 0, [ebiz_transaction.id])]
                                ebiz_transaction._set_authorized()
                                so.update({'authorized_transaction_ids': [(6, 0, [ebiz_transaction.id])] })
                                if transactions['TransactionType'] not in ('Auth Only', 'Authonly'):
                                    ebiz_transaction._set_done()
                                # if payment and payment.state=='draft':
                                #     payment.action_post()


    def action_confirm(self):
        ret = super(SaleOrderInh, self).action_confirm()
        if self.partner_id.ebiz_profile_id:
            if  self.partner_id.ebiz_profile_id.sales_auto_gpl and not self.save_payment_link and self.ebiz_amount_residual>0.0:
                self.action_generate_pay_ebiz_link()
        return ret

    def _transaction_line(self, line):
        qty = line.product_uom_qty if hasattr(line, 'product_uom_qty') else line.quantity
        tax_ids = line.tax_ids if hasattr(line, 'tax_ids') else line.tax_id
        price_tax = line.price_tax if hasattr(line, 'price_tax') else 0
        return {
            'SKU': line.product_id.id,
            'ProductName': line.product_id.name,
            'Description': line.name,
            'UnitPrice': line.price_unit,
            'Taxable': True if tax_ids else False,
            'TaxAmount': int(price_tax),
            'Qty': int(qty),
        }

    def _transaction_lines(self, lines):
        item_list = []
        for line in lines:
            item_list.append(self._transaction_line(line))
        return {'TransactionLineItem': item_list}

    
    def action_generate_pay_ebiz_link(self):
        template = self.env['email.templates'].search([('template_type_id', '=', 'SalesOrderWebFormEmail'), (
            'instance_id', '=', self.partner_id.ebiz_profile_id.id) ], limit=1)
        if template:
            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=self.partner_id.ebiz_profile_id)
            fname = self.partner_id.name.split(' ')
            lname = ''
            for name in range(1, len(fname)):
                lname += fname[name]
            address = ''
            if self.partner_id.street:
                address += self.partner_id.street
            if self.partner_id.street2:
                address += ' ' + self.partner_id.street2

            lines = self.order_line
            get_merchant_data = False
            get_allow_credit_card_pay = False
            if self.partner_id.ebiz_profile_id:
                get_merchant_data = self.partner_id.ebiz_profile_id.merchant_data
                get_allow_credit_card_pay = self.partner_id.ebiz_profile_id.allow_credit_card_pay
            payment_method = 'CC'
            if get_merchant_data and get_allow_credit_card_pay:
                payment_method = 'CC,ACH'
            elif get_merchant_data:
                payment_method = 'ACH'
            elif get_allow_credit_card_pay:
                payment_method = 'CC'

            ePaymentForm = {
                'FormType': 'PayLinkOnly',
                'FromEmail': 'support@ebizcharge.com',
                'FromName': 'EBizCharge',
                'EmailSubject': template.template_subject,
                'EmailAddress': self.partner_id.email if self.partner_id.email else ' ',
                'EmailTemplateID': template.template_id,
                'EmailTemplateName': template.name,
                'ShowSavedPaymentMethods': True,
                'CustFullName': self.partner_id.name,
                'TotalAmount': self.amount_total,
                'PayByType': payment_method,
                'AmountDue': self.ebiz_order_amount_residual,
                'ShippingAmount': 0,
                'CustomerId': self.partner_id.ebiz_customer_id or self.partner_id.id,
                'ShowViewSalesOrderLink': True,
                'SendEmailToCustomer': False,
                'TaxAmount': self.amount_tax,
                'SoftwareId': 'ODOOPayLinkOnly',
                'SalesOrderInternalId': self.ebiz_internal_id,
                'Description': 'SalesOrder' ,
                'DocumentTypeId': 'SalesOrder' ,
                'PoNum': self.client_order_ref if self.client_order_ref else self.name,
                'InvoiceNumber': str(self.id) if str(self.name) == '/' else str(self.name),
                'BillingAddress': {
                    "FirstName": fname[0],
                    "LastName": lname,
                    "CompanyName": self.partner_id.company_name if self.partner_id.company_name else '',
                    "Address1": address,
                    "City": self.partner_id.city if self.partner_id.city else '',
                    "State": self.partner_id.state_id.code or 'CA',
                    "ZipCode": self.partner_id.zip or '',
                    "Country": self.partner_id.country_id.code or 'US',
                },
                "LineItems": self._transaction_lines(lines),
            }

            if self.partner_id.ebiz_customer_id:
                ePaymentForm['CustomerId'] = self.partner_id.ebiz_customer_id
            if self.partner_id.ebiz_profile_id.gpl_pay_sale=='pre_auth':
                ePaymentForm['ProcessingCommand'] = 'AuthOnly'
                ePaymentForm['PayByType'] = 'CC'
                self.transaction_type = 'pre_auth'
            else:
                ePaymentForm['ProcessingCommand'] = 'sale'
                self.transaction_type = 'deposit'
            ePaymentForm[
                    'Date'] = self.date_order
            form_url = ebiz.client.service.GetEbizWebFormURL(**{
                'securityToken': ebiz._generate_security_json(),
                'ePaymentForm': ePaymentForm
            })
            self.save_payment_link = form_url
            self.is_email_request = False
            self.payment_internal_id = form_url.split('=')[1]
            # self.ebiz_invoice_status = ' '
            if self.save_payment_link:
                message_log ='New EBizCharge Payment Link has been generated: '+str(form_url)
                self.message_post(body=message_log)
    
    def _log_pay_link(self):
        for line in self:
            instance = self.partner_id.ebiz_profile_id
            if self.save_payment_link and self.payment_internal_id and instance:
                ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
                ebiz.client.service.DeleteEbizWebFormPayment(**{
                    'securityToken': ebiz._generate_security_json(),
                    'paymentInternalId': self.payment_internal_id,
                })
            if self and self.save_payment_link:
                message_log = 'EBizCharge Payment Link invalidated: ' + str(self.save_payment_link)
                self.message_post(body=message_log)
                self.save_payment_link = False            
            if line.odoo_payment_link_doc:
                message_log ='New Payment Link has been generated: '+str(line.odoo_payment_link_doc)
                line.message_post(body=message_log)

    def _compute_receipt_status(self):
        receipts = self.env['account.move.receipts']
        for order in self:
            config = receipts.search(
                [('invoice_id', '=', order.id)])
            order.receipt_status = True if config else False

    def _compute_ebiz_auto_sync(self):
        self.ebiz_auto_sync = False


    def _compute_order_pre_auth(self):
        for sal in self:
            transaction = self.env['payment.transaction'].sudo().search(
                [('reference', '=', sal.name), ('state', 'in', ('pending', 'authorized', 'done'))])
            for uniq_trans in transaction:
                uniq_trans.update({
                    'sale_order_ids': [[6, 0, [sal.id]]],
                })
            inv_list = sal.invoice_ids.mapped('payment_state')
            transactions = self.env['payment.transaction'].sudo().search(
                [('state', 'in', ('pending', 'authorized', 'done')), '|', ('sale_order_ids', 'in', sal.ids), ('invoice_ids', 'in', sal.invoice_ids.ids)])
            sum_amount_ebiz = 0
            for uniq_transaction in transactions:
                if uniq_transaction.captured_amount > 0:
                    sum_amount_ebiz += uniq_transaction.captured_amount
                else:
                    sum_amount_ebiz += uniq_transaction.amount

            amt_calc = (sal.amount_total - sum_amount_ebiz)
            sal.ebiz_order_amount_residual = amt_calc if amt_calc > 0.0 else 0
    

    def generate_payment_link(self):
        try:
            if len(self) == 0:
                raise UserError('Please select a record first!')
            if len(self.partner_id.ebiz_profile_id)>1:
                raise UserError('Filter the Orders for a specific unique merchant account. Selection of Orders for more than one merchant account is not allowed.')

            profile = False
            payment_lines = []

            if self:
                odoo_pay_link = False
                ebiz_pay_link = False
                for order in self:
                    if order.odoo_payment_link:
                        odoo_pay_link = True
                    if order.save_payment_link:
                        ebiz_pay_link = True
                if odoo_pay_link:
                    text = f"This document has a pending payment link. Proceeding may increase the risk of double payments. Do you want to continue?"
                    wizard = self.env['wizard.receive.email.payment.link'].create({
                                                                                   "sale_ids": [(6,0, self.ids)] ,
                                                                                   "text": text})
                    action = self.env.ref('payment_ebizcharge_crm.wizard_received_email_pay_payment_link').read()[0]
                    action['res_id'] = wizard.id
                    # action['context'] = dict(
                    #     invoice=self.id,
                    # )
                    return action

                elif ebiz_pay_link:
                    # raise UserError(str(ebiz_pay_link))
                    text = f"This document has an existing payment link. Proceeding will invalidate the existing link. Do you want to continue?"
                    wizard = self.env['wizard.receive.email.payment.link'].create({
                                                                                   "sale_ids": [(6, 0, self.ids)],
                                                                                   "text": text})
                    action = self.env.ref('payment_ebizcharge_crm.wizard_received_email_pay_payment_link').read()[0]
                    action['res_id'] = wizard.id
                    # action['context'] = dict(
                    #     invoice=self.id,
                    # )
                    return action
                else:
                    for inv in self:
                        search_so = self.env['sale.order'].search([('id', '=', inv.id)], limit=1)
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
                         'sale_link': True,
                         'ebiz_profile_id': profile})
                    action = \
                        self.env.ref('payment_ebizcharge_crm.wizard_generate_so_link_form_views_action').read()[0]
                    action['res_id'] = wiz.id
                    action['context'] = self.env.context
                    return action
        except Exception as e:
            raise ValidationError(e)

    def _compute_pre_order_auth(self):
        for sal in self:
            transaction = self.env['payment.transaction'].sudo().search(
                [('reference', '=', sal.name), ('state', 'in', ('pending', 'authorized', 'done'))])
            for uniq_trans in transaction:
                uniq_trans.update({
                    'sale_order_ids': [[6, 0, [sal.id]]],
                })
            inv_list = sal.invoice_ids.mapped('payment_state')
            transactions = self.env['payment.transaction'].sudo().search(
                [('state', 'in', ('pending', 'authorized', 'done')), '|', ('sale_order_ids', 'in', sal.ids), ('invoice_ids', 'in', sal.invoice_ids.ids)])
            sum_amount_ebiz = 0
            for uniq_transaction in transactions:
                if uniq_transaction.captured_amount > 0:
                    sum_amount_ebiz += uniq_transaction.captured_amount
                else:
                    sum_amount_ebiz += uniq_transaction.amount
            if sum_amount_ebiz >= sal.amount_total:
                sal.ebiz_amount_residual += sal.request_amount
            else:
                amt_calc = (sal.amount_total - sum_amount_ebiz) - sal.request_amount
                sal.ebiz_amount_residual = amt_calc if amt_calc > 0.0 else 0

            check = False
            if sal.invoice_ids:
                check = all(x in ['paid', 'in_payment'] for x in inv_list)
            if transaction or check:
                sal.is_pre_auth = True
            else:
                sal.is_pre_auth = False

    def _compute_pre_auth(self):
        for sal in self:
            transaction = self.env['payment.transaction'].sudo().search(
                [('reference', '=', sal.name), ('state', 'in', ('pending', 'authorized', 'done'))])
            for uniq_trans in transaction:
                uniq_trans.update({
                    'sale_order_ids': [[6, 0, [sal.id]]],
                })
            inv_list = sal.invoice_ids.mapped('payment_state')
            transactions = self.env['payment.transaction'].sudo().search(
                [('state', 'in', ('pending', 'authorized', 'done')), '|', ('sale_order_ids', 'in', sal.ids), ('invoice_ids', 'in', sal.invoice_ids.ids)])
            sum_amount_ebiz = 0
            for uniq_transaction in transactions:
                if uniq_transaction.captured_amount > 0:
                    sum_amount_ebiz += uniq_transaction.captured_amount
                else:
                    sum_amount_ebiz += uniq_transaction.amount
            if sum_amount_ebiz >= sal.amount_total:
                sal.ebiz_amount_residual += sal.request_amount
            else:
                amt_calc = (sal.amount_total - sum_amount_ebiz) - sal.request_amount
                sal.ebiz_amount_residual = amt_calc if amt_calc > 0.0 else 0

            check = False
            if sal.invoice_ids:
                check = all(x in ['paid', 'in_payment'] for x in inv_list)
            if transaction or check:
                sal.is_pre_auth = True
            else:
                sal.is_pre_auth = False

    @api.depends('partner_id')
    def _compute_customer_id(self):
        for sal in self:
            sal.customer_id = sal.partner_id.id

    @api.depends('invoice_ids.amount_residual')
    def _compute_amount_due(self):
        for entry in self:
            entry.amount_due_custom = entry.invoice_ids[0].amount_residual if entry.invoice_ids else entry.amount_total

    @api.depends('ebiz_internal_id')
    def _compute_sync_status(self):
        for order in self:
            order.sync_status = "Synchronized" if order.ebiz_internal_id else "Pending"

    @api.depends('invoice_ids.payment_state')
    def _compute_invoice_payment_status(self):
        self.is_invoice_paid = self.invoice_ids and self.invoice_ids[0].payment_state == "paid"

    @api.depends('transaction_ids.provider_reference')
    def _compute_trans_ref(self):
        self.ebiz_transaction_ref = self.transaction_ids[0].provider_reference if self.transaction_ids else ""

    @api.depends('transaction_ids')
    def _compute_done_transaction_ids(self):
        for trans in self:
            trans.done_transaction_ids = trans.transaction_ids.filtered(lambda t: t.state == 'done')

    @api.model_create_multi
    def create(self, values):
        record = super(SaleOrderInh, self).create(values)
        for rec in record:
            ebiz_auto_sync_sale_order = False
            if rec.partner_id.ebiz_profile_id:
                ebiz_auto_sync_sale_order = rec.partner_id.ebiz_profile_id.ebiz_auto_sync_sale_order
            if ebiz_auto_sync_sale_order:
                rec.sync_to_ebiz()
        return record

    def ebiz_create_payment_line(self, amount):
        acquirer = self.env['payment.provider'].search(
            [('company_id', '=', self.company_id.id), ('code', '=', 'ebizcharge')])
        journal_id = acquirer.journal_id
        ebiz_method = self.env['account.payment.method.line'].search(
            [('journal_id', '=', journal_id.id), ('payment_method_id.code', '=', 'ebizcharge')], limit=1)
        payment = self.env['account.payment'].sudo().with_context(active_ids=self.ids, active_model='sale.order', active_id=self.id).create({
            'journal_id': journal_id.id,
            'payment_method_id': ebiz_method.payment_method_id.id if ebiz_method else False,
            'payment_method_line_id': ebiz_method.id if ebiz_method else False,
            'token_type': None,
            'amount': amount,
            'partner_id': self.partner_id.id,
            'ref': self.name or None,
            'payment_type': 'inbound'
        })
        payment.with_context({'do_not_run_transaction': True}).action_post()

    def delete_ebiz_so_link(self):
        """
            Niaz Implementation:
            Delete the  pending invoice
        """
        try:
            instance = None
            if self.partner_id.ebiz_profile_id:
                instance = self.partner_id.ebiz_profile_id

            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            received_payments = ebiz.client.service.DeleteEbizWebFormPayment(**{
                'securityToken': ebiz._generate_security_json(),
                'paymentInternalId': self.payment_internal_id,
            })
            if received_payments:
                self.save_payment_link = False
                self.odoo_payment_link = False
                self.request_amount = 0
                self.env.cr.commit()
        except Exception as e:
            raise ValidationError(e)

    def aagenerate_payment_link(self):
        try:
            if self.amount_total <= 0:
                raise UserError('The value of the payment amount must be positive.')

            if self.odoo_payment_link:
                raise UserError('Payment link is already generated.')

            if not self.ebiz_internal_id:
                self.sync_to_ebiz()

            if self.save_payment_link:
                return {'type': 'ir.actions.act_window',
                        'name': _('Copy Payment Link'),
                        'res_model': 'ebiz.payment.link.copy',
                        'target': 'new',
                        'view_mode': 'form',
                        'view_type': 'form',
                        'context': {
                            'default_link': self.save_payment_link,
                        }}
            else:
                return {'type': 'ir.actions.act_window',
                        'name': _('Generate Payment Link'),
                        'res_model': 'ebiz.payment.link.wizard',
                        'target': 'new',
                        'view_mode': 'form',
                        'view_type': 'form',
                        'context': {
                            'default_ebiz_profile_id': self.partner_id.ebiz_profile_id.id,
                            'active_id': self.id,
                            'active_model': 'sale.order',
                        }}

        except Exception as e:
            raise ValidationError(e)

    def sync_to_ebiz_ind(self):
        self.sync_to_ebiz()
        return message_wizard('Sales order uploaded successfully!')

    def sync_to_ebiz(self, time_sample=None):
        self.ensure_one()
        instance = None
        if self.partner_id.ebiz_profile_id:
            instance = self.partner_id.ebiz_profile_id
        else:
            default_instance = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_default', '=', True)], limit=1)
            if default_instance:
                instance = default_instance
        ebiz = self.get_ebiz_object(instance)
        update_params = {}
        sales_order_upload = self.env['upload.sale.orders'].search([], limit=1)
        if not self.partner_id.ebiz_internal_id:
            self.partner_id.sync_to_ebiz()

        if self.ebiz_internal_id:
            resp = ebiz.update_sale_order(self)
        else:
            resp = ebiz.sync_sale_order(self)
            if resp['ErrorCode'] == 2:
                resp_search = self.ebiz_search_sale_order()
                update_params.update(
                    {'ebiz_internal_id': resp_search['SalesOrderInternalId']})
            if resp and not resp['ErrorCode'] == 2:
                update_params.update(
                    {'ebiz_internal_id': resp['SalesOrderInternalId']})

        self.create_odoo_logs(resp, sales_order_upload)
        update_params.update({
            "last_sync_date": fields.Datetime.now(),
            "sync_response": 'Success' if resp['ErrorCode'] in [0, 2] else resp['Error']})
        self.write(update_params)
        self.ebiz_application_transaction_ids.ebiz_add_application_transaction()
        return resp

    def create_odoo_logs(self, resp, sales_order_upload):
        odoo_log = self.env['logs.of.orders']
        odoo_log.create({
            'order_no': self.id,
            'customer_name': self.partner_id.id,
            'customer_id': self.partner_id.id,
            'currency_id': self.env.user.currency_id.id,
            'sync_status': 'Success' if resp['ErrorCode'] in [0, 2] else resp['Error'],
            'last_sync_date': datetime.now(),
            'sync_log_id': sales_order_upload.id if sales_order_upload else False,
            'user_id': self.env.user.id,
            'amount_total': self.amount_total,
            'amount_due': self.amount_due_custom,
            'order_date': self.date_order,
        })

    def ebiz_search_sale_order(self):
        instance = None
        if self.partner_id.ebiz_profile_id:
            instance = self.partner_id.ebiz_profile_id
        ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
        resp = ebiz.client.service.SearchSalesOrders(**{
            'securityToken': ebiz._generate_security_json(),
            'customerId': self.partner_id.id,
            'salesOrderNumber': self.name,
            'start': 0,
            'limit': 0,
            'includeItems': False
        })
        if resp:
            return resp[0]
        return resp

    def run_ebiz_transaction(self, payment_token_id, command, token_ebiz=None):
        self.ensure_one()
        if not self.partner_id.ebiz_internal_id and payment_token_id and payment_token_id.partner_id.id==self.partner_id.id:
            self.partner_id.sync_to_ebiz()
        #if not self.partner_id.payment_token_ids:
        #    raise ValidationError("Please enter payment method profile on the customer.")
        instance = None
        if payment_token_id:
            instance = payment_token_id.partner_id.ebiz_profile_id
        elif self.partner_id.ebiz_profile_id:
            instance = self.partner_id.ebiz_profile_id
        elif self.env.user.partner_id.ebiz_profile_id:
            instance = self.env.user.partner_id.ebiz_profile_id
        else:
            default_instance = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_default', '=', True)], limit=1)
            if default_instance:
                instance = default_instance
        ebiz = self.get_ebiz_object(instance)
        if self.env.user._is_public():
            if token_ebiz:
                resp = ebiz.run_transaction(self, payment_token_id, command, token_ebiz=token_ebiz)
            else:
                resp = ebiz.run_transaction(self, payment_token_id, command)
        else:
            resp = ebiz.run_customer_transaction(self, payment_token_id, command, current_user=self.env.user.partner_id)
        if self.invoice_ids:
            self.invoice_ids.transaction_ids = [(6, 0, self.transaction_ids.ids)]
        return resp

    def get_ebiz_object(self, instance):
        web = self.env['ir.module.module'].sudo().search(
            [('name', '=', 'website_sale'), ('state', 'in', ['installed', 'to upgrade', 'to remove'])])
        ebiz_obj = self.env['ebiz.charge.api']
        if web:
            ebiz = ebiz_obj.get_ebiz_charge_obj(self.website_id.id, instance=instance)
        else:
            ebiz = ebiz_obj.get_ebiz_charge_obj(instance=instance)
        return ebiz

    def run_ebiz_refund_transaction(self):
        self.ensure_one()
        if not self.partner_id.payment_token_ids:
            raise ValidationError("Please enter payment methode profile on the customer to run transaction.")
        vals = {
            'provider_id': self.env['payment.provider'].search(
                [('company_id', '=', self.company_id.id), ('code', '=', 'ebizcharge')]).id,
            'payment_token_id': self.partner_id.payment_token_ids.id,
        }
        self._create_payment_transaction(vals)
        return True

    def sync_multi_sale_orders(self):
        resp_lines = []
        success = 0
        failed = 0
        total = len(self)
        for so in self:
            resp_line = {
                'customer_name': so.partner_id.name,
                'customer_id': so.partner_id.id,
                'order_number': so.name
            }
            try:
                resp = so.sync_to_ebiz()
                resp_line['record_message'] = resp['Error'] or resp['Status']
            except Exception as e:
                _logger.exception(e)
                resp_line['record_message'] = str(e)
            if resp_line['record_message'] == 'Success' or resp_line['record_message'] == 'Record already exists':
                success += 1
            else:
                failed += 1
            resp_lines.append([0, 0, resp_line])

        wizard = self.env['wizard.multi.sync.message'].create({'name': 'sales orders', 'order_lines_ids': resp_lines,
                                                               'success_count': success, 'failed_count': failed,
                                                               'total': total})
        action = self.env.ref('payment_ebizcharge_crm.wizard_multi_sync_message_action').read()[0]
        action['context'] = self._context
        action['res_id'] = wizard.id
        return action

    def sync_multi_customers_from_upload_saleorders(self, list):
        sale_orders_records = self.env['sale.order'].browse(list).exists()
        resp_lines = []
        success = 0
        failed = 0
        total = len(sale_orders_records)
        for so in sale_orders_records:
            resp_line = {
                'customer_name': so.partner_id.name,
                'customer_id': so.partner_id.id,
                'order_number': so.name
            }
            try:
                resp = so.sync_to_ebiz()
                resp_line['record_message'] = resp['Error'] or resp['Status']
            except Exception as e:
                _logger.exception(e)
                resp_line['record_message'] = str(e)
            if resp_line['record_message'] == 'Success' or resp_line['record_message'] == 'Record already exists':
                success += 1
            else:
                failed += 1
            resp_lines.append([0, 0, resp_line])

        wizard = self.env['wizard.multi.sync.message'].create({'name': 'sales orders', 'order_lines_ids': resp_lines,
                                                               'success_count': success, 'failed_count': failed,
                                                               'total': total})
        action = self.env.ref('payment_ebizcharge_crm.wizard_multi_sync_message_action').read()[0]
        action['context'] = self._context
        action['res_id'] = wizard.id
        return action

    def write(self, values):
        ret = super(SaleOrderInh, self).write(values)
        if 'ebiz_internal_id' in values:
            return ret
        for order in self:
            if order._ebzi_check_update_sync(values):
                if order.ebiz_internal_id:
                    order.sync_to_ebiz()
        return ret

    def _ebzi_check_update_sync(self, values):
        """
        Kuldeeps implementation 
        def: checks if the after updating the sale should we run update sync base on the
        values that are updating.
        @params:
        values : update values params
        """
        update_fields = {"partner_id", "name", "date_order", "amount_total", "date_order", "amount_total",
                         "currency_id", "amount_tax", "expected_date", "user_id", "order_line", "state"}
        return bool(update_fields.intersection(values))

    def pre_authorize(self):
        if len(self.ids) > 1:
            raise UserError('Unable to process more than 1 sales order.')
        if not self.partner_id.ebiz_profile_id:
            default_instance = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_default', '=', True)], limit=1)
            if default_instance:
                self.partner_id.update({
                    'ebiz_profile_id': default_instance.id,
                })
            else:
                raise UserError('No EBizCharge profile selected in customer..')
        if self.emv_transaction_id:
            self.emv_transaction_id.action_check(trans=self.emv_transaction_id.id)
        if self.ebiz_amount_residual == 0 and self.request_amount==0:
            raise UserError('This sale order is already processed.')

        instance = False
        is_profile = False
        allow_credit_card_pay = False
        merchant_data = False  
        if self.partner_id.ebiz_profile_id:
            allow_credit_card_pay = self.partner_id.ebiz_profile_id.allow_credit_card_pay
            merchant_data = self.partner_id.ebiz_profile_id.merchant_data
            instance = self.partner_id.ebiz_profile_id
            is_profile = True
        else:
            default_instance = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_default', '=', True)], limit=1)
            if default_instance:
                allow_credit_card_pay = default_instance.allow_credit_card_pay
                merchant_data = default_instance.merchant_data
                instance = default_instance
                is_profile = True
        emv_device_id = 0
        if instance:
            if instance and instance.is_emv_enabled:
                instance.action_get_devices()
        emv_devices = self.env['ebizcharge.emv.device'].search(
            [('is_default_emv', '=', True), ('merchant_id', '=', self.partner_id.ebiz_profile_id.id)], limit=1)
        if emv_devices:
            emv_device_id = emv_devices
        check = any(inv.save_payment_link for inv in self)
        return {
            'name': 'Register Payment',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'custom.register.payment',
            'view_id': False,
            'type': 'ir.actions.act_window',
            'target': 'new',
            'context': {
                'default_amount': self.ebiz_amount_residual,
                'default_date': datetime.now().date(),
                'default_ebiz_receipt_emails': self.partner_id.email,
                'default_order_id': self.id,
                'default_memo': self.name,
                'partner_id': self.partner_id.id,
                'sub_partner_id': self.partner_id.id,
                'default_card_functionality_hide': allow_credit_card_pay,
                'default_ach_functionality_hide': merchant_data,
                'default_is_ebiz_profile': is_profile,
                'default_ebiz_profile_id': instance.id,
                'default_required_security_code': instance.verify_card_before_saving,
                'default_is_pay_link': check
                #'default_emv_device_id': emv_device_id.id if emv_device_id else False,
            }
        }


    def _has_to_be_paid(self):
        self.ensure_one()
        transaction = self.get_portal_last_transaction()
        return (
                self.state in ['draft', 'sent', 'sale']
                and not self.is_expired
                and self.require_payment
                and transaction.state not in ['done', 'authorized']
                and self.amount_total > 0
        )


class EBizApplicationTransactions(models.Model):
    _name = "ebiz.application.transaction"
    _description = "EBiz Application Transaction"

    ebiz_internal_id = fields.Char('Application Transaction Internal Id')
    partner_id = fields.Many2one('res.partner')
    sale_order_id = fields.Many2one('sale.order')
    transaction_id = fields.Many2one('payment.transaction')
    transaction_type = fields.Char('Transaction Command')
    is_applied = fields.Boolean('Is Applied', default=False)

    def ebiz_add_application_transaction(self):
        for trans in self:
            trans.ebiz_single_application_transaction()

    def mark_application_transaction_as_applied(self):
        instance = None
        if self.partner_id.ebiz_profile_id:
            instance = self.partner_id.ebiz_profile_id

        ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
        for trans in self:
            if not self.is_applied:
                resp = ebiz.client.service.MarkApplicationTransactionAsApplied(**{
                    'securityToken': ebiz._generate_security_json(),
                    'applicationTransactionInternalId': trans.ebiz_internal_id
                })
                if resp['StatusCode'] == 1:
                    self.is_applied = True

    def ebiz_single_application_transaction(self):
        if self.sale_order_id.ebiz_internal_id and self.transaction_id.provider_reference and not self.ebiz_internal_id:
            instance = None
            if self.partner_id.ebiz_profile_id:
                instance = self.partner_id.ebiz_profile_id
            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            params = {
                'securityToken': ebiz._generate_security_json(),
                'applicationTransactionRequest': {
                    'CustomerInternalId': self.partner_id.ebiz_internal_id,
                    'TransactionId': self.transaction_id.provider_reference,
                    'TransactionTypeId': self.transaction_type,
                    'LinkedToTypeId': 'SalesOrder',
                    'LinkedToExternalUniqueId': self.sale_order_id.id,
                    'LinkedToInternalId': self.sale_order_id.ebiz_internal_id,
                    'SoftwareId': "Odoo CRM",
                    'TransactionDate': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'TransactionNotes': "Order No: {}".format(self.sale_order_id.name)
                }
            }
            resp = ebiz.client.service.AddApplicationTransaction(**params)
            if resp['StatusCode'] == 1:
                self.ebiz_internal_id = resp['ApplicationTransactionInternalId']
                self.mark_application_transaction_as_applied()
            return resp
        else:
            _logger.info('cannot add application transaction on order No: {}'.format(self.sale_order_id.name))


class SaleAdvancePaymentInv(models.TransientModel):
    _inherit = "sale.advance.payment.inv"

    @api.model
    def _default_get_is_website_order(self):
        web = self.env['ir.module.module'].sudo().search(
            [('name', '=', 'website_sale'), ('state', 'in', ['installed', 'to upgrade', 'to remove'])])
        if web and self._context.get('active_model') == 'sale.order' and self._context.get('active_id', False):
            sale_order = self.env['sale.order'].browse(self._context.get('active_id'))
            return bool(sale_order.website_id)
        return False

    is_website_order = fields.Boolean("Is Website Order", default=_default_get_is_website_order)

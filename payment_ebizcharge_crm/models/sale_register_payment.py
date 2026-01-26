# -*- coding: utf-8 -*-
from typing import Dict, List

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
import logging
from datetime import datetime
from ..utils import strtobool

_logger = logging.getLogger(__name__)


class CustomRegisterPayment(models.Model):
    _name = "custom.register.payment"
    _description = "Custom Register Payment"

    def _default_template(self):
        payment_acq = self.env['payment.provider'].search(
            [('company_id', '=', self.env.company.id), ('code', '=', 'ebizcharge')])
        if payment_acq:
            return payment_acq.journal_id.id
        else:
            return None
            
    def _default_transaction(self):
        if not self.ebiz_profile_id.is_emv_pre_auth:
            return 'deposit'
        else:
            return 'pre_auth'

    def _default_method_line_ebiz(self):
        payment_acq = self.env['payment.provider'].search(
            [('company_id', '=', self.env.company.id), ('code', '=', 'ebizcharge')])
        if payment_acq:
            ebiz_method = self.env['account.payment.method.line'].search([('journal_id','=', payment_acq.journal_id.id),('payment_method_id.code','=','ebizcharge')], limit=1)
            if ebiz_method:
                return ebiz_method.id
            else:
                return None
        else:
            return None

    journal_id = fields.Many2one('account.journal', string='Journal', default=_default_template)
    transaction_type = fields.Selection([
        ('pre_auth', 'Pre-Authorize'),
        ('deposit', 'Deposit'),
    ], string='Transaction Type', default=_default_transaction, index=True)
    amount = fields.Monetary(string='Amount')
    currency_id = fields.Many2one("res.currency", string="Currency", readonly=True, required=True,
                                  default=lambda self: self.env.user.company_id.currency_id.id)

    date = fields.Date(string='Payment Date', copy=False)
    memo = fields.Char(string='Memo', copy=False)
    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config', string='Profile')
    is_ebiz_profile = fields.Boolean()
    journal_code = fields.Char(string='Journal Name', compute="_compute_journal_code")
    payment_token_id = fields.Many2one('payment.token', string="Payment Token ID")
    order_id = fields.Many2one('sale.order', string="Sale Order")
    partner_id = fields.Many2one('res.partner', string="Customer")
    full_amount = fields.Boolean("Full Amount AVS")
    ebiz_send_receipt = fields.Boolean(string='Email Receipt', default=True)
    ebiz_receipt_emails = fields.Char(string='Email list', help="Comma Separated Email list( email1,email2)")
    ebiz_sur_char = fields.Char(string=' Surcharge Char')
    is_surch_enable = fields.Boolean(string=' Surcharge Enabled')
    is_over_deposit = fields.Boolean(string="Over Deposit")
    is_pay_link = fields.Boolean(string='Is Pay Link')

    payment_method_line_id = fields.Many2one('account.payment.method.line', string='Payment Method' , default=_default_method_line_ebiz)

    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config')



        
    emv_device_id = fields.Many2one('ebizcharge.emv.device', string='EMV Device')
    
    is_emv_enabled = fields.Boolean(related='ebiz_profile_id.is_emv_enabled')    
    
    
    @api.onchange('emv_device_id')
    def _reset_card_id_and_ach_account(self):
        if self.emv_device_id:
            self.token_type = 'emv_device'
            self.is_over_deposit = self.ebiz_profile_id.is_emv_pre_auth
            if not self.ebiz_profile_id.is_emv_pre_auth:
                self.transaction_type='deposit'            
            #if self.ebiz_profile_id:
            #    self.ebiz_profile_id.action_get_devices()    
            self.ach_account = None
            self.card_avs_street = None
            self.card_avs_zip = None
            self.ach_id = None
            self.security_code = None
            self.card_card_number = None
            self.card_id = None
            self.card_card_number = None
            self.card_exp_year = None
            self.card_exp_month = None
            self.card_card_code = None  
        else:
            self.is_over_deposit = True      

    @api.onchange('journal_id')
    def _compute_journal_code(self):
        acquirer = self.env['payment.provider'].search(
            [('company_id', '=', self.env.company.id), ('code', '=', 'ebizcharge')])
        journal_id = acquirer.journal_id
        for payment in self:
            if payment.payment_method_line_id.code == 'ebizcharge':
                payment.journal_code = "EBIZC"
            else:
                payment.journal_code = "other"

    @api.model
    def year_selection(self):
        today = fields.Date.today()
        # year =  # replace 2000 with your a start year
        year = today.year
        max_year = today.year + 30
        year_list = []
        while year != max_year:  # replace 2030 with your end year
            year_list.append((str(year), str(year)))
            year += 1
        return year_list

    @api.model
    def month_selection(self):
        m_list = []
        for i in range(1, 13):
            m_list.append((str(i), str(i)))
        return m_list

    ach_id = fields.Many2one('payment.token', string='Saved Bank Account')
    ach_functionality_hide = fields.Boolean(string='ach functionality')
    ach_account_holder_name = fields.Char(string="Account Holder Name *")
    ach_account = fields.Char(string="Account Number *")
    ach_account_type = fields.Selection([('Checking', 'Checking'), ('Savings', 'Savings')], string='Account Type *',
                                        default="Checking")
    ach_routing = fields.Char(string='Routing Number *')
    bank_account_save = fields.Boolean(string='Save Bank Account', default=True, readonly=False)

    card_functionality_hide = fields.Boolean(string='Card functionality')
    card_id = fields.Many2one('payment.token', string='Saved Card')
    security_code = fields.Char(string='Security Code')
    card_account_holder_name = fields.Char(string='Name on Card *')
    card_card_number = fields.Char(string='Card Number *')
    card_exp_year = fields.Selection(year_selection, string='Expiration Year *')
    card_exp_month = fields.Selection(month_selection, string='Expiration Month *')
    card_avs_street = fields.Char(string="Billing Address *")
    card_avs_zip = fields.Char(string='Zip / Postal Code *')
    card_card_code = fields.Char(string='Card Code')
    card_card_type = fields.Char(string='Card Type')
    card_save = fields.Boolean(string='Save Card', default=True, readonly=False)
    sub_partner_id = fields.Many2one('res.partner', string="Sub Partner Id")
    token_type = fields.Selection([('ach', 'ACH'), ('credit', 'Credit Card'), ('emv_device', 'Emv Devoice')], string='Payment Token Type')
    required_security_code = fields.Boolean(string="Required Security Code")

    ebiz_avs_street = fields.Char(string="AVS Street *")
    ebiz_avs_zip = fields.Char(string='Zip Code *')

    @api.onchange('transaction_type')
    def onchange_transaction_type(self):
        if self.transaction_type == 'pre_auth':
            self.amount = self.order_id.ebiz_order_amount_residual

    @api.constrains('card_avs_zip')
    def card_avs_zip_length_id(self):
        for rec in self:
            if rec.card_avs_zip:
                if len(rec.card_avs_zip) > 15:
                    raise ValidationError(_('Zip / Postal Code must be less than 15 Digits!'))
                elif '-' in rec.card_avs_zip:
                    for mystr in rec.card_avs_zip.split('-'):
                        if not mystr.isalnum():
                            raise ValidationError(_("Zip/Postal Code can only include numbers, letters, and '-'."))
                elif not rec.card_avs_zip.isalnum():
                    raise ValidationError(_("Zip/Postal Code can only include numbers, letters, and '-'."))

    @api.constrains('card_card_number')
    def card_card_number_length_id(self):
        for rec in self:
            if rec.token_type == 'credit':
                if rec.card_card_number and (len(rec.card_card_number) > 19 or len(rec.card_card_number) < 13):
                    raise ValidationError(_('Card number should be valid and should be 13-19 digits!'))

    @api.constrains('amount')
    def _constraint_min_amount(self):
        for rec in self:
            if rec.journal_id:
                ebiz_method = self.env['account.payment.method.line'].search(
                    [('journal_id', '=', rec.journal_id.id), ('payment_method_id.code', '=', 'ebizcharge')],
                    limit=1)
                if rec.amount == 0 and ebiz_method.code == 'ebizcharge':
                    raise UserError(_('Payment amount must be greater than 0'))

    @api.constrains('ach_account')
    def ach_acc_number_length_id(self):
        for rec in self:
            if rec.token_type == 'ach':
                if rec.ach_account:
                    if not rec.ach_account.isnumeric():
                        raise ValidationError(_('Account number must be numeric only!'))
                    elif rec.ach_account and not (len(rec.ach_account) >= 4 and len(rec.ach_account) <= 17):
                        raise ValidationError(_('Account number should be 4-17 digits!'))

    @api.constrains('ach_routing')
    def ach_routing_number_length_id(self):
        for rec in self:
            if rec.token_type == 'ach':
                if rec.ach_routing and len(rec.ach_routing) != 9:
                    raise ValidationError(_('Routing number must be 9 digits!'))

    @api.constrains('card_card_code')
    def card_card_code_length(self):
        for rec in self:
            if rec.token_type == 'credit':
                if rec.card_card_code and (len(rec.card_card_code) != 3 and len(rec.card_card_code) != 4):
                    raise ValidationError(_('Security code must be 3-4 digits.'))

    @api.constrains('security_code')
    def card_card_code_length_security_code(self):
        for rec in self:
            if rec.token_type == 'credit':
                if rec.security_code and (len(rec.security_code) != 3 and len(rec.security_code) != 4):
                    raise ValidationError(_('Security code must be 3-4 digits.'))

    @api.constrains('card_exp_month', 'card_exp_year')
    def card_expiry_date(self):
        today = datetime.now()
        for rec in self:
            if rec.token_type == 'credit' and rec.card_exp_month and rec.card_exp_year:
                if int(rec.card_exp_year) > today.year:
                    return
                elif int(rec.card_exp_year) == today.year:
                    if int(rec.card_exp_month) >= today.month:
                        return
                raise ValidationError(_('Card is expired!'))

    @api.onchange('card_card_number', 'card_exp_month', 'card_exp_year', 'card_card_code')
    def _reset_card_id(self):
        if self.card_card_number or self.card_exp_year or self.card_exp_month or self.card_card_code:
            self.token_type = 'credit'
            self.card_id = None
            self.emv_device_id = None
            self.security_code = None
            self.is_over_deposit = True

            self.ach_id = None
            self.ach_account = None
            self.ach_routing = None

    @api.onchange('ach_account', 'ach_routing')
    def _reset_ach_account(self):
        if self.ach_account or self.ach_routing:
            self.token_type = 'ach'
            self.ach_id = None
            self.emv_device_id = None
            self.is_over_deposit = True
            #self.transaction_type = self.ebiz_profile_id.transaction_type
            self.security_code = None
            self.card_id = None
            self.card_card_number = None
            self.card_exp_year = None
            self.card_exp_month = None
            self.card_card_code = None

    @api.onchange('card_id')
    def _reset_new_card_fields(self):
        for payment in self:
            if payment.card_id:
                payment.token_type = 'credit'
                payment.emv_device_id = None
                payment.is_over_deposit = True

                payment.payment_token_id = payment.card_id
                payment.card_card_number = None
                payment.card_exp_year = None
                payment.card_exp_month = None
                payment.card_card_code = None
                payment.ach_id = None
                payment.ach_account = None
                payment.ach_routing = None

    @api.onchange('ach_id')
    def _reset_new_ach_fields(self):
        for payment in self:
            if payment.ach_id:
                payment.token_type = "ach"
                payment.emv_device_id = None
                payment.is_over_deposit = True

                payment.payment_token_id = payment.ach_id
                payment.ach_account = None
                payment.ach_routing = None
                payment.security_code = None
                payment.card_id = None
                payment.card_card_number = None
                payment.card_exp_year = None
                payment.card_exp_month = None
                payment.card_card_code = None

    @api.model
    def default_get(self, default_fields):
        rec = super(CustomRegisterPayment, self).default_get(default_fields)
        if 'amount' in rec:
            partner = self.env['res.partner'].browse(self._context['partner_id'])
            is_sur_able = False
            if partner.ebiz_profile_id.is_surcharge_enabled and partner.ebiz_profile_id.surcharge_type_id == 'DailyDiscount':
                is_sur_able = True
            rec.update({
                'sub_partner_id': partner,
                'ebiz_sur_char': partner.ebiz_profile_id.surcharge_terms,
                'is_surch_enable': is_sur_able,
                'card_account_holder_name': partner.name,
                'card_avs_street': partner.street,
                'card_avs_zip': partner.zip,
                'partner_id': partner.id,
                'ebiz_profile_id': partner.ebiz_profile_id.id,
                'transaction_type': partner.ebiz_profile_id.transaction_type,
            })
            partner.with_context({'donot_sync': True}).ebiz_get_payment_methods()
        return rec

    def process(self):
        if not self.payment_method_line_id:
            raise UserError('Please select the EBizCharge payment method over Journal Incoming Payments!')
        if self.transaction_type == 'pre_auth' and self.amount < self.order_id.ebiz_order_amount_residual:
            raise UserError('Amount cannot be less than the original document amount for Pre-Auth.')
        if not self.card_id and not self.card_card_number and not self.ach_id and not self.ach_account_holder_name and not self.token_type == 'emv_device':
            raise UserError('Please select a payment method first!')

        if self.order_id.save_payment_link:
            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=self.order_id.partner_id.ebiz_profile_id)
            received_payments = ebiz.client.service.DeleteEbizWebFormPayment(**{
                'securityToken': ebiz._generate_security_json(),
                'paymentInternalId': self.order_id.payment_internal_id,
            })
            message_log = 'EBizCharge Payment Link invalidated: ' + str(self.order_id.save_payment_link)
            self.order_id.message_post(body=message_log)
            self.order_id.request_amount = 0
            self.order_id.last_request_amount = 0
            self.order_id.save_payment_link = False

        if not self.sub_partner_id.ebiz_internal_id:
            self.sub_partner_id.sync_to_ebiz()

        order = self.env['sale.order'].search([('id', '=', self.env.context.get('active_id'))])
        # if self.amount > order.amount_total:
        #     product = self.env['sale.order.line'].create({
        #         'order_id': order.id,
        #         'product_id': self.env.ref('payment_ebizcharge.default_ebizcharge_product').sudo().id,
        #         'product_uom_qty': 1,
        #         'price_unit': self.amount - order.amount_total,
        #     })
        card_resp = None
        if not self.order_id:
            self.order_id = order
        if self.ebiz_send_receipt:
            partner_ebiz_profile_id = self.order_id.partner_id.ebiz_profile_id
            if partner_ebiz_profile_id and not partner_ebiz_profile_id.use_econnect_transaction_receipt:
                raise ValidationError(
                    'Configuration required. Please enable eConnect transaction receipts in the integration server.')

        if not self.order_id.ebiz_internal_id:
            self.order_id.sync_to_ebiz()
        merchant_card_verification = False
        use_full_amount_for_avs = False
        if self.partner_id.ebiz_profile_id:
            merchant_card_verification = self.partner_id.ebiz_profile_id.merchant_card_verification
            use_full_amount_for_avs = self.partner_id.ebiz_profile_id.use_full_amount_for_avs

        if self.token_type == 'credit':
            if not self.card_id:
                resp, avs_result, card_resp = self.run_new_card_flow()
                if resp and type(resp) == bool:
                    token_id = self.create_credit_card_payment_method().id
                    self.payment_token_id = token_id
                else:
                    return resp
            else:
                self.payment_token_id = self.card_id
                if strtobool(use_full_amount_for_avs):
                    self.full_amount = True
                else:
                    card_resp = self.validate_card_runcustomertransaction()
                    avs_result = self.get_avs_result(card_resp)
                    if all([x == 'Match' for x in avs_result]) and card_resp['ResultCode'] == 'A':
                        pass
                    else:
                        return self.show_payment_response(card_resp, bypass_newcard_avs=True, saved_avs_card=True,
                                                          ebizcharge_profile=self.payment_token_id.ebizcharge_profile)
            if not self.full_amount:
                avs_result = self.get_avs_result(card_resp)
                """If merchant has full amount avs validation setting on
                then following code will run avs or proceed with the transaction as usual"""
                if card_resp['ResultCode'] == 'A':
                    "on successful invoice add payment on the invoice"
                    proceed = False
                    "full_amount will only be set true if the transaction is with new card so it will check for avs"
                    if self.full_amount:
                        if all([x == 'Match' for x in avs_result]):
                            proceed = True
                    else:
                        proceed = True

                    if proceed:
                        payment = self.action_create_payment()
                        transactions = payment._create_payment_transaction()
                        transactions.write({
                            'payment_id': payment.id,
                            'sale_order_ids': [self.order_id.id],
                            'invoice_ids': False,
                            'reference': self.order_id.name,
                            'transaction_type': self.transaction_type,
                        })
                        resp = transactions.with_context({'pre_auth_order': True})._send_payment_request()
                        transactions.update({
                            'invoice_ids': False,
                        })
                        avs_result = self.get_avs_result(resp)
                        if all([x == 'Match' for x in avs_result]) and resp['ResultCode'] == 'A':
                            pass
                        else:
                            return self.with_context(
                                {'transaction_id': transactions.id, 'full_amount': True}).show_payment_response(resp)

                        order.transaction_ids = [transactions.id]
                        payment.payment_transaction_id = transactions.id
                        payment.ref = transactions.reference or self.memo
                        if not self.card_save and not self.card_id:
                            self.payment_token_id.delete_payment_method()
                            self.partner_id.refresh_payment_methods()
                        context = self.action_check_surcharge(payment.payment_transaction_id)
                        return self.message_wizard(context)
                    else:
                        if merchant_card_verification:
                            return self.show_payment_response(card_resp,
                                                              customer_token=self.payment_token_id.partner_id.ebizcharge_customer_token,
                                                              payment_method_id=self.payment_token_id.ebizcharge_profile)

                        elif self.card_id and not self.new_card:
                            return self.show_payment_response(card_resp, bypass_newcard_avs=True)
                        else:
                            return self.show_payment_response(card_resp)
                else:
                    return self.show_payment_response(card_resp)
            else:
                payment = self.action_create_payment()
                transactions = payment._create_payment_transaction()
                transactions.write({
                    'payment_id': payment.id,
                    'sale_order_ids': [self.order_id.id],
                    'invoice_ids': False,
                    'reference': self.order_id.name,
                    'transaction_type': self.transaction_type,
                })
                resp = transactions.with_context({'full_amount': True, 'pre_auth_order': True})._send_payment_request()
                transactions.update({
                    'invoice_ids': False,
                })
                avs_result = self.get_avs_result(resp)
                if all([x == 'Match' for x in avs_result]) and resp['ResultCode'] == 'A':
                    pass
                else:
                    return self.with_context(
                        {'transaction_id': transactions.id, 'full_amount': True}).show_payment_response(resp)
                order.transaction_ids = [transactions.id]
                payment.payment_transaction_id = transactions.id
                payment.ref = transactions.reference or self.memo
                transactions._set_authorized()
                if not self.card_save and not self.card_id:
                    self.payment_token_id.delete_payment_method()
                    self.partner_id.refresh_payment_methods()
                context = self.action_check_surcharge(transactions)
                return self.message_wizard(context)
        elif self.token_type == 'ach':
            if not self.ach_id:
                token_id = self.create_bank_account().id
                self.payment_token_id = token_id
            else:
                self.payment_token_id = self.ach_id.id

            payment = self.action_create_payment()
            transactions = payment._create_payment_transaction()
            transactions.write({
                'payment_id': payment.id,
                'sale_order_ids': [self.order_id.id],
                'invoice_ids': False,
                'reference': self.order_id.name,
                'transaction_type': self.transaction_type,
            })
            resp = transactions.with_context({'pre_auth_order': True})._send_payment_request()
            transactions.update({
                'invoice_ids': False,
            })
            if not self.bank_account_save and not self.ach_id:
                self.payment_token_id.delete_payment_method()
                self.partner_id.refresh_payment_methods()
            context = self.action_check_surcharge(payment.payment_transaction_id)
            return self.message_wizard(context)
        elif self.token_type == 'emv_device' and self.emv_device_id:
            line_list = []
            emv_command = 'sale'
            if self.transaction_type == 'pre_auth' and self.ebiz_profile_id.is_emv_pre_auth:
                emv_command = 'AuthOnly'
            for line in self.order_id.order_line:
                line_list.append((0, 0, {
                    "name": line.product_id.name,
                    "description": line.product_id.name,
                    "list_price": line.price_unit,
                    "sku": line.product_id.default_code,
                    "commoditycode": line.product_id.default_code,
                    "discountamount": line.discount,
                    "discountrate": "0",
                    "taxable": True,
                    "taxamount":line.price_tax,
                    'qty': line.product_qty,
                    'price_unit': line.price_unit,
                    'price_subtotal': line.price_subtotal,
                }))
            device_value = {
                'devicekey': self.emv_device_id.source_key,
                'pin': self.ebiz_profile_id.pin,
                'journal_id': self.journal_id.id,
                'email_sent': True if self.ebiz_send_receipt else False, 
                'amount': self.amount,
                'partner_id': self.partner_id.id,
                'payment_date': self.date,
                'invoice': self.order_id.name,
                'sale_id': self.order_id.id,
                'command': emv_command,
                "ponum": self.order_id.client_order_ref or self.order_id.name,
                "orderid": self.order_id.name,
                "description": self.order_id.name,
                "billing_address": {
                    "company": self.partner_id.company_name,
                    "street": str(self.partner_id.street2) + str(self.partner_id.street2),
                    "postalcode": self.partner_id.zip, },
                "shipping_address": {
                    "company": self.partner_id.company_name,
                    "street": str(self.partner_id.street2) + str(self.partner_id.street2),
                    "postalcode": self.partner_id.zip, },
                'emv_device_ids': line_list,
            }
            emv_device_transaction = self.env['emv.device.transaction'].create(device_value)
            emv_device_transaction.sale_id.emv_transaction_id = emv_device_transaction.id
            emv_device_transaction.action_post()
            emv_device_transaction.sale_id.log_status_emv = "Transaction Sent to Selected Device: "+str(self.emv_device_id.name)
            context = dict()
            context['message'] =  'Transaction has been successfully sent to the device!'
            context['default_transaction_id'] = emv_device_transaction.id
            return self.message_wizard(context)
        else:
            raise ValidationError(_('Please select any payment method [Credit Card/Bank Account]!'))

    def action_check_surcharge(self, transaction):
        context = dict()
        eligible = False
        if transaction.is_pay_method_eligible and transaction.is_zip_code_allowed:
            eligible = True
        context['message'] = 'Transaction has been successfully processed!'
        context['default_is_ach'] = False if self.token_type == 'credit' else True
        context['default_is_surcharge'] = True if self.partner_id.ebiz_profile_id.is_surcharge_enabled else False
        context['default_is_eligible'] = eligible
        context['default_surcharge_subtotal'] = transaction.payment_id.amount
        context['default_surcharge_amount'] = transaction.surcharge_amt
        context[
            'default_surcharge_percentage'] = transaction.surcharge_percent
        context['default_surcharge_total'] = transaction.payment_id.amount + float(
            transaction.surcharge_amt)
        context['default_currency_id'] = self.env.company.currency_id.id
        context['default_partner_id'] = transaction.token_id.partner_id.name if transaction.token_id else transaction.partner_id.name
        context['default_transaction_type'] = 'Auth Only' if transaction.transaction_type=='pre_auth' else 'Sale'
        context['default_surcharge_percent'] = str(transaction.surcharge_percent) +' %'
        context['default_document_number'] = transaction.reference
        context['default_reference_number'] = transaction.provider_reference
        context['default_auth_code'] = transaction.ebiz_auth_code
        display_name = transaction.token_id.get_encrypted_name() if transaction.token_id else transaction.partner_id.name
        context['default_payment_method'] = display_name
        context['default_date_paid'] = transaction.last_state_change
        context['default_subtotal'] = transaction.amount
        context['default_avs_street'] = transaction.payment_id.ebiz_avs_street if transaction.payment_id.ebiz_avs_street else transaction.ebiz_avs_street
        context['default_avs_zip_code'] = transaction.payment_id.ebiz_avs_zip if transaction.payment_id.ebiz_avs_zip else transaction.ebiz_avs_zip_code
        context['default_cvv'] = transaction.ebiz_cvv_resp
        return context

    def action_create_payment(self):
        ebiz_method = self.env['account.payment.method.line'].search(
            [('journal_id', '=', self.journal_id.id), ('payment_method_id.code', '=', 'ebizcharge')], limit=1)

        payment = self.env['account.payment'].sudo().create({
            'journal_id': self.journal_id.id,
            'payment_method_id': ebiz_method.payment_method_id.id if ebiz_method else False,
            'payment_method_line_id': ebiz_method.id if ebiz_method else False,
            'payment_token_id': self.payment_token_id.id,
            'amount': abs(self.amount),
            'partner_id': self.sub_partner_id.id,
            'partner_type': 'customer',
            'payment_type': 'inbound',
            'ebiz_avs_street': self.ebiz_avs_street,
            'ebiz_avs_zip': self.ebiz_avs_zip,
            'ebiz_send_receipt': self.ebiz_send_receipt,
            'ebiz_receipt_emails': self.ebiz_receipt_emails,
        })
        return payment

    def message_wizard(self, context):
        return {
            'name': 'Success',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'message.wizard',
            'view_id': False,
            'type': 'ir.actions.act_window',
            'target': 'new',
            'context': context
        }

    def create_bank_account(self):
        if not self.partner_id.ebiz_internal_id:
            self.partner_id.sync_to_ebiz()
        method = self.env.ref('payment_ebizcharge_crm.payment_method_ebizcharge').id
        params = {
            "account_holder_name": self.ach_account_holder_name,
            'payment_method_id': method,
            "payment_details": self.ach_account,
            "account_number": self.ach_account,
            "account_type": self.ach_account_type,
            "routing": self.ach_routing,
            "partner_id": self.partner_id.id,
            "ebiz_internal_id": self.partner_id.ebiz_internal_id,
            "token_type": 'ach',
            "provider_ref": "Temp",
            'provider_id': self.env['payment.provider'].search(
                [('company_id', '=', self.order_id.company_id.id), ('code', '=', 'ebizcharge')]).id
        }
        resp = self.create_ebiz_payment_method(params, 'bank')
        del params['ebiz_internal_id']
        params.update({
            'ebizcharge_profile': resp,
            "user_id": self.env.user.id,
            "is_card_save": True,
            "active": True,
        })
        token = self.env['payment.token'].with_context({'from_wizard': True}).create(params)
        token.action_sync_token_to_ebiz()
        return token

    def run_new_card_flow(self):
        self.ensure_one()
        if self.env.context.get('avs_bypass'):
            return True
        avs_action = False
        use_full_amount_for_avs = False
        if self.partner_id.ebiz_profile_id:
            avs_action = self.partner_id.ebiz_profile_id.merchant_card_verification
            use_full_amount_for_avs = self.partner_id.ebiz_profile_id.use_full_amount_for_avs

        if avs_action == 'minimum-amount':
            resp = self.credit_card_validate_transaction()
            avs_result = self.get_avs_result(resp)
            if all([x == 'Match' for x in avs_result]) and resp['ResultCode'] == 'A':
                return True, avs_result, resp
            else:
                return self.show_payment_response(resp), None, None
        elif avs_action == "full-amount":
            self.full_amount = True
            return True, None, None
        elif avs_action == 'no-validation':
            if strtobool(use_full_amount_for_avs):
                self.full_amount = True
                return True, None, None
            else:
                resp = self.credit_card_validate_transaction()
                avs_result = self.get_avs_result(resp)
                if all([x == 'Match' for x in avs_result]) and resp['ResultCode'] == 'A':
                    return True, avs_result, resp
                else:
                    return self.show_payment_response(resp), None, None

        return True, True, True

    def validate_card_runcustomertransaction(self):
        try:
            security_code = self.security_code
            instance = None
            if self.partner_id.ebiz_profile_id:
                instance = self.partner_id.ebiz_profile_id

            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            params = {
                "securityToken": ebiz._generate_security_json(),
                "custNum": self.partner_id.ebizcharge_customer_token,
                "paymentMethodID": self.card_id.ebizcharge_profile,
                "tran": {
                    "isRecurring": False,
                    "IgnoreDuplicate": False,
                    "Details": self.partner_id.transaction_details(),
                    "Software": 'Odoo CRM',
                    "MerchReceipt": True,
                    "CustReceiptName": '',
                    "CustReceiptEmail": '',
                    "CustReceipt": False,
                    "CardCode": security_code,
                    "Command": 'AuthOnly',
                },
            }
            resp = ebiz.client.service.runCustomerTransaction(**params)
            resp_void = ebiz.execute_transaction(resp['RefNum'], {'command': 'Void'})
        except Exception as e:
            _logger.exception(e)
            raise ValidationError(e)
        return resp

    def credit_card_validate_transaction(self):
        try:
            instance = None
            if self.partner_id.ebiz_profile_id:
                instance = self.partner_id.ebiz_profile_id

            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            params = {
                "securityToken": ebiz._generate_security_json(),
                "tran": {
                    "IgnoreDuplicate": False,
                    "IsRecurring": False,
                    "Software": 'Odoo CRM',
                    "CustReceipt": False,
                    "Command": 'AuthOnly',
                    "Details": self.partner_id.transaction_details(),
                    "CustomerID": self.partner_id.id,
                    "CreditCardData": self._get_credit_card_dict(),
                    "AccountHolder": self.card_account_holder_name,
                }
            }
            resp = ebiz.client.service.runTransaction(**params)
            resp_void = ebiz.execute_transaction(resp['RefNum'], {'command': 'Void'})
        except Exception as e:
            raise ValidationError(e)
        return resp

    def _get_credit_card_dict(self, existing_card=None):
        if not existing_card:
            return {
                'InternalCardAuth': False,
                'CardPresent': False,
                'CardNumber': self.card_card_number,
                "CardExpiration": "%s%s" % (self.card_exp_month, self.card_exp_year[2:]),
                'CardCode': self.card_card_code,
                'AvsStreet': self.card_avs_street,
                'AvsZip': self.card_avs_zip,
            }
        else:
            return {
                'InternalCardAuth': False,
                'CardPresent': False,
                'CardNumber': self.card_card_number,
                "CardExpiration": "%s%s" % (self.card_exp_month, self.card_exp_year[2:]),
                'CardCode': self.card_card_code,
                'AvsStreet': self.card_avs_street,
                'AvsZip': self.card_avs_zip,
            }

    def create_ebiz_payment_method(self, params_dict, type=None):
        instance = self.partner_id.ebiz_profile_id
        ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
        if not type:
            resp = ebiz.add_customer_payment_profile(profile=params_dict)
        else:
            resp = ebiz.add_customer_payment_profile(profile=params_dict, p_type=type)
        return resp

    def create_credit_card_payment_method(self):
        if not self.partner_id.ebiz_internal_id:
            self.partner_id.sync_to_ebiz()
        method = self.env.ref('payment_ebizcharge_crm.payment_method_ebizcharge').id
        params = {
            "account_holder_name": self.card_account_holder_name,
            'payment_method_id': method,
            "card_number": self.card_card_number,
            "payment_details": self.card_card_number,
            "card_exp_year": self.card_exp_year,
            "card_exp_month": self.card_exp_month,
            "avs_street": self.card_avs_street,
            "avs_zip": self.card_avs_zip,
            "card_code": self.card_card_code,
            "partner_id": self.sub_partner_id.id,
            "ebiz_internal_id": self.partner_id.ebiz_internal_id,
            "provider_ref": "Temp",
            'provider_id': self.env['payment.provider'].search(
                [('company_id', '=', self.env.company.id), ('code', '=', 'ebizcharge')]).id
        }
        resp = self.create_ebiz_payment_method(params)
        del params['ebiz_internal_id']
        params.update({
            'ebizcharge_profile': resp,
            "user_id": self.env.user.id,
            "is_card_save": True,
            "active": True,
        })
        token = self.env['payment.token'].with_context({'from_wizard': True,'donot_sync': True}).create(params)
        token.action_sync_token_to_ebiz()
        return token

    def reset_credit_card_fields(self):
        self.write({
            "card_card_number": None,
            "card_exp_year": None,
            "card_exp_month": None,
            "card_avs_street": None,
            "card_avs_zip": None,
            "card_card_code": None,
            "card_card_type": None
        })

    def get_avs_result(self, resp):
        card_code = ''
        if resp['CardCodeResultCode'] == 'M':
            card_code = 'Match'
        elif resp['CardCodeResultCode'] == 'N':
            card_code = 'No Match'
        elif resp['CardCodeResultCode'] == 'P':
            card_code = 'Not Processed'
        elif resp['CardCodeResultCode'] == 'S':
            card_code = 'Should be on card but not so indicated'
        elif resp['CardCodeResultCode'] == 'U':
            card_code = 'Issuer Not Certified'
        elif resp['CardCodeResultCode'] == 'X':
            card_code = 'No response from association'
        elif resp['CardCodeResultCode'] == '':
            card_code = 'No CVV2/CVC data available for transaction'

        avs = resp['AvsResultCode']
        address, zip_code = 'No Match', 'No Match'

        if avs in ['YYY', 'Y', 'YYA', 'YYD']:
            address = zip_code = 'Match'
        if avs in ['NYZ', 'Z']:
            zip_code = 'Match'
        if avs in ['YNA', 'A', 'YNY']:
            address = 'Match'
        if avs in ['YYX', 'X']:
            address = zip_code = 'Match'
        if avs in ['NYW', 'W']:
            zip_code = 'Match'
        if avs in ['GGG', 'D']:
            address = zip_code = 'Match'
        if avs in ['YGG', 'P']:
            zip_code = 'Match'
        if avs in ['YYG', 'B', 'M']:
            address = 'Match'
        if address == 'No Match':
            address = resp['AvsResult']
        if zip_code == 'No Match':
            zip_code = resp['AvsResult']
        self.ebiz_avs_street = address
        self.ebiz_avs_zip = zip_code
        return card_code.strip(), address.strip(), zip_code.strip()

    def show_payment_response(self, resp, my_full_amount=None, customer_token=None, payment_method_id=None,
                              bypass_newcard_avs=None, saved_avs_card=None, ebizcharge_profile=None):
        action = self.env.ref('payment_ebizcharge_crm.action_wizard_view_sale_order_transaction_validation').read()[0]
        if resp['ResultCode'] == 'E':
            raise ValidationError(resp['Error'])
        card_code, address, zip_code = self.get_avs_result(resp)
        validation_params = {'address': address,
                             'zip_code': zip_code,
                             'card_code': card_code,
                             'order_id': self.order_id.id,
                             'is_card_denied': False,
                             'wizard_process_id': self.id,
                             'transaction_id': self.env.context[
                                 'transaction_id'] if 'transaction_id' in self.env.context else False,
                             'check_avs_match': all([x == "Match" for x in [card_code, address, zip_code]])}

        if resp['ResultCode'] == 'D':
            validation_params['is_card_denied'] = True
            validation_params['denied_message'] = 'Card Declined' if 'Card Declined' in resp['Error'] else resp['Error']
            action['name'] = 'Card Declined'
        wiz = self.env['wizard.ebiz.sale.order.transaction.validation'].create(validation_params)
        action['res_id'] = wiz.id
        return action

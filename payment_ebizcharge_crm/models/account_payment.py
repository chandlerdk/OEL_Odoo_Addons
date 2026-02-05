# -*- coding: utf-8 -*-
from odoo import models, api, fields, _
import logging
from odoo.exceptions import UserError, ValidationError, AccessError
from datetime import datetime
from .ebiz_charge import message_wizard
from ..utils import strtobool
import ast

_logger = logging.getLogger(__name__)


def to_dict(resp):
    if not resp:
        return {}

    if isinstance(resp, dict):
        if "__values__" in resp:
            return resp["__values__"]
        return resp

    d = getattr(resp, "__dict__", {})
    if "__values__" in d:
        return d["__values__"]
    return d


class AccountPayments(models.Model):
    _inherit = "account.payment"

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

    def _get_transaction_command(self):
        dep = ('Sale', 'Deposit')
        return [dep]

    card_id = fields.Many2one('payment.token', string='Saved Card')
    security_code = fields.Char(string='Security Code')
    required_security_code = fields.Boolean(string="Is Required Security Code")
    ach_id = fields.Many2one('payment.token', string='Saved Bank Account')
    token_type = fields.Selection([('ach', 'ACH'), ('credit', 'Credit Card')], string='Payment Token Type')
    transaction_command = fields.Selection(_get_transaction_command, string='Transaction Command', default="Sale")
    card_account_holder_name = fields.Char(string='Name on Card *')
    card_card_number = fields.Char(string='Card Number *')
    card_exp_year = fields.Selection(year_selection, string='Expiration Year *')
    card_exp_month = fields.Selection(month_selection, string='Expiration Month *')
    card_avs_street = fields.Char(string="Billing Address *")
    card_avs_zip = fields.Char(string='Zip / Postal Code *')
    card_card_code = fields.Char(string='Security Code *')
    card_card_type = fields.Char(string='Card Type')
    ach_account_holder_name = fields.Char(string="Account Holder Name *")
    ach_account = fields.Char(string="Account Number *")
    ach_account_type = fields.Selection([('Checking', 'Checking'), ('Savings', 'Savings')], string='Account Type *',
                                        default="Checking")
    ach_routing = fields.Char(string='Routing Number *')
    journal_code = fields.Char(string='Journal Name', compute="_compute_journal_code")
    payment_internal_id = fields.Char(string="Payment Internal Id", compute="_compute_payment_internal_id", store=True)
    sub_partner_id = fields.Many2one('res.partner', string="Sub Partner Id")
    transaction_ref = fields.Char(string='Reference #', compute="_compute_trans_ref", store=False)
    ebiz_send_receipt = fields.Boolean(string='Email Receipt', default=True)
    ebiz_receipt_emails = fields.Char(string='Email list', help="Comma Seperated Email list( email1,email2)")
    ach_functionality_hide = fields.Boolean(compute="check_if_merchant_needs_avs_validation",
                                            string='ach functionality', )
    card_functionality_hide = fields.Boolean(string='Card ach functionality')
    card_save = fields.Boolean(string='Save Card', default=True, readonly=True)
    bank_account_save = fields.Boolean(string='Save Bank Account', default=True, readonly=True)
    full_amount = fields.Boolean(string='Full Amount Check')
    new_card = fields.Boolean(string='New Card Check')
    is_token_valid = fields.Boolean(string='Token Validity')

    ebiz_avs_street = fields.Char(string="AVS Street *")
    ebiz_avs_zip = fields.Char(string='Zip Code *')
    ebiz_transaction_status = fields.Char(string='Transaction Status')
    ebiz_transaction_result = fields.Char(string='Result')

    @api.depends('state')
    def _compute_payment_internal_id(self):
        for payment in self:
            if not payment.payment_internal_id and payment.payment_method_line_id.code == 'ebizcharge':
                payment.ebiz_add_invoice_payment()
            # if there is any transaction mark that paid
            if self.payment_transaction_id:
                self.payment_transaction_id.write({'is_post_processed': True})

    @api.depends('journal_id')
    def check_if_merchant_needs_avs_validation(self):
        """
        Gets Merchant transaction configuration
        """

        get_merchant_data = False
        get_allow_credit_card_pay = False
        for payment in self:
            if payment.partner_id.ebiz_profile_id:
                get_merchant_data = payment.partner_id.ebiz_profile_id.merchant_data
                get_allow_credit_card_pay = payment.partner_id.ebiz_profile_id.allow_credit_card_pay
            payment.ach_functionality_hide = get_merchant_data
            payment.card_functionality_hide = get_allow_credit_card_pay

    @api.onchange('ebiz_send_receipt')
    def _compute_emails(self):
        if self.ebiz_send_receipt:
            self.ebiz_receipt_emails = self.sub_partner_id.email

    @api.constrains('card_avs_zip')
    def card_avs_zip_length_id(self):
        if self.env['payment.provider'].search(
                [('company_id', '=', self.company_id.id),
                 ('code', '=', 'ebizcharge')]).journal_id.name == self.journal_id.name:
            for rec in self:
                if rec.card_avs_zip and rec.payment_method_line_id.code == 'ebizcharge':
                    if len(rec.card_avs_zip) > 15:
                        raise UserError(_('Zip / Postal Code must be less than 15 Digits!'))
                    elif '-' in rec.card_avs_zip:
                        for mystr in rec.card_avs_zip.split('-'):
                            if not mystr.isalnum():
                                raise UserError(_("Zip/Postal Code can only include numbers, letters, and '-'."))
                    elif not rec.card_avs_zip.isalnum():
                        raise UserError(_("Zip/Postal Code can only include numbers, letters, and '-'."))

    @api.constrains('card_card_number')
    def card_card_number_length_id(self):
        if self.env['payment.provider'].search(
                [('company_id', '=', self.company_id.id),
                 ('code', '=', 'ebizcharge')]).journal_id.name == self.journal_id.name:
            for rec in self:
                if rec.token_type == 'credit' and rec.payment_method_line_id.code == 'ebizcharge':
                    if rec.card_card_number and (len(rec.card_card_number) > 19 or len(rec.card_card_number) < 13):
                        raise UserError(_('Card number should be valid and should be 13-19 digits!'))

    @api.constrains('amount')
    def _constraint_min_amount(self):
        if self.env['payment.provider'].search(
                [('company_id', '=', self.company_id.id),
                 ('code', '=', 'ebizcharge')]).journal_id.name == self.journal_id.name:
            for rec in self:
                if rec.journal_id and rec.payment_method_line_id.code == 'ebizcharge':
                    ebiz_method = self.env['account.payment.method.line'].search(
                        [('journal_id', '=', rec.journal_id.id), ('payment_method_id.code', '=', 'ebizcharge')],
                        limit=1)
                    if rec.amount == 0 and ebiz_method.code == 'ebizcharge':
                        raise UserError(_('Payment amount must be greater than 0'))

    @api.constrains('ach_account')
    def ach_acc_number_length_id(self):
        if self.env['payment.provider'].search(
                [('company_id', '=', self.company_id.id),
                 ('code', '=', 'ebizcharge')]).journal_id.name == self.journal_id.name:
            for rec in self:
                if rec.token_type == 'ach' and rec.payment_method_line_id.code == 'ebizcharge':
                    ebiz_method = self.env['account.payment.method.line'].search(
                        [('journal_id', '=', rec.journal_id.id), ('payment_method_id.code', '=', 'ebizcharge')],
                        limit=1)
                    if rec.ach_account and ebiz_method.code == 'ebizcharge':
                        if not rec.ach_account.isnumeric():
                            raise UserError(_('Account number must be numeric only!'))
                        elif rec.ach_account and not (len(rec.ach_account) >= 4 and len(rec.ach_account) <= 17):
                            raise UserError(_('Account number should be 4-17 digits!'))

    @api.constrains('ach_routing')
    def ach_routing_number_length_id(self):
        if self.env['payment.provider'].search(
                [('company_id', '=', self.company_id.id),
                 ('code', '=', 'ebizcharge')]).journal_id.name == self.journal_id.name:
            for rec in self:
                if rec.token_type == 'ach' and rec.payment_method_line_id.code == 'ebizcharge':
                    if rec.ach_routing and len(rec.ach_routing) != 9:
                        raise UserError(_('Routing number must be 9 digits!'))

    @api.constrains('card_card_code')
    def card_card_code_length(self):
        if self.env['payment.provider'].search(
                [('company_id', '=', self.company_id.id),
                 ('code', '=', 'ebizcharge')]).journal_id.name == self.journal_id.name:
            for rec in self:
                if rec.token_type == 'credit' and rec.payment_method_line_id.code == 'ebizcharge':
                    if rec.card_card_code and (len(rec.card_card_code) != 3 and len(rec.card_card_code) != 4):
                        raise UserError(_('Security code must be 3-4 digits.'))

    @api.constrains('security_code')
    def card_card_code_length_security_code(self):
        if self.env['payment.provider'].search(
                [('company_id', '=', self.company_id.id),
                 ('code', '=', 'ebizcharge')]).journal_id.name == self.journal_id.name:
            for rec in self:
                if rec.token_type == 'credit' and rec.payment_method_line_id.code == 'ebizcharge':
                    if rec.security_code and (len(rec.security_code) != 3 and len(rec.security_code) != 4):
                        raise UserError(_('Security code must be 3-4 digits.'))

    @api.constrains('card_exp_month', 'card_exp_year')
    def card_expiry_date(self):
        if self.env['payment.provider'].search(
                [('company_id', '=', self.company_id.id),
                 ('code', '=', 'ebizcharge')]).journal_id.name == self.journal_id.name:
            today = datetime.now()
            for rec in self:
                if rec.token_type == 'credit' and rec.card_exp_month and rec.card_exp_year and rec.payment_method_line_id.code == 'ebizcharge':
                    if int(rec.card_exp_year) > today.year:
                        return
                    elif int(rec.card_exp_year) == today.year:
                        if int(rec.card_exp_month) >= today.month:
                            return
                    raise UserError(_('Card is expired!'))

    @api.depends('payment_transaction_id')
    def _compute_trans_ref(self):
        for transaction in self:
            transaction.transaction_ref = transaction.payment_transaction_id.provider_reference if transaction.payment_transaction_id else ""
            transaction.ebiz_transaction_status = transaction.payment_transaction_id.ebiz_transaction_status if transaction else ''
            transaction.ebiz_transaction_result =  transaction.payment_transaction_id.ebiz_transaction_result if transaction else ''

    def action_post(self):
        if self.payment_method_line_id.code != "ebizcharge":
            return super(AccountPayments, self).action_post()
        provider_obj = self.env['payment.provider'].search([('company_id', '=', self.company_id.id),('code', '=', 'ebizcharge')])
        if provider_obj.journal_id.name == self.journal_id.name and len(self) > 1 and self.payment_method_line_id.code=="ebizcharge":
            raise UserError('Unable to process more than 1 invoice.')
        elif len(self) > 1:
            return super(AccountPayments, self).action_post()
        if self.payment_method_line_id.code == "ebizcharge" and provider_obj.journal_id.name == self.journal_id.name and not self.partner_id.country_id and not self.sub_partner_id.country_id and not \
                self.env.company.country_id.id:
            raise UserError("Please enter the country for the customer or for the User.")
        self_data = {}
        if self.partner_id.ebiz_profile_id:
            pass
        else:
            default_instance = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_default', '=', True)], limit=1)
            if default_instance:
                self.partner_id.update({
                    'ebiz_profile_id': default_instance.id,
                })

        if 'payment_data' in self._context:
            self_data = self._context['payment_data']
            self.card_id = self_data['tokenid'] if 'tokenid' in self_data else False
            self.ebiz_send_receipt = self_data['ebiz_send_receipt']
            self.ebiz_receipt_emails = self_data['ebiz_receipt_emails']
            if 'token_type' in self_data:
                self.token_type = self_data['token_type']

        avs_by_pass_check = False
        if 'avs_bypass' in self._context:
            if self._context['avs_bypass']:
                avs_by_pass_check = True

        success_message_keyword = "processed"
        self.full_amount = False
        self.new_card = False
        use_full_amount_for_avs = False
        merchant_card_verification = False
        if self.partner_id.ebiz_profile_id:
            merchant_card_verification = self.partner_id.ebiz_profile_id.merchant_card_verification
            use_full_amount_for_avs = self.partner_id.ebiz_profile_id.use_full_amount_for_avs

        if self.env.context.get('batch_processing'):
            command = self.transaction_command
            payments_need_trans = self.filtered(lambda pay: pay.payment_token_id and not pay.payment_transaction_id)
            transactions = payments_need_trans._create_payment_transaction()
            resp = transactions._send_payment_request()

            if resp['ResultCode'] == 'A':
                self.action_create_receipt(resp, transactions)
                res = super(AccountPayments, self).action_post()
                transactions._set_done()
                return True
            if resp['ResultCode'] in ["D", "E"]:
                transactions.write({
                    'state_message': resp['Error'],
                    'provider_reference': resp['RefNum'],
                    'ebiz_auth_code': resp['AuthCode'],
                })
                transactions._set_canceled()
                if transactions.payment_id.move_id.state == 'draft':
                    transactions.payment_id.action_cancel()
                return False

        if self.env.context.get('do_not_run_transaction'):
            self.payment_token_id = None
            super(AccountPayments, self).action_post()
            return True

        if not self.payment_transaction_id and self.payment_method_line_id.code=="ebizcharge" and self_data:
            my_card_no = False
            if self.env.context.get('pass_validation'):
                self.payment_token_id = None
                super(AccountPayments, self).action_post()
                return True

            elif 'token_type' in self_data and self_data['token_type'] == 'credit':
                command = self.transaction_command
                if not self_data['card_id']:
                    my_card_no = self_data['card_card_number']
                    self.new_card = True
                    resp = self.run_new_card_flow()
                    if self.env.context.get('get_customer_profile'):
                        self.partner_id.with_context({'donot_sync': True}).ebiz_get_payment_methods()
                        self.payment_token_id = self.env['payment.token'].search(
                            [('ebizcharge_profile', '=', self.env.context.get('get_customer_profile'))])
                    elif resp and type(resp) == bool:
                        token_id = self.create_credit_card_payment_method().id
                        self.payment_token_id = token_id
                    else:
                        return resp
                else:
                    if strtobool(use_full_amount_for_avs) or avs_by_pass_check:
                        self.full_amount = True
                    else:
                        if self.payment_method_line_id.code=="ebizcharge":
                            resp = self.validate_card_runcustomertransaction()
                            avs_result = self.get_avs_result(resp)
                            if all([x == 'Match' for x in avs_result]) and resp['ResultCode'] == 'A':
                                pass
                            else:
                                return self.show_payment_response(resp, bypass_newcard_avs=True, saved_avs_card=True,
                                                                  ebizcharge_profile=self.payment_token_id.ebizcharge_profile)
            elif 'token_type' in self_data and self_data['token_type'] == 'ach':
                command = "Check"
                if not self_data['ach_id']:
                    token_id = self.create_bank_account().id
                    self.payment_token_id = token_id
            else:
                raise UserError(_('Please select any payment method [Credit Card/Bank Account]!'))

            payments_need_trans = self.filtered(lambda pay: pay.payment_token_id and not pay.payment_transaction_id)
            transactions = payments_need_trans._create_payment_transaction()
            if self_data['card_id']:
                token_exist = self.env['payment.token'].sudo().search([('id', '=', self_data['tokenid'])], limit=1)
                if self_data['security_code']:
                    token_exist.card_code = self_data['security_code']

            if merchant_card_verification and self.token_type == 'credit' and not self_data['card_id']:
                resp = transactions.with_context({'run_transaction': True,'command': command, 'card': my_card_no})._send_payment_request()
            else:
                resp = transactions._send_payment_request()

            self.action_create_receipt(resp, transactions)
            avs_result = self.get_avs_result(resp)
            if resp['ResultCode'] == 'A':
                if self.journal_code == 'EBIZC:credit_note':
                    if command in ['Check', 'Sale']:
                        res = super(AccountPayments, self).action_post()
                        transactions._set_done()
                        # transactions._log_received_message()
                    self.action_update_payment_methods(self_data)
                    return message_wizard('Transaction has been successfully {}!'.format(success_message_keyword))
                # on successful invoice add payment on the invoice
                proceed = False
                # full_amount will only be set true if the transaction is with new card
                # so it will check for avs
                if self.full_amount:
                    if all([x == 'Match' for x in avs_result]):
                        proceed = True
                else:
                    proceed = True

                if proceed or avs_by_pass_check:
                    if command in ['Check', 'Sale']:
                        self.write({'partner_id': self.partner_id.id})
                        res = super(AccountPayments, self).action_post()
                        transactions._set_done()
                    self.action_update_payment_methods(self_data)
                    return message_wizard('Transaction has been successfully {}!'.format(success_message_keyword))
                else:
                    if merchant_card_verification:
                        return self.show_payment_response(resp,
                                                          customer_token=self.payment_token_id.partner_id.ebizcharge_customer_token,
                                                          payment_method_id=self.payment_token_id.ebizcharge_profile)
                    elif self.card_id and not self.new_card:
                        return self.show_payment_response(resp, bypass_newcard_avs=True)
                    else:
                        return self.show_payment_response(resp)
            else:
                return self.show_payment_response(resp)
        else:
            res = super(AccountPayments, self).action_post()

        if 'payment_data' in self._context:
            if 'to_reconcile' in self._context['payment_data']:
                self.ebiz_reconcile_payment(source='payment_data')
        return True

    def action_create_receipt(self, resp, transactions):
        resp_dict = to_dict(resp)
        if resp_dict and resp_dict.get('RefNum'):
            receipt = self.env['account.move.receipts'].create({
                'invoice_id': self.env['account.move'].search([('name', '=', self.ref)]).id,
                'name': self.env.user.currency_id.symbol + str(transactions.amount) + ' Paid On ' +
                        str(datetime.now().date()),
                'ref_nums': resp_dict['RefNum'],
            })

    def action_update_payment_methods(self, self_data):
        if not self_data['card_save'] and not self.card_id:
            self.payment_token_id.delete_payment_method()
            self.partner_id.refresh_payment_methods()
        if not self_data['ach_save'] and not self.ach_id:
            self.payment_token_id.delete_payment_method()
            self.partner_id.refresh_payment_methods()

    def ebiz_reconcile_payment(self, source=False):
        if source:
            to_process = self.env['account.move.line'].search(
                [('id', 'in', self._context['payment_data']['to_reconcile'])])
        else:
            to_process = self.line_ids.filtered_domain([('debit', '>', 0)])

        domain = [
            ('parent_state', '=', 'posted'),
            ('account_type', 'in', ('asset_receivable', 'liability_payable')),
            ('reconciled', '=', False)]
        for vals in to_process:
            payment_lines = self.line_ids.filtered_domain(domain)
            lines = to_process
            for account in payment_lines.account_id:
                (payment_lines + lines).filtered_domain(
                    [('account_id', '=', account.id), ('reconciled', '=', False)]).reconcile()

    def action_send_email_receipt(self):
        if self.send_email_receipt:
            if self.payment_transaction_id and self.payment_transaction_id.state in ['authorized', 'done']:
                emails = self.ebiz_receipt_emails.split(',')
                for email in emails:
                    self.email_receipt(email.strip())

    def email_receipt(self, email):
        instance = None
        if self.partner_id.ebiz_profile_id:
            instance = self.partner_id.ebiz_profile_id
        ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
        params = {
            'securityToken': ebiz._generate_security_json(),
            'transactionRefNum': self.payment_transaction_id.provider_reference,
            'receiptRefNum': self.ebiz_receipt_template.receipt_id,
            'receiptName': self.ebiz_receipt_template.name,
            'emailAddress': email,
        }
        form_url = ebiz.client.service.EmailReceipt(**params)

    # @api.model
    # def default_get(self, default_fields):
    #     rec = super(AccountPayments, self).default_get(default_fields)
    #     if 'partner_id' in rec:
    #         if rec['invoice_ids'][0][2]:
    #             invoice_ids = rec['invoice_ids'][0][2]
    #             if len(invoice_ids) > 1:
    #                 return rec
    #             sub_partner_id = self.env['account.move'].browse(invoice_ids).partner_id.id
    #         partner = self.env['res.partner'].browse(rec['partner_id'])
    #         rec.update({
    #             'sub_partner_id': sub_partner_id,
    #             'card_account_holder_name': partner.name,
    #             'card_avs_street': partner.street,
    #             'card_avs_zip': partner.zip,
    #             'ach_account_holder_name': partner.name,
    #         })
    #     return rec

    def refresh_payment_methods(self):
        self.partner_id.with_context({'donot_sync': True}).ebiz_get_payment_methods()
        context = dict()
        context['message'] = 'Payment methods are up to date!'
        return {
            'name': 'Success',
            'view_type': 'form',
            'view_mode': 'form',
            'views': [[False, 'form']],
            'res_model': 'success.payment.methods',
            'view_id': False,
            'type': 'ir.actions.act_window',
            'target': 'new',
            'context': context
        }

    @api.onchange('card_card_number', 'card_exp_month', 'card_exp_year', 'card_card_code')
    def _reset_card_id(self):
        if self.card_card_number or self.card_exp_year or self.card_exp_month or self.card_card_code:
            self.token_type = 'credit'
            self.card_id = None
            self.security_code = None
            self.ach_id = None
            self.ach_account = None
            self.ach_routing = None

    @api.onchange('ach_account', 'ach_routing')
    def _reset_ach_account(self):
        if self.ach_account or self.ach_routing:
            self.token_type = 'ach'
            self.ach_id = None
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
                payment.payment_token_id = payment.ach_id
                payment.ach_account = None
                payment.ach_routing = None
                payment.security_code = None
                payment.card_id = None
                payment.card_card_number = None
                payment.card_exp_year = None
                payment.card_exp_month = None
                payment.card_card_code = None

    def run_new_card_flow(self):
        self.ensure_one()
        if self.env.context.get('avs_bypass'):
            return True
        if self.journal_code == 'EBIZC:credit_note':
            return True
        avs_action = False
        use_full_amount_for_avs = False
        if self.partner_id.ebiz_profile_id:
            avs_action = self.partner_id.ebiz_profile_id.merchant_card_verification
            use_full_amount_for_avs = self.partner_id.ebiz_profile_id.use_full_amount_for_avs

        if avs_action == 'minimum-amount':
            if self.partner_id.ebiz_profile_id.verify_card_before_saving:
                resp = self.credit_card_validate_transaction()
                avs_result = self.get_avs_result(resp)
                if all([x == 'Match' for x in avs_result]) and resp['ResultCode'] == 'A':
                    return True
            else:
                self.full_amount = True
                return True
        elif avs_action == "full-amount":
            self.full_amount = True
            return True
        elif avs_action == 'no-validation':
            if strtobool(use_full_amount_for_avs):
                self.full_amount = True
                return True
            else:
                if self.partner_id.ebiz_profile_id.verify_card_before_saving:
                    resp = self.credit_card_validate_transaction()
                    avs_result = self.get_avs_result(resp)
                    if all([x == 'Match' for x in avs_result]) and resp['ResultCode'] == 'A':
                        return True
                    else:
                        return self.show_payment_response(resp, my_full_amount=True)
                else:
                    self.full_amount = True
                    return True
        return self.show_payment_response(resp)

    def validate_card_runcustomertransaction(self):
        try:
            self_data = self._context['payment_data']
            security_code = self_data['security_code']
            instance = None
            if self.partner_id.ebiz_profile_id:
                instance = self.partner_id.ebiz_profile_id

            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            params = {
                "securityToken": ebiz._generate_security_json(),
                "custNum": self.partner_id.ebizcharge_customer_token,
                "paymentMethodID": self.payment_token_id.ebizcharge_profile,
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
            raise UserError(e)
        return resp

    def show_payment_response(self, resp, my_full_amount=None, customer_token=None, payment_method_id=None,
                              bypass_newcard_avs=None, saved_avs_card=None, ebizcharge_profile=None):
        action = self.env.ref('payment_ebizcharge_crm.action_ebiz_transaction_validation_form').read()[0]
        if resp['ResultCode'] == 'E':
            raise UserError(resp['Error'])

        card_code, address, zip_code = self.get_avs_result(resp)
        transaction_id = self.payment_transaction_id
        validation_params = {'address': address,
                             'zip_code': zip_code,
                             'card_code': card_code,
                             'full_amount_avs': self.full_amount,
                             'payment_id': self.id,
                             'transaction_id': transaction_id.id,
                             'check_avs_match': all([x == "Match" for x in [card_code, address, zip_code]])}
        if not self.new_card and not bypass_newcard_avs and all([x == "Match" for x in [card_code, address, zip_code]]):
            validation_params['check_avs_match'] = True

        if resp['ResultCode'] == 'D':
            validation_params['is_card_denied'] = True
            validation_params['denied_message'] = 'Card Declined' if 'Card Declined' in resp['Error'] else resp['Error']
            action['name'] = 'Card Declined'
        wiz = self.env['wizard.ebiz.transaction.validation'].create(validation_params)
        action['res_id'] = wiz.id
        action['context'] = {'payment_data': self._context['payment_data']}
        if my_full_amount:
            action['context'] = dict(
                my_full_amount=True,
                payment_data=self._context['payment_data'],
            )

        if customer_token and payment_method_id:
            action['context'] = dict(
                customer_token_to_dell=customer_token,
                payment_method_id_to_dell=payment_method_id,
                payment_data=self._context['payment_data'],
            )

        if saved_avs_card:
            action['context'] = dict(
                ebiz_charge_profile=ebizcharge_profile,
                payment_data=self._context['payment_data'],
            )
        return action

    @api.depends('journal_id')
    def _compute_journal_code(self):
        acquirer = self.env['payment.provider'].search(
            [('company_id', '=', self.company_id.id), ('code', '=', 'ebizcharge')])
        journal_id = acquirer.journal_id
        for payment in self:
            if payment.payment_method_line_id.code == 'ebizcharge' and (
                    'payment_data' in self._context or payment.payment_type == 'outbound'):
                if 'active_id' in self._context and 'active_model' in self._context:
                    if self.env['account.move'].search(
                            [('id', '=', self._context['active_id'])]).move_type == 'out_refund':
                        payment.journal_code = 'EBIZC:credit_note'
                    elif 'payment_data' in self._context:
                        payment.journal_code = "EBIZC"
                    else:
                        payment.journal_code = "other"
                else:
                    payment.journal_code = payment.journal_code
            else:
                payment.journal_code = "other"

    @api.onchange('partner_id', 'payment_method_id', 'journal_id')
    def _onchange_set_payment_token_id(self):
        acquirer = self.env['payment.provider'].search(
            [('company_id', '=', self.company_id.id), ('code', '=', 'ebizcharge')])
        journal_id = acquirer.journal_id
        su = super(AccountPayments, self)._onchange_set_payment_token_id()

        if self.invoice_line_ids and self.invoice_line_ids[0].move_id.move_type == "out_refund" and self.payment_method_line_id.code == 'ebizcharge':
            trans_ids = self.invoice_line_ids.reversed_entry_id.transaction_ids
            if trans_ids:
                self.payment_token_id = trans_ids[0].token_id
        return su

    def create_ebiz_payment_method(self, params_dict, type=None):
        try:
            instance = self.partner_id.ebiz_profile_id
            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            if not type:
                resp = ebiz.add_customer_payment_profile(profile=params_dict)
            else:
                resp = ebiz.add_customer_payment_profile(profile=params_dict, p_type=type)
            return resp
        except Exception as e:
            raise ValidationError(str(e))

    def create_credit_card_payment_method(self):
        if not self.partner_id.ebiz_internal_id:
            self.partner_id.sync_to_ebiz()
        self_data = self._context['payment_data']
        params = {
            "account_holder_name": self_data['card_account_holder_name'],
            "card_number": self_data['card_card_number'],
            "payment_details": self_data['card_card_number'],
            "card_exp_year": self_data['card_exp_year'],
            "card_exp_month": self_data['card_exp_month'],
            "avs_street": self_data['card_avs_street'],
            "avs_zip": self_data['card_avs_zip'],
            "card_code": self_data['card_card_code'],
            "partner_id": self_data['sub_partner_id'],
            "provider_ref": "Temp",
            "ebiz_internal_id": self.partner_id.ebiz_internal_id,
            "is_card_save": self_data['card_save'],
            'provider_id': self.env['payment.provider'].search(
                [('company_id', '=', self.company_id.id), ('code', '=', 'ebizcharge')], limit=1).id
        }
        resp = self.create_ebiz_payment_method(params)
        del params['ebiz_internal_id']
        method = self.env.ref('payment_ebizcharge_crm.payment_method_ebizcharge').id
        params.update({
            'payment_method_id':  method,
            'ebizcharge_profile': resp,
            "user_id": self.env.user.id,
            "is_card_save": True,
            "active": True,
        })
        self.reset_credit_card_fields()
        token = self.env['payment.token'].with_context({'from_wizard': True, 'donot_sync': True, }).create(params)
        token.action_sync_token_to_ebiz()
        return token

    def credit_card_validate_transaction(self):
        try:
            instance = None
            if self.partner_id.ebiz_profile_id:
                instance = self.partner_id.ebiz_profile_id

            self_data = self.env.context.get('payment_data')
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
                    "AccountHolder": self_data.get('card_account_holder_name'),
                }
            }
            resp = ebiz.client.service.runTransaction(**params)
            resp_void = ebiz.execute_transaction(resp['RefNum'], {'command': 'Void'})
        except Exception as e:
            _logger.exception(e)
            raise UserError(e)
        return resp

    def _get_credit_card_dict(self):
        if 'payment_data' in self._context:
            self_data = self._context['payment_data']
            return {
                'InternalCardAuth': False,
                'CardPresent': False,
                'CardNumber': self_data['card_card_number'],
                "CardExpiration": "%s%s" % (self_data['card_exp_month'], self_data['card_exp_year'][2:]),
                'CardCode': self_data['card_card_code'],
                'AvsStreet': self_data['card_avs_street'],
                'AvsZip': self_data['card_avs_zip'],
            }

    def create_bank_account(self):
        if not self.partner_id.ebiz_internal_id:
            self.partner_id.sync_to_ebiz()
        self_data = self._context['payment_data']
        params = {
            "account_holder_name": self_data['ach_account_holder_name'],
            "payment_details": self_data['account_number'],
            "account_number": self_data['account_number'],
            "account_type": self_data['account_type'],
            "routing": self_data['routing'],
            "partner_id": self_data['sub_partner_id'],
            "ebiz_internal_id": self.partner_id.ebiz_internal_id,
            "token_type": 'ach',
            "provider_ref": "Temp",
            'provider_id': self.env['payment.provider'].search(
                [('company_id', '=', self.company_id.id), ('code', '=', 'ebizcharge')], limit=1).id
        }
        resp = self.create_ebiz_payment_method(params, 'bank')
        del params['ebiz_internal_id']
        method = self.env.ref('payment_ebizcharge_crm.payment_method_ebizcharge').id
        params.update({
            'payment_method_id': method,
            'ebizcharge_profile': resp,
            "user_id": self.env.user.id,
            "is_card_save": True,
            "active": True,
        })
        token = self.env['payment.token'].with_context({'from_wizard': True}).create(params)
        token.action_sync_token_to_ebiz()
        return token

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

        # if address == 'No Match':
        #     address = resp['AvsResult']
        # if zip_code == 'No Match':
        #     zip_code = resp['AvsResult']
        self.ebiz_avs_street = address
        self.ebiz_avs_zip = zip_code
        return card_code.strip(), address.strip(), zip_code.strip()

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

    def reset_ach_fields(self):
        self.write({
            "ach_account": None,
            "ach_routing": None
        })

    def ebiz_add_invoice_payment(self):
        try:
            if self.state == "posted" and self.reconciled_invoice_ids and self.payment_transaction_id:
                invoice_id = self.reconciled_invoice_ids[0]
                if invoice_id.is_payment_processed:
                    return False
                if not invoice_id.ebiz_internal_id:
                    invoice_id.sync_to_ebiz()
                instance = None
                if self.partner_id.ebiz_profile_id:
                    instance = self.partner_id.ebiz_profile_id
                else:
                    default_instance = self.env['ebizcharge.instance.config'].search(
                        [('is_valid_credential', '=', True), ('is_default', '=', True)], limit=1)
                    if default_instance:
                        instance = default_instance

                ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
                trans_id = self.payment_transaction_id

                if not trans_id:
                    return False

                params = {
                    'securityToken': ebiz._generate_security_json(),
                    'payment': {
                        'CustomerId': self.partner_id.id,
                        'InvoicePaymentDetails': {
                            'InvoicePaymentDetails': [
                                {
                                    'InvoiceInternalId': invoice_id.ebiz_internal_id,
                                    'PaidAmount': trans_id.amount,
                                }
                            ]
                        },
                        'TotalPaidAmount': trans_id.amount,
                        'CustNum': invoice_id.partner_id.ebizcharge_customer_token,
                        'RefNum': trans_id.provider_reference,
                        'PaymentMethodType': 'CreditCard' if trans_id.token_id.token_type == 'credit' else 'ACH',
                        'PaymentMethodId': trans_id.token_id.id,
                    }
                }
                resp = ebiz.client.service.AddInvoicePayment(**params)
                if resp['StatusCode'] == 1:
                    self.payment_internal_id = resp['PaymentInternalId']
                    mark_resp = ebiz.client.service.MarkPaymentAsApplied(**{
                        'securityToken': ebiz._generate_security_json(),
                        'paymentInternalId': self.payment_internal_id,
                        'invoiceNumber': invoice_id.name
                    })
                return resp
        except Exception as e:
            _logger.exception(e)
            raise UserError(str(e))

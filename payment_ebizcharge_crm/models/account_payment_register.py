# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from datetime import datetime
import logging

_logger = logging.getLogger(__name__)

from ..utils import strtobool


class AccountPaymentRegister(models.TransientModel):
    _inherit = "account.payment.register"

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

    @api.depends('partner_id', 'partner_id.ebiz_profile_id')
    def _compute_required_sc(self):
        ebiz_profile_id = False
        is_ebiz_profile = False
        verify_card_before_saving = False
        if self.partner_id.ebiz_profile_id:
            verify_card_before_saving = self.partner_id.ebiz_profile_id.verify_card_before_saving
            is_ebiz_profile = True
            ebiz_profile_id = self.partner_id.ebiz_profile_id.id
        else:
            default_instance = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_default', '=', True)], limit=1)
            if default_instance:
                verify_card_before_saving = default_instance.verify_card_before_saving
                is_ebiz_profile = True
                ebiz_profile_id = default_instance.id

        self.ebiz_profile_id = ebiz_profile_id
        self.is_ebiz_profile = is_ebiz_profile
        self.required_security_code = verify_card_before_saving

    def _get_partner_id(self):
        if 'active_id' in self.env.context:
            return self.env['account.move'].browse(
                self.env.context.get('active_id')).partner_id.id
        elif 'active_ids' in self.env.context:
            if 'active_model' in self.env.context and self.env.context.get('active_model') == 'account.move.line':
                if len(self.env['account.move.line'].browse(self.env.context['active_ids']).exists().move_id.partner_id.ids) > 1:
                    return self.env['account.move.line'].browse(
                        self.env.context['active_ids']).exists().move_id.partner_id[0].id
                else:
                    return self.env['account.move.line'].browse(self.env.context['active_ids']).exists().move_id.partner_id.id
            else:
                return self.env['account.move'].browse(self.env.context.get('active_ids')[0]).exists().partner_id.id
        else:
            return False

    card_id = fields.Many2one('payment.token', string='Saved Card')
    security_code = fields.Char(string='Security Code')
    required_security_code = fields.Boolean(string="Required Security Code")
    ach_id = fields.Many2one('payment.token', string='Saved Bank Account')
    token_type = fields.Selection([('ach', 'ACH'), ('credit', 'Credit Card'), ('emv_device', 'Emv Devoice')], string='Payment Token Type')
    transaction_command = fields.Selection(_get_transaction_command, string='Transaction Command', default="Sale")

    card_account_holder_name = fields.Char(string='Name on Card *')
    card_card_number = fields.Char(string='Card Number *')
    card_exp_year = fields.Selection(year_selection, string='Expiration Year *')
    card_exp_month = fields.Selection(month_selection, string='Expiration Month *')
    card_avs_street = fields.Char(string="Billing Address *")
    card_avs_zip = fields.Char(string='Zip / Postal Code *')
    card_card_code = fields.Char()
    card_card_type = fields.Char(string='Card Type')
    ach_account_holder_name = fields.Char(string="Account Holder Name *")
    ach_account = fields.Char(string="Account Number *")
    ach_account_type = fields.Selection([('Checking', 'Checking'), ('Savings', 'Savings')], string='Account Type *',
                                        default="Checking")
    ach_routing = fields.Char('Routing Number *')
    journal_code = fields.Char(string='Journal Name', compute="_compute_journal_code")
    sub_partner_id = fields.Many2one('res.partner', string="Sub Partner Id", default=_get_partner_id)
    ebiz_send_receipt = fields.Boolean(string='Email Receipt', default=True)
    ebiz_receipt_emails = fields.Char(string='Email list', help="Comma Seperated Email list( email1,email2)")

    ach_functionality_hide = fields.Boolean(compute="check_if_merchant_needs_avs_validation", string='ach functionality')
    card_functionality_hide = fields.Boolean(string='card ach functionality')
    card_save = fields.Boolean(string='Save Card', default=True, readonly=False)
    bank_account_save = fields.Boolean(string='Save Bank Account', default=True, readonly=False)
    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config', string="Merchant Account", compute="_compute_required_sc")
    is_ebiz_profile = fields.Boolean()
    ebiz_sur_char = fields.Char(string='Surcharge Char')
    is_surch_enable = fields.Boolean(string='Surcharge Enabled')
    is_pay_link = fields.Boolean(string='Pay link')
        
    emv_device_id = fields.Many2one('ebizcharge.emv.device', string='EMV Device')
    is_emv_enabled = fields.Boolean(related='ebiz_profile_id.is_emv_enabled', )
    


    @api.depends('journal_id', 'ebiz_profile_id')
    def check_if_merchant_needs_avs_validation(self):
        """
        Gets Merchant transaction configuration
        """
        get_merchant_data = False
        get_allow_credit_card_pay = False
        if self.partner_id.ebiz_profile_id:
            get_merchant_data = self.partner_id.ebiz_profile_id.merchant_data
            get_allow_credit_card_pay = self.partner_id.ebiz_profile_id.allow_credit_card_pay
        
        else:
            default_instance = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_default', '=', True)], limit=1)
            if default_instance:
                get_merchant_data = default_instance.merchant_data
                get_allow_credit_card_pay = default_instance.allow_credit_card_pay
            else:
                get_merchant_data = self.ebiz_profile_id.merchant_data
                get_allow_credit_card_pay = self.ebiz_profile_id.allow_credit_card_pay


        self.ach_functionality_hide = get_merchant_data
        self.card_functionality_hide = get_allow_credit_card_pay

    @api.onchange('ebiz_send_receipt')
    def _compute_emails(self):
        if self.ebiz_send_receipt:
            self.ebiz_receipt_emails = self.partner_id.email

    @api.constrains('card_avs_zip')
    def card_avs_zip_length_id(self):
        for rec in self:
            if rec.card_avs_zip  and rec.payment_method_line_id.code == 'ebizcharge':
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
            if rec.token_type == 'credit'  and rec.payment_method_line_id.code == 'ebizcharge':
                if rec.card_card_number and (len(rec.card_card_number) > 19 or len(rec.card_card_number) < 13):
                    raise ValidationError(_('Card number should be valid and should be 13-19 digits!'))

    @api.constrains('amount')
    def _constraint_min_amount(self):
        for rec in self:
            ebiz_method = self.env['account.payment.method.line'].search(
                [('journal_id', '=', rec.journal_id.id), ('payment_method_id.code', '=', 'ebizcharge')], limit=1)
            if rec.amount == 0 and ebiz_method.code == 'ebizcharge':
                if 'active_model' in self.env.context and self.env.context.get('active_model') == 'account.move.line':
                    if len(self.env['account.move.line'].browse(self.env.context['active_ids']).exists().move_id.ids)== 1:
                        raise ValidationError(_('Payment amount must be greater than 0'))

    @api.constrains('ach_account')
    def ach_acc_number_length_id(self):
        for rec in self:
            if rec.token_type == 'ach':
                if rec.ach_account  and rec.payment_method_line_id.code == 'ebizcharge':
                    if not rec.ach_account.isnumeric():
                        raise ValidationError(_('Account number must be numeric only!'))
                    elif rec.ach_account and not (len(rec.ach_account) >= 4 and len(rec.ach_account) <= 17):
                        raise ValidationError(_('Account number should be 4-17 digits!'))

    @api.constrains('ach_routing')
    def ach_routing_number_length_id(self):
        for rec in self:
            if rec.token_type == 'ach'  and rec.payment_method_line_id.code == 'ebizcharge':
                if rec.ach_routing and len(rec.ach_routing) != 9:
                    raise ValidationError(_('Routing number must be 9 digits!'))

    @api.constrains('card_card_code')
    def card_card_code_length(self):
        for rec in self:
            if rec.token_type == 'credit'  and rec.payment_method_line_id.code == 'ebizcharge':
                if rec.card_card_code and (len(rec.card_card_code) != 3 and len(rec.card_card_code) != 4):
                    raise ValidationError(_('Security code must be 3-4 digits.'))

    @api.constrains('security_code')
    def card_card_code_length_security_code(self):
        for rec in self:
            if rec.token_type == 'credit'  and rec.payment_method_line_id.code == 'ebizcharge':
                if rec.security_code and (len(rec.security_code) != 3 and len(rec.security_code) != 4):
                    raise ValidationError(_('Security code must be 3-4 digits.'))

    @api.constrains('card_exp_month', 'card_exp_year')
    def card_expiry_date(self):
        today = datetime.now()
        for rec in self:
            if rec.token_type == 'credit' and rec.card_exp_month and rec.card_exp_year  and rec.payment_method_line_id.code == 'ebizcharge':
                if int(rec.card_exp_year) > today.year:
                    return
                elif int(rec.card_exp_year) == today.year:
                    if int(rec.card_exp_month) >= today.month:
                        return
                raise ValidationError(_('Card is expired!'))

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
        elif self.ebiz_profile_id:
            instance = self.ebiz_profile_id

        ebiz = self.get_ebiz_charge_obj(instance=instance)
        params = {
            'securityToken': ebiz._generate_security_json(),
            'transactionRefNum': self.payment_transaction_id.provider_reference,
            'receiptRefNum': self.ebiz_receipt_template.receipt_id,
            'receiptName': self.ebiz_receipt_template.name,
            'emailAddress': email,
        }
        form_url = ebiz.client.service.EmailReceipt(**params)

    @api.model
    def default_get(self, default_fields):
        rec = super(AccountPaymentRegister, self).default_get(default_fields)
        if 'sub_partner_id' in rec:
            partner = self.env['res.partner'].browse(rec['sub_partner_id'])
            instance = False
            is_profile = False
            if partner.ebiz_profile_id:
                instance = partner.ebiz_profile_id
                is_profile = True

            is_sur_able = False
            if instance:
                if instance.is_surcharge_enabled and instance.surcharge_type_id == 'DailyDiscount':
                    is_sur_able = True
                rec.update({
                    'is_ebiz_profile': is_profile,
                    'ebiz_sur_char': instance.surcharge_terms,
                    'required_security_code': instance.verify_card_before_saving,
                    'is_surch_enable': is_sur_able,
                    'ebiz_profile_id': instance.id,
                })
            rec.update({
                'sub_partner_id': rec['sub_partner_id'],
                'card_account_holder_name': partner.name,
                'card_avs_street': partner.street,
                'card_avs_zip': partner.zip,
                'ach_account_holder_name': partner.name,
            })
        return rec

    @api.onchange('card_card_number', 'card_exp_month', 'card_exp_year', 'card_card_code')
    def _reset_card_id(self):
        if self.card_card_number or self.card_exp_year or self.card_exp_month or self.card_card_code:
            self.token_type = 'credit'
            self.card_id = None
            self.emv_device_id = None
            self.security_code = None
            self.ach_id = None
            self.ach_account = None
            self.ach_routing = None

    @api.onchange('ach_account', 'ach_routing')
    def _reset_ach_account(self):
        if self.ach_account or self.ach_routing:
            self.token_type = 'ach'
            self.emv_device_id = None
            self.ach_id = None
            self.security_code = None
            self.card_id = None
            self.card_card_number = None
            self.card_exp_year = None
            self.card_exp_month = None
            self.card_card_code = None

    @api.onchange('emv_device_id')
    def _reset_emv_device(self):
        if self.emv_device_id:
            self.token_type = 'emv_device'
            #if self.ebiz_profile_id:
            #    self.ebiz_profile_id.action_get_devices()
            self.ach_account = None
            self.ach_routing = None
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


    @api.onchange('card_id')
    def _reset_new_card_fields(self):
        for payment in self:
            if payment.card_id:
                payment.token_type = 'credit'
                payment.payment_token_id = payment.card_id
                payment.card_card_number = None
                payment.emv_device_id = None
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
                payment.emv_device_id = None
                payment.security_code = None
                payment.card_id = None
                payment.card_card_number = None
                payment.card_exp_year = None
                payment.card_exp_month = None
                payment.card_card_code = None

    def action_register_payment(self):
        active_ids = self.env.context.get('active_ids')
        if len(active_ids) == 1:
            record = self.env['account.move'].search([('id', '=', self.env.context.get('active_ids')[0])])
            if record.payment_state == "paid":
                raise UserError(
                    "You can't register a payment because there is nothing left to pay on the selected journal items.")

        return super(AccountPaymentRegister, self).action_register_payment()

    @api.depends('journal_id')
    def _compute_journal_code(self):
        for payment in self:
            acquirer = self.env['payment.provider'].search(
                [('company_id', '=', self.company_id.id), ('code', '=', 'ebizcharge')])
            journal_id = acquirer.journal_id
            if payment.payment_method_line_id.code == 'ebizcharge' and payment.line_ids:
                if payment.line_ids[0].move_id.move_type == "out_refund":
                    payment.journal_code = 'EBIZC:credit_note'
                else:
                    payment.journal_code = "EBIZC"
            else:
                payment.journal_code = "other"

    def _post_payments(self, to_process, edit_mode=False):
        """ Post the newly created payments.

        :param to_process:  A list of python dictionary, one for each payment to create, containing:
                            * create_vals:  The values used for the 'create' method.
                            * to_reconcile: The journal items to perform the reconciliation.
                            * batch:        A python dict containing everything you want about the source journal items
                                            to which a payment will be created (see '_get_batches').
        :param edit_mode:   Is the wizard in edition mode.
        """
        if 'from_transaction_history' in self.env.context:
            return super()._post_payments(to_process, edit_mode=edit_mode)
        payments = self.env['account.payment']
        for vals in to_process:
            payments |= vals['payment']

        if self.payment_method_line_id.code=='ebizcharge':
            if self.partner_id.ebiz_profile_id:
                pass
            else:
                default_instance = self.env['ebizcharge.instance.config'].search(
                    [('is_valid_credential', '=', True), ('is_default', '=', True)], limit=1)
                if default_instance:
                    self.partner_id.update({
                        'ebiz_profile_id': default_instance.id,
                    })

            responce_check = payments.sudo().with_context({
                'active_id': self._context['active_id'] if 'active_id' in self._context else False,
                'active_model': self._context['active_model'] if 'active_model' in self._context else False,
                'payment_data': {
                    'card_save': self.card_save,
                    'ach_save': self.bank_account_save,
                    'token_type': self.token_type,
                    'card_id': self.card_id,
                    'tokenid': self.card_id.id,
                    'ach_id': self.ach_id,
                    'card_card_number': self.card_card_number,
                    'card_card_code': self.card_card_code,
                    "card_account_holder_name": self.card_account_holder_name,
                    "card_exp_year": self.card_exp_year,
                    "journal_id": self.journal_id.id,
                    "card_exp_month": self.card_exp_month,
                    "card_avs_street": self.card_avs_street,
                    "card_avs_zip": self.card_avs_zip,
                    "partner_id": self.partner_id.id,
                    "ach_account_holder_name": self.ach_account_holder_name,
                    "account_number": self.ach_account,
                    "account_type": self.ach_account_type,
                    "routing": self.ach_routing,
                    'sub_partner_id': self.sub_partner_id.id,
                    'security_code': self.security_code,
                    'to_reconcile': to_process[0]['to_reconcile'].ids or False,
                    'ebiz_receipt_emails': self.ebiz_receipt_emails,
                    'ebiz_send_receipt': self.ebiz_send_receipt,
                }}).action_post()

            if 'xml_id' in responce_check:
                if responce_check['xml_id'] == 'payment_ebizcharge_crm.action_ebiz_transaction_validation_form':
                    return responce_check

            if 'res_model' in responce_check and responce_check['res_model'] == 'message.wizard':
                return responce_check
        else:
            payments.action_post()

    def _create_payments(self):
        if self.payment_method_line_id.code == 'ebizcharge' and 'from_transaction_history' not in self.env.context:
            self.ensure_one()
            batches = self._get_batches()
            first_batch_result = batches[0]
            edit_mode = self.can_edit_wizard and (len(first_batch_result['lines']) == 1 or self.group_payment)
            to_process = []

            if edit_mode:
                payment_vals = self._create_payment_vals_from_wizard(first_batch_result)
                to_process.append({
                    'create_vals': payment_vals,
                    'to_reconcile': batches[0]['lines'],
                    'batch': batches[0],
                })
            else:
                # Don't group payments: Create one batch per move.
                if not self.group_payment:
                    new_batches = []
                    for batch_result in batches:
                        for line in batch_result['lines']:
                            new_batches.append({
                                **batch_result,
                                'lines': line,
                            })
                    batches = new_batches

                for batch_result in batches:
                    to_process.append({
                        'create_vals': self._create_payment_vals_from_batch(batch_result),
                        'to_reconcile': batch_result['lines'],
                        'batch': batch_result,
                    })

            payments = self._init_payments(to_process, edit_mode=edit_mode)
            responce_validation = self._post_payments(to_process, edit_mode=edit_mode)

            if responce_validation and 'xml_id' in responce_validation:
                if responce_validation['xml_id'] == 'payment_ebizcharge_crm.action_ebiz_transaction_validation_form':
                    return responce_validation

            self._reconcile_payments(to_process, edit_mode=edit_mode)

            if responce_validation and 'res_model' in responce_validation and responce_validation['res_model'] == 'message.wizard':
                for inv in payments.payment_transaction_id.invoice_ids:
                    inv.sync_to_ebiz()
                if self.ebiz_profile_id.is_surcharge_enabled and any(inv.move_type == 'out_invoice' for inv in payments.payment_transaction_id.invoice_ids):
                    eligible = False
                    if payments.payment_transaction_id.is_pay_method_eligible and payments.payment_transaction_id.is_zip_code_allowed:
                        eligible = True
                    responce_validation['context']['default_is_surcharge'] = True
                    responce_validation['context']['default_is_eligible'] = eligible
                    responce_validation['context']['default_surcharge_subtotal'] = self.amount
                    responce_validation['context'][
                        'default_surcharge_amount'] = payments.payment_transaction_id.surcharge_amt
                    responce_validation['context'][
                        'default_surcharge_percentage'] = payments.payment_transaction_id.surcharge_percent
                    responce_validation['context']['default_surcharge_total'] = self.amount + float(
                        payments.payment_transaction_id.surcharge_amt)
                    responce_validation['context']['default_currency_id'] = self.env.company.currency_id.id
                else:
                    responce_validation['context']['default_surcharge_total'] = payments.payment_transaction_id.amount
                    
                responce_validation['context']['default_is_ach'] = False if self.token_type == 'credit' else True
                responce_validation['context']['default_partner_id'] = payments.payment_transaction_id.token_id.partner_id.name if payments.payment_transaction_id.token_id else self.partner_id.name
                responce_validation['context']['default_transaction_type'] = 'Auth Only' if payments.payment_transaction_id.transaction_type=='pre_auth' else 'Sale'
                responce_validation['context']['default_surcharge_percent'] = str(payments.payment_transaction_id.surcharge_percent) +' %'
                responce_validation['context']['default_currency_id'] = payments.payment_transaction_id.currency_id.id
                responce_validation['context']['default_document_number'] = payments.payment_transaction_id.reference
                responce_validation['context']['default_reference_number'] = payments.payment_transaction_id.provider_reference
                responce_validation['context']['default_auth_code'] = payments.payment_transaction_id.ebiz_auth_code
                display_name = payments.payment_transaction_id.token_id.get_encrypted_name() if payments.payment_transaction_id.token_id else self.partner_id.name
                responce_validation['context']['default_payment_method'] = display_name
                responce_validation['context']['default_date_paid'] = payments.payment_transaction_id.last_state_change
                responce_validation['context']['default_subtotal'] = payments.payment_transaction_id.amount
                responce_validation['context']['default_avs_street'] = payments.ebiz_avs_street
                responce_validation['context']['default_avs_zip_code'] = payments.ebiz_avs_zip
                responce_validation['context']['default_cvv'] = payments.payment_transaction_id.ebiz_cvv_resp
                return responce_validation

            return payments
        else:
            return super(AccountPaymentRegister, self)._create_payments()



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

    def action_create_payments(self):
        for line in self:
            if line.is_pay_link==True and line.line_ids:
                move_ebiz = line.line_ids[0].move_id
                if move_ebiz.payment_internal_id and line.partner_id.ebiz_profile_id and move_ebiz.payment_internal_id:
                    ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=line.partner_id.ebiz_profile_id)
                    received_payments = ebiz.client.service.DeleteEbizWebFormPayment(**{
                        'securityToken': ebiz._generate_security_json(),
                        'paymentInternalId': move_ebiz.payment_internal_id,
                    })                
                move_ebiz.update({
                    'request_amount': 0,
                    'last_request_amount': 0,
                    'save_payment_link': False,
                })        
        if self.payment_method_line_id.code == 'ebizcharge':
            if len(self.env['account.move.line'].browse(self.env.context['active_ids']).move_id.ids) > 1:
                raise UserError('Unable to process more than 1 invoice.')

            if 'active_model' in self.env.context and self.env.context['active_model'] == 'account.move.line':
                invoice_lines = self.env['account.move.line'].browse(self.env.context['active_ids'])
                if invoice_lines.move_id.move_type not in ('out_invoice','out_refund'):
                    raise UserError('EBizCharge payments are only allowed for customer invoices/Credit Notes!')
            if self.ebiz_send_receipt and self.line_ids.move_id:
                move_id = self.line_ids.move_id
                ebiz_profile_id = move_id.partner_id.ebiz_profile_id
                if ebiz_profile_id and not ebiz_profile_id.use_econnect_transaction_receipt:
                    raise ValidationError(
                        'Configuration required. Please enable eConnect transaction receipts in the integration server.')
            if self.token_type == 'emv_device' and self.emv_device_id:
                # record = self.env['account.move'].search([('id', '=', self.line_ids.move_id.id)])
                line_list = []
                for line in self.line_ids.move_id.invoice_line_ids:
                    taxable = True if line.tax_ids else False
                    base_line = line._convert_to_tax_base_line_dict()
                    to_update_vals, tax_values_list = line.env['account.tax']._compute_taxes_for_single_line(base_line)
                    tax = sum([x['tax_amount'] for x in tax_values_list if 'tax_amount' in x])
                    
                    line_list.append((0, 0, {
                        "name": line.product_id.name,
                        "description": line.name,
                        "list_price": line.product_id.list_price,
                        "sku": line.product_id.default_code,
                        "commoditycode": line.product_id.default_code,
                        "discountamount": line.discount,
                        "discountrate": "0",
                        "taxable": taxable,
                        "taxamount": tax,
                        'qty': line.quantity,
                        'price_unit': line.price_unit,
                        'price_subtotal': line.price_subtotal,
                    }))
                device_value = {
                    'devicekey': self.emv_device_id.source_key,
                    'pin': self.ebiz_profile_id.pin,
                    'journal_id': self.journal_id.id,
                    'invoice_id': self.line_ids[0].move_id.id,
                    'email_sent': True if self.ebiz_send_receipt else False, 
                    'payment_date': self.payment_date,
                    'invoice': self.communication,
                    'command': 'Credit'  if self.line_ids[0].move_id.move_type=='out_refund' else  self.transaction_command,
                    "ponum": self.line_ids[0].move_id.ref or self.line_ids[0].move_id.name,
                    "amount": self.amount,
                    "orderid": self.communication,
                    "partner_id": line.partner_id.id,
                    "description": "sample description",
                    "billing_address": {
                        "company": line.partner_id.company_name,
                        "street": str(line.partner_id.street2) + str(line.partner_id.street2),
                        "postalcode": line.partner_id.zip, },
                    "shipping_address": {
                        "company": line.partner_id.company_name,
                        "street": str(line.partner_id.street2) + str(line.partner_id.street2),
                        "postalcode": line.partner_id.zip, },
                    'emv_device_ids': line_list,
                }
                emv_device_transaction = self.env['emv.device.transaction'].create(device_value)
                emv_device_transaction.action_post()
                emv_device_transaction.invoice_id.emv_transaction_id = emv_device_transaction.id
                emv_device_transaction.invoice_id.log_status_emv = "Transaction Sent to Selected Device: " + str(
                    self.emv_device_id.name)
                context = dict()
                context['message'] =  'Transaction has been successfully sent to the device!'
                context['default_transaction_id'] = emv_device_transaction.id
                return self.message_wizard(context)

            elif self.token_type == 'emv_device':
                raise UserError('Select the Payment method. [EMV Device/ Credit/Bank Account]')
            else:
                payments = self._create_payments()

                if 'xml_id' in payments:
                    if payments['xml_id'] == 'payment_ebizcharge_crm.action_ebiz_transaction_validation_form':
                        return payments

                if payments and 'res_model' in payments and payments['res_model'] == 'message.wizard':
                    return payments

                if self._context.get('dont_redirect_to_payments'):
                    return True

                action = {
                    'name': _('Payments'),
                    'type': 'ir.actions.act_window',
                    'res_model': 'account.payment',
                    'context': {'create': False},
                }
                if len(payments) == 1:
                    action.update({
                        'view_mode': 'form',
                        'res_id': payments.id,
                    })
                else:
                    action.update({
                        'view_mode': 'tree,form',
                        'domain': [('id', 'in', payments.ids)],
                    })
                return action
        else:
            return super(AccountPaymentRegister, self).action_create_payments()


from odoo import fields, models, api, _
from odoo.exceptions import UserError, ValidationError
import logging
from ..models.ebiz_charge import message_wizard

_logger = logging.getLogger(__name__)


class CustomMessageWizard(models.TransientModel):
    _name = 'wizard.order.process.transaction'
    _description = "Wizard Order Process Transaction"

    def _get_transaction_command(self):
        pre_auth = ('AuthOnly', 'Pre Auth')
        dep = ('Sale', 'Deposit')
        return [pre_auth, dep]

    def _get_inv_transaction_command(self):
        pre_auth = ('AuthOnly', 'Pre Auth')
        dep = ('Sale', 'Deposit')
        return [pre_auth, dep]

    @api.model
    def year_selection(self):
        today = fields.Date.today()
        # year =  # replace 2000 with your a start year
        year = 2000
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

    sale_order_id = fields.Many2one('sale.order')
    invoice_id = fields.Many2one('account.move')
    partner_id = fields.Many2one('res.partner')
    card_id = fields.Many2one('payment.token')
    ach_id = fields.Many2one('payment.token')
    amount = fields.Monetary(string='Amount')
    currency_id = fields.Many2one("res.currency", string="Currency", readonly=True, required=True)
    is_check_or_credit = fields.Selection([('ach', 'ACH'), ('credit', 'Credit Card')], string='Payment method type',
                                          default="credit")
    inv_transaction_command = fields.Selection(_get_inv_transaction_command, string='Transaction Command Invoice')
    transaction_command = fields.Selection(_get_transaction_command, string='Transaction Command')
    ach_account_holder_name = fields.Char("Account Holder Name")
    ach_account = fields.Char("Account #")
    ach_account_type = fields.Selection([('Checking', 'Checking'), ('Savings', 'Savings')], string='Account Type',
                                        default="Checking")
    ach_routing = fields.Char('Account Routing')
    ach_save = fields.Boolean(default=False)
    is_credit_transaction = fields.Boolean(default=False)
    card_account_holder_name = fields.Char('Name on Card')
    card_card_number = fields.Char('Card Number')
    card_exp_year = fields.Selection(year_selection, 'Expiration Year')
    card_exp_month = fields.Selection(month_selection, 'Expiration Month')
    card_avs_street = fields.Char("Billing Address")
    card_avs_zip = fields.Char('Zip Code')
    card_card_code = fields.Char('Security Code')
    card_card_type = fields.Char('Card Type')
    is_refund = fields.Char('Is Refund Transaction', default=False)
    allow_partial_payment = fields.Boolean('Allow partial payment', default=False)

    @api.constrains('card_card_number')
    def card_card_number_length_id(self):
        for rec in self:
            if rec.card_card_number and (len(rec.card_card_number) > 19 or len(rec.card_card_number) < 13):
                raise ValidationError(_('Card number should be valid and should be 13-19 digits!'))

    @api.constrains('ach_account')
    def ach_acc_number_length_id(self):
        for rec in self:
            if not rec.ach_account.isnumeric():
                raise ValidationError(_('Account number must be numeric only!'))
            elif rec.ach_account and 4 <= len(rec.ach_account) <= 17:
                return True
            else:
                raise ValidationError(_('Account number should be 4-17 digits!'))

    @api.constrains('ach_routing')
    def ach_routing_number_length_id(self):
        for rec in self:
            if rec.ach_routing and len(rec.ach_routing) != 9:
                raise ValidationError(_('Routing number must be 9 Digits!'))

    def process_transaction(self):
        if self.is_check_or_credit == 'credit':
            if self.card_id:
                return self.process_existing_card_transaction()
            else:
                return self.validate_card()
        elif self.is_check_or_credit == 'ach':
            if self.ach_id:
                resp = self.process_existing_ach_transaction()
            else:
                resp = self.process_new_ach_transaction()
            return resp

    def process_new_card_transaction(self):
        token_id = self.create_credit_card_payment_method().id
        if self.sale_order_id:
            company = self.sale_order_id.company_id.id
        else:
            company = self.invoice_id.company_id.id
        vals = {
            'provider_id': self.env['payment.provider'].search([('company_id', '=', company), ('code', '=', 'ebizcharge')]).id,
        }
        if self.sale_order_id:
            trans = self.sale_order_id.sudo()._create_payment_transaction(vals)
            self.create_invoice()
        else:
            trans = self.invoice_id.sudo()._create_payment_transaction(vals)
        trans.write({'payment_token_id': token_id})
        resp = trans._send_payment_request()
        if ((self.transaction_command or self.inv_transaction_command) == "Sale") and resp['ResultCode'] == 'A':
            if self.sale_order_id:
                self.sale_order_id.payment_action_capture()
            else:
                self.invoice_id.payment_action_capture()
        return resp

    def process_new_ach_transaction(self):
        token_id = self.create_bank_account().id
        if self.sale_order_id:
            company = self.sale_order_id.company_id.id
        else:
            company = self.invoice_id.company_id.id
        vals = {
            'provider_id': self.env['payment.provider'].search([('company_id', '=', company), ('code', '=', 'ebizcharge')]).id,
        }
        if self.sale_order_id:
            trans = self.sale_order_id._create_payment_transaction(vals)
            self.create_invoice()
        else:
            trans = self.invoice_id._create_payment_transaction(vals)
        trans.write({'payment_token_id': token_id})
        command = self._generate_transaction_command()
        resp = trans._send_payment_request()
        if self.sale_order_id:
            self.sale_order_id.payment_action_capture()
        else:
            self.invoice_id.payment_action_capture()
        return message_wizard('Transaction has been successfully processed!')

    def create_invoice(self):
        if not self.sale_order_id.invoice_ids:
            payment = self.env['sale.advance.payment.inv'] \
                .with_context(active_ids=self.sale_order_id.ids, active_model='sale.order',
                              active_id=self.sale_order_id.id).create(
                {'advance_payment_method': 'delivered'})
            payment.create_invoices()

    def process_existing_card_transaction(self):
        if not self.card_id:
            raise ValidationError("Please enter payment methode profile on the customer to run transaction.")
        if not self.card_id.ebizcharge_profile:
            self.card_id.do_syncing()
            if self.sale_order_id:
                company = self.sale_order_id.company_id.id
            else:
                company = self.invoice_id.company_id.id
        vals = {
            'provider_id': self.env['payment.provider'].search(
                [('company_id', '=', company), ('code', '=', 'ebizcharge')]).id,
        }
        if self.sale_order_id:
            trans = self.sale_order_id._create_payment_transaction(vals)
            self.create_invoice()
        else:
            trans = self.invoice_id._create_payment_transaction(vals)

        trans.write({'payment_token_id': self.card_id})
        resp = trans._send_payment_request()
        if resp['ResultCode'] == "D":
            return self.show_payment_response(resp)
        if (self.transaction_command or self.inv_transaction_command) == "Sale":
            if self.sale_order_id:
                self.sale_order_id.payment_action_capture()
            else:
                self.invoice_id.payment_action_capture()
            return message_wizard('Transaction has been successfully deposited!')
        return message_wizard('Transaction has been successfully authorized!')

    def process_existing_ach_transaction(self):
        if not self.ach_id:
            raise ValidationError("Please enter payment methode profile on the customer to run transaction.")
        if not self.ach_id.ebizcharge_profile:
            self.ach_id.do_syncing()
        if self.sale_order_id:
            company = self.sale_order_id.company_id.id
        else:
            company = self.invoice_id.company_id.id
        vals = {
            'provider_id': self.env['payment.provider'].search(
                [('company_id', '=', company), ('code', '=', 'ebizcharge')]).id,
        }
        if self.sale_order_id:
            trans = self.sale_order_id._create_payment_transaction(vals)
            self.create_invoice()
        else:
            trans = self.invoice_id._create_payment_transaction(vals)
        command = self._generate_transaction_command()
        trans.write({'payment_token_id': self.ach_id})
        resp = trans._send_payment_request()
        if resp['ResultCode'] == "D":
            return self.show_payment_response(resp)
        if self.sale_order_id:
            self.sale_order_id.payment_action_capture()
        else:
            self.invoice_id.payment_action_capture()
        return message_wizard('Transaction has been successfully processed!')

    def _generate_transaction_command(self):
        if self.is_check_or_credit == 'ach':
            return 'Check'
        else:
            return self.transaction_command or self.inv_transaction_command

    def validate_card(self):
        self.ensure_one()
        avs_action = self.partner_id.ebiz_profile_id.merchant_card_verification

        if avs_action == 'minimum-amount':
            resp = self.credit_card_validate_transaction()
            avs_result = self.get_avs_result(resp)

            if all([x == 'Match' for x in avs_result]) and resp['ResultCode'] == 'A':
                resp = self.process_new_card_transaction()
                if (self.transaction_command or self.inv_transaction_command) == "Sale":
                    if self.sale_order_id:
                        self.sale_order_id.payment_action_capture()
                    else:
                        self.invoice_id.payment_action_capture()
                    return message_wizard('Transaction has been successfully deposited!')
                return message_wizard('Transaction has been successfully authorized!')

            return self.show_payment_response(resp)
        elif avs_action == "full-amount":
            resp = self.process_new_card_transaction()
            avs_result = self.get_avs_result(resp)
            if all([x == 'Match' for x in avs_result]) and resp['ResultCode'] == 'A':
                return message_wizard('Successful!')
            else:
                return self.show_payment_response(resp)
        else:
            resp = self.process_new_card_transaction()
            avs_result = self.get_avs_result(resp)
            if resp['ResultCode'] == 'A':
                return message_wizard('Successful!')
            else:
                return self.show_payment_response(resp)

    def create_credit_card_payment_method(self):
        method = self.env.ref('payment_ebizcharge_crm.payment_method_ebizcharge').id
        params = {
            "account_holder_name": self.card_account_holder_name,
            "payment_details": self.card_account_holder_name,
            "payment_method_id": method,
            "card_number": self.card_card_number,
            "card_exp_year": self.card_exp_year,
            "card_exp_month": self.card_exp_month,
            "avs_street": self.card_avs_street,
            "avs_zip": self.card_avs_zip,
            "card_code": self.card_card_code,
            "partner_id": self.partner_id.id,
            "user_id": self.env.user.id,
            "is_card_save": True,
            "active": True,
            "provider_ref": "Temp",
            'provider_id': self.env['payment.provider'].search(
                [('company_id', '=', self.partner_id.company_id.id if self.partner_id.company_id else self.env.company.id), ('code', '=', 'ebizcharge')]).id
        }
        token = self.env['payment.token'].create(params)
        token.action_sync_token_to_ebiz()
        return token

    def create_bank_account(self):
        method = self.env.ref('payment_ebizcharge_crm.payment_method_ebizcharge').id
        params = {
            "account_holder_name": self.ach_account_holder_name,
            "payment_method_id": method,
            "account_number": self.ach_account,
            'payment_details': self.ach_account,
            "account_type": self.ach_account_type,
            "routing": self.ach_routing,
            "partner_id": self.partner_id.id,
            "user_id": self.env.user.id,
            "is_card_save": True,
            "active": True,
            "token_type": 'ach',
            'provider_id': self.env['payment.provider'].search(
                [('company_id', '=', self.partner_id.company_id.id if self.partner_id.company_id else self.env.company.id), ('code', '=', 'ebizcharge')]).id
        }
        token = self.env['payment.token'].create(params)
        token.action_sync_token_to_ebiz()
        return token

    def show_payment_response(self, resp):
        action = self.env.ref('payment_ebizcharge_crm.action_ebiz_transaction_validation_form').read()[0]
        if resp['ResultCode'] == 'E':
            return ValidationError(resp['Error'])
        card_code, address, zip_code = self.get_avs_result(resp)
        if self.sale_order_id:
            transaction_id = self.sale_order_id.authorized_transaction_ids
        else:
            transaction_id = self.invoice_id.authorized_transaction_ids

        validation_params = {'address': address,
                             'zip_code': zip_code,
                             'card_code': card_code,
                             'wizard_process_id': self.id,
                             'transaction_id': transaction_id.id,
                             'check_avs_match': all([x == "Match" for x in [card_code, address, zip_code]])}

        if resp['ResultCode'] == 'D':
            validation_params['is_card_denied'] = True
            validation_params['denied_message'] = 'Card Declined' if 'Card Declined' in resp['Error'] else resp['Error']
            action['name'] = 'Card Declined'
        wiz = self.env['wizard.ebiz.transaction.validation'].create(validation_params)

        action['res_id'] = wiz.id
        return action

    def _get_credit_card_transaction(self):
        return {
            'InternalCardAuth': False,
            'CardPresent': False,
            'CardNumber': self.card_card_number,
            # 'CardExpiration': self.card_card_expiration.strftime('%m%y'),
            "CardExpiration": "%s%s" % (self.card_exp_month, self.card_exp_year[2:] if self.card_exp_year else False),
            'CardCode': self.card_card_code,
            'AvsStreet': self.card_avs_street,
            'AvsZip': self.card_avs_zip
        }

    def credit_card_validate_transaction(self):
        try:
            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj()
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
                    "CreditCardData": self._get_credit_card_transaction(),
                    "AccountHolder": self.card_account_holder_name,
                }
            }

            resp = ebiz.client.service.runTransaction(**params)
            resp_void = ebiz.execute_transaction(resp['RefNum'], {'command': 'Void'})
        except Exception as e:
            _logger.exception(e)
            raise ValidationError(e)
        return resp

    def get_avs_result(self, resp):
        card_code = resp['CardCodeResult']
        avs = resp['AvsResult']
        if '&' in avs:
            array = avs.split('&')
            address = array[0].split(':')[1]
            zip_code = array[1].split(':')[1]
        else:
            address = avs
            zip_code = avs

        return card_code.strip(), address.strip(), zip_code.strip()

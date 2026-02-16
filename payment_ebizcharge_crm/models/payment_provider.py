# coding: utf-8

from datetime import datetime
import logging
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError, AccessError
from odoo.addons.payment_authorize import const
from ..utils import strtobool

_logger = logging.getLogger(__name__)


class PaymentAcquirerEBizCharge(models.Model):
    _inherit = 'payment.provider'

    def _compute_feature_support_fields(self):
        """ Override of `payment` to enable additional features. """
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == 'ebizcharge').update({
            'support_manual_capture': 'full_only',
            'support_refund': 'full_only',
            'support_tokenization': True,
        })

    code = fields.Selection(selection_add=[('ebizcharge', 'EBizCharge')], ondelete={'ebizcharge': 'set default'})

    VALIDATION_AMOUNTS = {
        'CAD': 0.05,
        'EUR': 0.05,
        'GBP': 0.05,
        'JPY': 0.05,
        'AUD': 0.05,
        'NZD': 0.05,
        'CHF': 0.05,
        'HKD': 0.05,
        'SEK': 0.05,
        'DKK': 0.05,
        'PLN': 0.05,
        'NOK': 0.05,
        'HUF': 0.05,
        'CZK': 0.05,
        'BRL': 0.05,
        'MYR': 0.05,
        'MXN': 0.05,
        'ILS': 0.05,
        'PHP': 0.05,
        'TWD': 0.05,
        'USD': 0.05
    }

    def _get_default_payment_method_codes(self):
        """ Override of `payment` to return the default payment method codes. """
        default_codes = super()._get_default_payment_method_codes()
        if self.code != 'ebizcharge':
            return default_codes
        return const.DEFAULT_PAYMENT_METHODS_CODES

    @api.model
    def _get_compatible_providers(self, *args, is_validation=False, **kwargs):
        """ Override of payment to unlist EbizCharge providers for validation operations. """
        providers = super()._get_compatible_providers(*args, is_validation=is_validation, **kwargs)
        if is_validation:
            providers = providers.filtered(lambda p: p.code != 'ebizcharge')
        return providers

    def _get_ebizcharge_urls(self, environment):
        """ EBizCharge URLS """
        return {
            'ebizcharge_form_url': '/payment/ebizcharge',
        }

    def ebizcharge_get_form_action_url(self):
        self.ensure_one()
        environment = 'prod' if self.state == 'enabled' else 'test'
        return self._get_ebizcharge_urls(environment)['ebizcharge_form_url']

    def get_acquirer_name(self, *args):
        if self.name == 'EBizCharge':
            return True
        else:
            return False

    @api.model
    def _get_feature_support(self):
        """Get advanced feature support by provider.

        Each provider should add its technical in the corresponding
        key for the following features:
            * fees: support payment fees computations
            * authorize: support authorizing payment (separates
                         authorization and capture)
            * tokenize: support saving payment data in a payment.tokenize
                        object
        """
        res = super(PaymentAcquirerEBizCharge, self)._get_feature_support()
        res['authorize'].append('ebizcharge')
        res['tokenize'].append('ebizcharge')
        return res

    @api.model
    def ebizcharge_s2s_form_process(self, data):
        method = self.env.ref('payment_ebizcharge_crm.payment_method_ebizcharge').id
        if 'cardData' in data:
            exp_date = data['cardData']["expiry"].split('/')
            default = False
            if 'default_card_method' in data['cardData']:
                default = True if data['cardData']['default_card_method'] == 'true' else False
            update_data = {
                "card_exp_year": str(2000 + int(exp_date[1])),
                "card_exp_month": str(int(exp_date[0])),
                "card_code": data['cardData']["cardCode"],
                "avs_street": data['cardData']["street"],
                "avs_zip": data['cardData']["zip"],
                "account_holder_name": data['cardData']["name"],
                "is_default": default
            }
        else:
            default = False
            if 'default_card_method' in data['bankData']:
                default = True if data['bankData']['default_card_method'] == 'true' else False
            update_data = {
                "routing": data['bankData']['routingNumber'],
                "account_holder_name": data['bankData']["nameOnAccount"],
                "account_type": data['bankData']['accountType'] if data['bankData']['accountType'] in ('Checking','Savings') else 'Checking',
                "is_default": default
            }
        token = self.env['payment.token'].sudo()
        if data.get('update_pm_id'):
            payment_token = token.browse(int(data.get('update_pm_id')))
            payment_token.write(update_data)
            payment_token.action_sync_token_to_ebiz()
            payment_token.card_code = False
            return payment_token
        elif 'cardData' in data and data['cardData']['pmid'] != '':
            payment_token = token.browse(int(data['cardData']['pmid']))
            payment_token.write(update_data)
            payment_token.action_sync_token_to_ebiz()
            payment_token.card_code = False
            return payment_token
        elif 'bankData' in data and data['bankData']['pmid'] != '':
            payment_token = token.browse(int(data['bankData']['pmid']))
            payment_token.write(update_data)
            payment_token.action_sync_token_to_ebiz()
            return payment_token
        else:
            if 'cardData' in data:
                last4 = data['cardData'].get('cardNumber', "")[-4:]
                update_data.update({
                    "card_exp_year": str(2000 + int(exp_date[1])),
                    "card_exp_month": str(int(exp_date[0])),
                    "partner_id": int(data['partner_id']),
                    "payment_method_id": method,
                    'payment_details': 'XXXXXXXXXXXX%s' % last4,
                    'provider_ref': data['cardData']["name"],
                    'provider_id': int(data['provider_id']),
                    "card_number": data['cardData']["cardNumber"].replace(" ", ""),
                    "avs_street": data['cardData']["street"],
                    "avs_zip": data['cardData']['zip'],
                    "is_card_save": True if data['cardData']['tokenBox'] == 'true' else False
                })
                if data['cardData']['default_card_method'] != 'true':
                    update_data.update({
                        "is_default": False
                    })
                if 'partner_id' in data:
                    partner_obj = self.env['res.partner'].browse([data['partner_id']])
                    instance = partner_obj.ebiz_profile_id
                    if instance.merchant_card_verification == 'minimum-amount':
                        if instance.verify_card_before_saving:
                            resp = self.sudo().ebizcharge_token_validate(update_data)
                            if resp['ResultCode'] in ['D', 'E']:
                                raise AccessError(_(resp['Error']))
                    elif instance.merchant_card_verification == 'no-validation':
                        if strtobool(instance.use_full_amount_for_avs) and instance.verify_card_before_saving:
                            resp = self.sudo().ebizcharge_token_validate(update_data)
                            if resp['ResultCode'] in ['D', 'E']:
                                raise AccessError(_(resp['Error']))
                        elif not strtobool(instance.use_full_amount_for_avs) and not instance.verify_card_before_saving:
                            resp = self.sudo().ebizcharge_token_validate(update_data)
                            if resp['ResultCode'] in ['D', 'E']:
                                raise AccessError(_(resp['Error']))
            else:
                last4 = data['bankData'].get('accountNumber', "")[-4:]
                update_data.update({
                    "provider_id": int(data['provider_id']),
                    'payment_details': 'XXXXXXXXXXXX%s' % last4,
                    "account_number": data['bankData']['accountNumber'],
                    "routing": data['bankData']['routingNumber'],
                    "provider_ref": int(data['provider_id']),
                    "payment_method_id": method,
                    "partner_id": int(data['partner_id']),
                    "token_type": "ach",
                    "is_card_save": True if data['bankData']['tokenBox'] == 'true' else False
                })
                if data['bankData']['default_card_method'] != 'true':
                    update_data.update({
                        "is_default": False
                    })
            payment_token = token.sudo().create(update_data)
            payment_token.action_sync_token_to_ebiz()
            payment_token.card_code = False
        return payment_token

    def ebizcharge_s2s_form_validate(self, data):
        error = dict()
        if 'bankData' in data:
            mandatory_fields = ["accountNumber", "routingNumber", "nameOnAccount", "accountType"]
            if len(data['bankData']['accountNumber']) < 4 or len(data['bankData']['accountNumber']) > 17 or len(
                    data['bankData']['routingNumber']) > 9 or len(data['bankData']['routingNumber']) < 9:
                return False
            # Checking for mandatory fields
            for field_name in mandatory_fields:
                if not data['bankData'].get(field_name):
                    error[field_name] = 'missing'
        if 'cardData' in data:
            mandatory_fields = ["cardNumber", "name", "street", "expiry", "cardCode", "zip"]
            if data['cardData']['expiry'] and datetime.now().strftime('%y%m') > datetime.strptime(
                    data['cardData']['expiry'].replace(' ', ''), '%m/%y').strftime('%y%m'):
                return False
            # Checking for mandatory fields
            for field_name in mandatory_fields:
                if not data['cardData'].get(field_name):
                    if field_name == 'cardCode' and data.get('partner_id'):
                        ebiz_profile_id = self.env['res.partner'].sudo().browse(
                            int(data.get('partner_id'))).exists().ebiz_profile_id
                        if ebiz_profile_id.verify_card_before_saving:
                            error[field_name] = 'missing'
                    else:
                        error[field_name] = 'missing'

        return False if error else True

    def ebizcharge_token_validate(self, data):
        try:
            instance = None
            partner_obj = False
            if 'partner_id' in data:
                partner_obj = self.env['res.partner'].browse([data['partner_id']])
                instance = partner_obj.ebiz_profile_id
            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(self.env.context.get('website'), instance=instance)
            params = {
                "securityToken": ebiz._generate_security_json(),
                "tran": {
                    "IgnoreDuplicate": False,
                    "IsRecurring": False,
                    "Software": 'Odoo CRM',
                    "CustReceipt": False,
                    "Command": 'AuthOnly',
                    "Details": partner_obj.transaction_details(),
                    "CreditCardData": self._get_credit_card_transaction(data),
                    "AccountHolder": data['account_holder_name']
                }
            }
            resp = ebiz.client.service.runTransaction(**params)
            resp_void = ebiz.execute_transaction(resp['RefNum'], {'command': 'Void'})
        except Exception as e:
            _logger.exception(e)
            raise ValidationError(e)
        return resp

    def _get_credit_card_transaction(self, data):
        return {
            'InternalCardAuth': False,
            'CardPresent': False,
            'CardNumber': data.get('card_number'),
            "CardExpiration": "%02d%s" % (int(data.get('card_exp_month')), data.get('card_exp_year')[2:]),
            'CardCode': data.get('card_code'),
            'AvsStreet': data.get('avs_street'),
            'AvsZip': data.get('avs_zip')
        }

    def s2s_process(self, data):
        cust_method_name = '%s_s2s_form_process' % (self.code)
        if not self.s2s_validate(data):
            return False
        if hasattr(self, cust_method_name):
            # As this method may be called in JSON and overridden in various addons
            # let us raise interesting errors before having strange crashes
            if not data.get('partner_id'):
                raise ValueError(_('Missing partner reference when trying to create a new payment token'))
            method = getattr(self, cust_method_name)
            return method(data)
        return True

    def s2s_validate(self, data):
        cust_method_name = '%s_s2s_form_validate' % (self.code)
        if hasattr(self, cust_method_name):
            method = getattr(self, cust_method_name)
            return method(data)

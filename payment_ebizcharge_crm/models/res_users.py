
from odoo import models, api
import logging

_logger = logging.getLogger(__name__)


class UserCredentials(models.Model):
    _inherit = 'res.users'

    @api.model_create_multi
    def create(self, vals_list):
        context = self
        for val in vals_list:
            if 'sel_groups_1_10_11' in val and val['sel_groups_1_10_11'] == 10:
                context = self.with_context({'portal_user': True})
            elif 'website_id' in self.env.context:
                context = self.with_context({'portal_user': True, 'website_id': self.env.context['website_id']})
        users = super(UserCredentials, context).create(vals_list)
        return users

    def _check_credentials(self, password, env):
        """Make all wishlists from session belong to its owner user."""

        result = super(UserCredentials, self)._check_credentials(password, env)
        instances = self.env['ebizcharge.instance.config'].sudo().search(
            [('is_valid_credential', '=', True), ('is_active', '=', True)])
        web_sale = self.env['ir.module.module'].sudo().search(
            [('name', '=', 'website_sale'), ('state', 'in', ['installed', 'to upgrade', 'to remove'])])
        ebiz_obj = self.env['ebiz.charge.api']
        for instance in instances:
            is_security_key = instance.ebiz_security_key
            if is_security_key:
                ebiz = ebiz_obj.sudo().with_context({'login': True}).get_ebiz_charge_obj(
                    instance=instance)
                resp, card_verification = self.sudo().merchant_details(ebiz)

                # FIXME:By default its True for every account EnableAVSWarnings False
                # flow/process is not decided yet as per Mr.Frank & Mam Jane (Dated 18-01-22)
                if resp and card_verification:
                    instance.merchant_data = resp['AllowACHPayments']
                    instance.merchant_card_verification = card_verification
                    instance.verify_card_before_saving = resp['VerifyCreditCardBeforeSaving']
                    instance.use_full_amount_for_avs = resp['UseFullAmountForAVS']
                    instance.allow_credit_card_pay = resp['AllowCreditCardPayments']
                    instance.enable_cvv = resp['EnableCVVWarnings']
                    if web_sale:
                        if instance.is_website:
                            for w in instance.website_ids:
                                w.merchant_data = resp['AllowACHPayments']
                                w.merchant_card_verification = card_verification
                                w.verify_card_before_saving = resp['VerifyCreditCardBeforeSaving']
                                w.allow_credit_card_pay = resp['AllowCreditCardPayments']
                                w.enable_cvv = resp['EnableCVVWarnings']

                    instance.is_surcharge_enabled = resp['IsSurchargeEnabled']
                    instance.surcharge_type_id = resp['SurchargeTypeId']
                    surcharge_integration_resp = self.sudo().integration_surcharge_details(ebiz)
                    ebiz_emv_pre_auth = next((sur['SettingValue'] for sur in surcharge_integration_resp if
                          sur['SettingName'] == 'IsEMVPreAuthEnabled'), None)
                    use_econnect_transaction_receipt = next(
                        (sur['SettingValue'] for sur in surcharge_integration_resp if
                         sur['SettingName'] == 'UseEConnectTransactionReceipts'), None)
                    if ebiz_emv_pre_auth=='True':
                        instance.is_emv_pre_auth = True
                    else:
                        instance.is_emv_pre_auth = False

                    if use_econnect_transaction_receipt == 'True':
                        instance.use_econnect_transaction_receipt = True
                    else:
                        instance.use_econnect_transaction_receipt = False

                    if resp['IsSurchargeEnabled'] and resp['SurchargeTypeId'] == 'DailyDiscount':
                        surcharge_resp = self.sudo().surcharge_details(ebiz)

                        batch_res = [sub['SettingValue'] for sub in surcharge_integration_resp if
                                     sub['SettingName'] == 'BatchProcessingSurchargeTermsNote']
                        instance.surcharge_percentage = surcharge_resp['SurchargePercentage']
                        instance.batch_terms = batch_res[0]
                        instance.surcharge_caption = surcharge_resp['SurchargeCaption']
                        instance.surcharge_terms = surcharge_resp['SurchargeTermsNote']
        return result

    def merchant_details(self, ebiz):
        try:
            resp = ebiz.client.service.GetMerchantTransactionData(**{
                'securityToken': ebiz._generate_security_json()
            })
        except:
            return None, None

        if resp['VerifyCreditCardBeforeSaving']:
            if resp['UseFullAmountForAVS']:
                return resp, 'full-amount'
            else:
                return resp, 'minimum-amount'
        elif resp['UseFullAmountForAVS']:
            return resp, 'full-amount'
        else:
            return resp, 'no-validation'

    def surcharge_details(self, ebiz):
        try:
            resp = ebiz.client.service.GetSurchargeSettings(**{
                'securityToken': ebiz._generate_security_json()
            })
            return resp
        except:
            return None

    def integration_surcharge_details(self, ebiz):
        try:
            resp = ebiz.client.service.GetMerchantIntegrationSettings(**{
                'securityToken': ebiz._generate_security_json()
            })
            return resp
        except:
            return None

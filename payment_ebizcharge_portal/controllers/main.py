# -*- coding: utf-8 -*-
import logging
from odoo.addons.website_sale.controllers.main import WebsiteSale
from odoo import http, _
from odoo.http import request
from odoo.exceptions import ValidationError
_logger = logging.getLogger(__name__)


class EbizchargeController(http.Controller):
    _approved_url = '/payment/ebizcharge/approved'
    _decline_url = '/payment/ebizcharge/cancel'
    _error_url = '/payment/ebizcharge/error'



    @http.route(['/surcharge/check'], type='json', auth='public', csrf=False)
    def surcharge_check_data(self, verify_validity=False, **kwargs):
        surcharge_calc_amt = 0
        kwargs = kwargs['kwargs']
        partner = request.env.user.partner_id
        if partner.ebiz_profile_id:
            ebiz = request.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=partner.ebiz_profile_id)
            methodid = kwargs['pm_id'] if 'pm_id' in kwargs else 0
            method_id = request.env['payment.token'].search([('id', '=', int(methodid)), ('token_type', '=', 'credit')],
                                                            limit=1)
            params = {
                'securityToken': ebiz._generate_security_json(),
                'customerInternalId': partner.ebiz_internal_id,
                'amount': float(kwargs['amount']),
            }
            if method_id:
                params.update({
                    'paymentMethodId': method_id.ebizcharge_profile,
                    'cardZipCode': method_id.avs_zip,
                })
            else:
                params.update({
                    'cardNumber': kwargs['cc_number'].replace(' ', '') if 'cc_number' in kwargs else 0,
                    'cardZipCode': kwargs['avs_zip'] if 'avs_zip' in kwargs else 0,
                })

            resp = ebiz.client.service.CalculateSurchargeAmount(**params)
            surcharge_calc_amt = float(resp['SurchargeAmount'])
        res = {
            'amount': surcharge_calc_amt,
        }
        return res

    @http.route(['/payment/ebizcharge/s2s/create_json_3ds'], type='json', auth='public', csrf=False)
    def ebizcharge_s2s_create_json_3ds(self, verify_validity=False, **kwargs):
        token = False
        kwargs = kwargs['kwargs']
        acquirer = request.env['payment.provider'].sudo().browse(int(kwargs['acquirer_id']))
        is_manage_screen = False
        kwargs['web_pay'] = '1'
        try:
            if not kwargs.get('partner_id'):
                kwargs = dict(kwargs, partner_id=request.env.user.partner_id.id)
            website_id = request.website_routing
            if not request.env.user._is_public():
                token = acquirer.with_context({'website': website_id}).s2s_process(kwargs)
        except ValidationError as e:
            _logger.exception(e)
            message = e.args[0]
            if isinstance(message, dict) and 'missing_fields' in message:
                msg = _("The transaction cannot be processed because some contact details are missing or invalid: ")
                message = msg + ', '.join(message['missing_fields']) + '. '
                if request.env.user._is_public():
                    message += _("Please sign in to complete your profile.")
                    # update message if portal mode = b2b
                    if request.env['ir.config_parameter'].sudo().get_param('auth_signup.allow_uninvited', 'False').lower() == 'false':
                        message += _("If you don't have any account, please ask your salesperson to update your profile. ")
                else:
                    message += _("Please complete your profile.")

            return {
                'error': message
            }

        if not token  and not request.env.user._is_public():
            res = {
                'result': False,
                'is_manage_screen': is_manage_screen,
            }
            return res

        res = {
            'result': True,
            'is_manage_screen': is_manage_screen,
            'id': token.id if token else False,
            '3d_secure': False,
            'verified': token.id if token else False,
            'providerid': token.provider_id.id if token else False,
        }
        return res

    @http.route(['/payment/ebizcharge/get/token'], type='json', auth='public', csrf=False)
    def ebizcharge_get_token_info(self, **kwargs):
        return request.env['payment.token'].get_payment_token_information(kwargs['pm_id'])

    @http.route(['/delete/ebizcharge/token'], type='json', auth='public', csrf=False)
    def ebizcharge_delete_token_info(self, **kwargs):
        return request.env['payment.token'].get_payment_token_information(kwargs['pm_id'])


class EbizChargeWebsiteSale(WebsiteSale):

    @http.route(['/shop/payment'], type='http', auth="public", website=True, sitemap=False)
    def shop_payment(self, **post):
        res = super(EbizChargeWebsiteSale, self).shop_payment(post=post)
        order = request.website.sale_get_order()
        if res.status_code == 200:
            request.env.user.partner_id.refresh_payment_methods()
            if request.env.user.partner_id.ebiz_profile_id:
                profile = request.env.user.partner_id.ebiz_profile_id
            else:
                profile = request.env['ebizcharge.instance.config'].sudo().search(
                    [('website_ids', 'in', request.website.ids), ('is_website', '=', True), ('is_active', '=', True)])
            show_ach = profile.merchant_data
            show_credit_cards = profile.allow_credit_card_pay
            allowed_commands = profile.ebiz_website_allowed_command
            auth_only = True if allowed_commands == 'pre-auth' else False
            odoo_partner = request.env['res.partner'].sudo().browse(res.qcontext['partner'].id).ensure_one()
            if odoo_partner:
                odoo_partner.sudo().with_context({'donot_sync': True, 'website':request.website.id}).ebiz_get_payment_methods()
            payment_tokens = odoo_partner.payment_token_ids
            payment_tokens |= odoo_partner.commercial_partner_id.sudo().payment_token_ids
            card_narrations = profile.surcharge_terms
            is_sur_able = False
            surcharge_terms = ''
            if profile.is_surcharge_enabled and profile.surcharge_type_id == 'DailyDiscount':
                is_sur_able = True
                surcharge_terms = profile.surcharge_terms
            res.qcontext['tokens_sudo'] = res.qcontext['tokens_sudo'].filtered(lambda i:i.provider_id.code != 'ebizcharge')
            res.qcontext['cardNarrations'] = card_narrations
            res.qcontext['is_sur_able'] = is_sur_able
            res.qcontext['allow_pay_surcharge'] = True if profile.is_surcharge_enabled else False
            res.qcontext['surcharge_percent'] = profile.surcharge_percentage if profile.is_surcharge_enabled else 0
            res.qcontext['surcharge_amount'] = 0.00
            res.qcontext['show_surcharge_amt'] = False
            res.qcontext['surcharge_terms'] = surcharge_terms
            #res.qcontext['tokens'] = payment_tokens.filtered(lambda r: r.create_uid == request.env.user)
            res.qcontext['tokens'] = payment_tokens.filtered(
                lambda r: r.partner_id == request.env.user.partner_id and r.provider_id.code != "ebizcharge")
            res.qcontext['ebiz_tokens'] = payment_tokens.filtered(
                lambda r: r.partner_id == request.env.user.partner_id and r.provider_id.code == "ebizcharge")

            res.qcontext['logIn'] = True if request.session['session_token'] else False
            res.qcontext['showACH'] = show_ach
            res.qcontext['showCreditCards'] = show_credit_cards
            res.qcontext['authOnly'] = auth_only
            res.qcontext['logIn'] = True if request.session['session_token'] else False
        return res

    def _prepare_shop_payment_confirmation_values(self, order):
        values = super(EbizChargeWebsiteSale, self)._prepare_shop_payment_confirmation_values(order)
        values['surcharge_amount'] = order.transaction_ids[0].surcharge_amt if order.partner_id.ebiz_profile_id.is_surcharge_enabled else 0
        values['surcharge_percent'] = order.transaction_ids[0].surcharge_percent if order.partner_id.ebiz_profile_id.is_surcharge_enabled else 0
        values['allow_pay_surcharge'] = True if order.partner_id.ebiz_profile_id.is_surcharge_enabled else False
        values['is_add_surcharge'] = True if order.partner_id.ebiz_profile_id.is_surcharge_enabled else False
        return values


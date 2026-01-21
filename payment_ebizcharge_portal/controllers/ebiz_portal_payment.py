# Part of Odoo. See LICENSE file for full copyright and licensing details.
from odoo import _, http
from odoo.http import request
from odoo.addons.portal.controllers import portal


class PaymentPortal(portal.CustomerPortal):
    @http.route(['/payment/ebizcharge/manage/token'], type='json', auth='public',methods=["POST"], csrf=False)
    def ebizcharge_get_token_manage(self, pm_id):
        return request.env['payment.token'].get_payment_token_information(pm_id)


class EbizCustomerPortal(http.Controller):

    @http.route('/my/ebiz_payment_method', type="http", website=True, auth='user')
    def ebiz_payment_method_content(self, **kw):
        ebiz_list = request.env['payment.method'].sudo().search([])
        if request.env.user.partner_id.ebiz_profile_id:
            profile = request.env.user.partner_id.ebiz_profile_id
        else:
            profile = request.env['ebizcharge.instance.config'].sudo().search(
                [('website_ids', 'in', request.website.ids), ('is_website', '=', True), ('is_active', '=', True)])
        verify_card_before_saving = ''
        if profile:
            show_ach = profile.merchant_data
            show_credit_cards = profile.allow_credit_card_pay
            allowed_commands = profile.ebiz_website_allowed_command
            auth_only = True if allowed_commands == 'pre-auth' else False
            if profile.verify_card_before_saving:
                verify_card_before_saving = 'true'
            # odooPartner = request.env['res.partner'].browse(res.context['partner_id']).ensure_one()
            odooPartner = request.env.user.partner_id
            if odooPartner:
                odooPartner.sudo().with_context(
                    {'donot_sync': True, 'website': request.website.id}).ebiz_get_payment_methods()
            payment_tokens = odooPartner.payment_token_ids
            payment_tokens |= odooPartner.commercial_partner_id.sudo().payment_token_ids
        values = {
            'logIn': True if request.session['session_token'] else False,
            'showACH': show_ach,
            'providerid':  request.env['payment.provider'].sudo().search([('code', '=', 'ebizcharge'),('company_id', '=', request.env.company.id)],limit=1).id,
            'showCreditCards': show_credit_cards,
            'VerifyCreditCardBeforeSaving': verify_card_before_saving,
            'authOnly': show_credit_cards,
            'tokens': payment_tokens,
        }

        return request.render('payment_ebizcharge_portal.payment_methods', values)


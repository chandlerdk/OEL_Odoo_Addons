# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
from odoo.http import request
from odoo.addons.account.controllers import portal
from odoo.addons.payment.controllers.portal import PaymentPortal

class PortalAccount(portal.PortalAccount, PaymentPortal):

    def _invoice_get_page_view_values(self, invoice, access_token, **kwargs):
        values = super()._invoice_get_page_view_values(invoice, access_token, **kwargs)

        if not invoice._has_to_be_paid():
            # Do not compute payment-related stuff if given invoice doesn't have to be paid.
            return values

        logged_in = not request.env.user._is_public()
        # We set partner_id to the partner id of the current user if logged in, otherwise we set it
        # to the invoice partner id. We do this to ensure that payment tokens are assigned to the
        # correct partner and to avoid linking tokens to the public user.
        partner_sudo = request.env.user.partner_id if logged_in else invoice.partner_id
        invoice_company = invoice.company_id or request.env.company

        # Select all the payment methods and tokens that match the payment context.
        providers_sudo = request.env['payment.provider'].sudo()._get_compatible_providers(
            invoice_company.id,
            partner_sudo.id,
            invoice.amount_total,
            currency_id=invoice.currency_id.id
        )  # In sudo mode to read the fields of providers and partner (if logged out).
        payment_methods_sudo = request.env['payment.method'].sudo()._get_compatible_payment_methods(
            providers_sudo.ids,
            partner_sudo.id,
            currency_id=invoice.currency_id.id,
        )  # In sudo mode to read the fields of providers.
        tokens_sudo = request.env['payment.token'].sudo()._get_available_tokens(
            providers_sudo.ids, partner_sudo.id
        )  # In sudo mode to read the partner's tokens (if logged out) and provider fields.

        # Make sure that the partner's company matches the invoice's company.
        company_mismatch = not PaymentPortal._can_partner_pay_in_company(
            partner_sudo, invoice_company
        )

        portal_page_values = {
            'company_mismatch': company_mismatch,
            'expected_company': invoice_company,
        }
        payment_form_values = {
            'show_tokenize_input_mapping': PaymentPortal._compute_show_tokenize_input_mapping(
                providers_sudo
            ),
        }
        ebiz_provider_enabled = providers_sudo.sudo().filtered(lambda x: x.code == 'ebizcharge' and x.state != 'disabled')
        if ebiz_provider_enabled:
            is_sur_able = False
            show_ach = False
            show_credit_cards = False
            card_narrations = False
            surcharge_terms = False
            if partner_sudo.ebiz_profile_id:
                profile = partner_sudo.ebiz_profile_id
            else:
                profile = request.env['ebizcharge.instance.config'].sudo().search(
                    [('website_ids', 'in', request.website.ids), ('is_website', '=', True)])
            if profile:
                show_ach = profile.merchant_data
                show_credit_cards = profile.allow_credit_card_pay
                surcharge_terms = ''
                if profile.is_surcharge_enabled and profile.surcharge_type_id == 'DailyDiscount':
                    is_sur_able = True
                    surcharge_terms = profile.surcharge_terms
                card_narrations = profile.surcharge_terms
            if  invoice.partner_id:
                ebiz_partner = partner_sudo
            payment_context = {
                # 'showACH': show_ach,
                # 'showCreditCards': show_credit_cards,
                # 'cardNarrations': card_narrations,
                # 'surcharge_terms': surcharge_terms,
                # 'is_sur_able': is_sur_able,
                # 'authOnly': False,
                # 'logIn': True if request.session['session_token'] else False,
                'ebiz_tokens': request.env['payment.token'].sudo().search(
                    [('provider_id', 'in', providers_sudo.filtered(lambda i: i.code == "ebizcharge").ids), ('partner_id', '=', ebiz_partner.id)]),
                'tokens': request.env['payment.token'].sudo().search(
                    [('provider_id', 'in', providers_sudo.sudo().filtered(lambda i: i.code != "ebizcharge").ids), ('partner_id', '=', ebiz_partner.id)]),
                'amount': invoice.amount_residual,
                'currency': invoice.currency_id,
                'partner_id': partner_sudo.id,
                'providers_sudo': providers_sudo.sudo(),
                'providers_ebiz': providers_sudo.sudo().filtered(lambda i: i.code == "ebizcharge"),
                'payment_methods_sudo': payment_methods_sudo,

                'tokens_sudo': tokens_sudo.filtered(lambda i:i.provider_id.code != 'ebizcharge'),
                'transaction_route': f'/invoice/transaction/{invoice.id}/',
                'landing_route': invoice.get_portal_url(),
                'access_token': access_token,
            }
            values.update(
                **portal_page_values,
                **payment_form_values,
                **payment_context,
                **self._get_extra_payment_form_values(**kwargs),
            )
            return values
        else:
            res = super(PortalAccount, self)._invoice_get_page_view_values(invoice, access_token, **kwargs)
            return res
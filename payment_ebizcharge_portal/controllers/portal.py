# -*- coding: utf-8 -*-
from odoo import fields, http, _, api
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager, get_records_pager
from odoo.addons.portal.controllers import portal
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.addons.payment.controllers.post_processing import PaymentPostProcessing
from odoo.addons.payment.controllers.portal import PaymentPortal
from odoo.fields import Command

import urllib.parse

import werkzeug

from odoo import _, http
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment.controllers.post_processing import PaymentPostProcessing
from odoo.addons.portal.controllers import portal


class EbizWebsitePayment(portal.CustomerPortal):

    @http.route('/payment/pay', type='http', methods=['GET'], auth='public', website=True, sitemap=False)
    def payment_pay(self, reference=None, amount=None, currency_id=None, partner_id=None, company_id=None,
                    provider_id=None, access_token=None, invoice_id=None, **kwargs):

        res = super(EbizWebsitePayment, self).payment_pay(reference=reference, amount=amount, currency_id=currency_id,
                                                          partner_id=partner_id, company_id=company_id,
                                                          provider_id=provider_id, access_token=access_token,
                                                          invoice_id=invoice_id, custom_create_values={'invoice_ids': [Command.set([invoice_id])]}, **kwargs)
        if invoice_id:
            inv = request.env['account.move'].sudo().search([('id', '=', int(invoice_id))])
            #if request.env.user.partner_id.id != inv.partner_id.id:
             #   raise UserError('Make sure your are logged in as the right partner before making this payment.')
            inv.partner_id.refresh_payment_methods()
        if res.status_code == 200:
            odooInvoice = request.env['account.move'].sudo().search([('name', '=', reference),
                                                                     ('payment_state', '=', 'paid')])
            if odooInvoice and odooInvoice.partner_id.ebiz_profile_id:
                return request.render("payment_ebizcharge_crm.payment_already_paid")

        if res and 'tokens_sudo' in res.qcontext and 'providers_sudo' in res.qcontext:
            payment_tokens = res.qcontext['tokens_sudo']
            ebiz_payment_tokens = request.env['payment.token'].sudo().search([
                ('partner_id' ,'=', request.env.user.partner_id.id),('provider_code','=', "ebizcharge"),
                ('company_id','=',request.env.user.company_id.id)
            ])
            res.qcontext['providers_ebiz'] = res.qcontext['providers_sudo'].sudo().filtered(lambda i: i.code == "ebizcharge")
            # if len(res.qcontext['providers_sudo'].sudo()) > 1:
                # res.qcontext['providers_sudo'] = res.qcontext['providers_sudo'].sudo().filtered(lambda i: i.code != "ebizcharge")
                # res.qcontext['payment_methods_sudo'] = res.qcontext['payment_methods_sudo'].sudo().filtered(lambda i: i.code != "ebizcharge")

            res.qcontext['tokens_sudo'] = payment_tokens.sudo().filtered(
                lambda r: r.provider_id.sudo().code != "ebizcharge")
            res.qcontext['ebiz_tokens'] = ebiz_payment_tokens
        return res

    def _get_extra_payment_form_values(self, **kwargs):
        """ Return a dict of additional rendering context values.

        :param dict kwargs: Optional data. This parameter is not used here
        :return: The dict of additional rendering context values
        :rtype: dict
        """
        rendering_context_values = super()._get_extra_payment_form_values(**kwargs)
        ebiz_providers = request.env['payment.provider'].sudo().search(
            [('code', '=', 'ebizcharge'), ('company_id', '=', request.env.user.company_id.id),
             ('state', '!=', 'disabled')])
        profile = False
        if ebiz_providers:
            if request.env.user.partner_id.ebiz_profile_id and not request.env.user._is_public():
                profile = request.env.user.partner_id.ebiz_profile_id
            elif 'invoice_id' in rendering_context_values:
                inv_load = request.env['account.move'].sudo().search([('id','=', rendering_context_values['invoice_id'])], limit=1)
                if inv_load:
                    profile = inv_load.partner_id.ebiz_profile_id
                    if not profile:
                        profile = request.env['ebizcharge.instance.config'].sudo().search(
                                                [('website_ids', 'in', request.website.ids), ('is_website', '=', True), ('is_active', '=', True)])
            else:
                # raise UserError(str('test here'))
                profile = request.env['ebizcharge.instance.config'].sudo().search(
                    [('website_ids', 'in', request.website.ids), ('is_website', '=', True), ('is_active', '=', True)], limit=1)
            if profile:
                showACH = profile.merchant_data
                showCreditCards = profile.allow_credit_card_pay
                allowedCommands = profile.ebiz_website_allowed_command
                authOnly = True if allowedCommands == 'pre-auth' else False
                rendering_context_values['logIn'] = True if request.session['session_token'] else False
                is_sur_able = False
                surcharge_terms = ''
                if profile.is_surcharge_enabled and profile.surcharge_type_id == 'DailyDiscount':
                    is_sur_able = True
                    surcharge_terms = profile.surcharge_terms

                rendering_context_values['surcharge_terms'] = surcharge_terms
                rendering_context_values['is_sur_able'] = is_sur_able

                rendering_context_values['showACH'] = showACH
                rendering_context_values['showCreditCards'] = showCreditCards
                rendering_context_values['authOnly'] = authOnly
        return rendering_context_values



class TransactionPortal(CustomerPortal):

    @http.route(['/my/orders/<int:order_id>'], type='http', auth="public", website=True)
    def portal_order_page(self, order_id, report_type=None, access_token=None, message=False, download=False, **kw):
        res = super(TransactionPortal, self).portal_order_page(order_id, report_type=report_type,
                                                               access_token=access_token, message=message,
                                                               download=download, **kw)
        if res.status_code == 200:
            ebiz_charge_transaction = request.env['sale.order'].sudo().browse(order_id).transaction_ids.filtered(lambda x: x.provider_code== 'ebizcharge')
            if ebiz_charge_transaction:
                transaction_obj = request.env['transaction.history']
                if 'sale_order' in res.qcontext:
                    if 'search' in kw and kw['search'] != "":
                        transactions = transaction_obj.sudo().search([('invoice_id', '=', res.qcontext['sale_order'].name)])
                        if not transactions:
                            response = self.getTransactionData(res.qcontext['sale_order'])
                            transaction_obj.sudo().search([]).unlink()
                            transaction_obj.sudo().create(response)
                            transactions = transaction_obj.sudo().search([('invoice_id', '=', res.qcontext['sale_order'].name)])
                        transactions = transactions.sudo().search([('ref_no', '=', kw['search'])])
                    else:
                        response = self.getTransactionData(res.qcontext['sale_order'])
                        transaction_obj.sudo().search([]).unlink()
                        transaction_obj.sudo().create(response)
                        transactions = transaction_obj.sudo().search([('invoice_id', '=', res.qcontext['sale_order'].name)])
                    res.qcontext.update({
                        'transactions': transactions,
                    })
        return res


class PaymentPortal(portal.CustomerPortal):



    def _create_transaction(
            self, provider_id, payment_method_id, token_id, amount, currency_id, partner_id, flow,
            tokenization_requested, landing_route, reference_prefix=None, is_validation=False, token_ebiz=None,
            custom_create_values=None, **kwargs
    ):
        """ Create a draft transaction based on the payment context and return it.

        :param int provider_id: The provider of the provider payment method or token, as a
                                `payment.provider` id.
        :param int|None payment_method_id: The payment method, if any, as a `payment.method` id.
        :param int|None token_id: The token, if any, as a `payment.token` id.
        :param float|None amount: The amount to pay, or `None` if in a validation operation.
        :param int|None currency_id: The currency of the amount, as a `res.currency` id, or `None`
                                     if in a validation operation.
        :param int partner_id: The partner making the payment, as a `res.partner` id.
        :param str flow: The online payment flow of the transaction: 'redirect', 'direct' or 'token'.
        :param bool tokenization_requested: Whether the user requested that a token is created.
        :param str landing_route: The route the user is redirected to after the transaction.
        :param str reference_prefix: The custom prefix to compute the full reference.
        :param bool is_validation: Whether the operation is a validation.
        :param dict custom_create_values: Additional create values overwriting the default ones.
        :param dict kwargs: Locally unused data passed to `_is_tokenization_required` and
                            `_compute_reference`.
        :return: The sudoed transaction that was created.
        :rtype: payment.transaction
        :raise UserError: If the flow is invalid.
        """
        if token_id and not provider_id:
            token_sudo = request.env['payment.token'].sudo().browse(token_id)
            provider_id = token_sudo.sudo().provider_id.id
                    
        if request.env['payment.provider'].sudo().browse(provider_id).code != 'ebizcharge':
            res = super()._create_transaction(provider_id, payment_method_id, token_id, amount, currency_id, partner_id, flow,
            tokenization_requested, landing_route, reference_prefix=None, is_validation=False,
            custom_create_values=custom_create_values, **kwargs)
            return res
        # Prepare create values
        new_flag = False
        if flow == 'direct' and not request.env.user._is_public():
            new_flag = True
            token_ebiz = None
            flow = 'token'
        if flow in ['redirect', 'direct']:  # Direct payment or payment with redirection
            provider_sudo = request.env['payment.provider'].sudo().browse(provider_id)
            token_id = None
            tokenize = bool(
                # Don't tokenize if the user tried to force it through the browser's developer tools
                provider_sudo.allow_tokenization
                # Token is only created if required by the flow or requested by the user
                and (provider_sudo._is_tokenization_required(**kwargs) or tokenization_requested)
            )
        elif flow == 'token':  # Payment by token
            token_sudo = request.env['payment.token'].sudo().browse(token_id)

            # Prevent from paying with a token that doesn't belong to the current partner (either
            # the current user's partner if logged in, or the partner on behalf of whom the payment
            # is being made).

            if token_sudo.partner_id:
                partner_id = token_sudo.partner_id.id
            partner_sudo = request.env['res.partner'].sudo().browse(partner_id)
            
            # if partner_sudo.commercial_partner_id != token_sudo.partner_id.commercial_partner_id:
            #     raise AccessError(_("You do not have access to this payment token."))

            provider_sudo = token_sudo.provider_id
            payment_method_id = token_sudo.payment_method_id.id
            tokenize = False
        else:
            raise ValidationError(
                _("The payment should either be direct, with redirection, or made by a token.")
            )

        reference = request.env['payment.transaction']._compute_reference(
            provider_sudo.code,
            prefix=reference_prefix,
            **(custom_create_values or {}),
            **kwargs
        )
        if is_validation:  # Providers determine the amount and currency in validation operations
            amount = provider_sudo._get_validation_amount()
            currency_id = provider_sudo._get_validation_currency().id

        # Create the transaction
        tx_sudo = request.env['payment.transaction'].sudo().create({
            'provider_id': provider_sudo.sudo().id,
            'payment_method_id': payment_method_id,
            'reference': reference,
            'amount': amount,
            'currency_id': currency_id,
            'partner_id': partner_id,
            'token_id': token_id,
            'operation': f'online_{flow}' if not is_validation else 'validation',
            'tokenize': tokenize,
            'landing_route': landing_route,
            **(custom_create_values or {}),
        })  # In sudo mode to allow writing on callback fields


        if flow == 'token':
            if token_sudo.token_type == 'credit':
                partner_obj = partner_sudo
                instance = partner_obj.ebiz_profile_id
                if not new_flag:
                    if not instance.use_full_amount_for_avs:
                        resp = self.validate_card_runcustomertransaction(partner_obj, token_sudo)
            if 'web_pay' in kwargs:

                tx_sudo.with_context({'web_pay': kwargs['web_pay'],
                                      'from_portal': True})._send_payment_request()  # Payments by token process transactions immediately

            else:
                tx_sudo.sudo().write({
                    'transaction_type': 'pre_auth'
                })
                tx_sudo.with_context({'set_done': True, 'from_portal': True, 'web_pay': '1', })._send_payment_request()
        else:
            tx_sudo.with_context(
                {'set_done': True, 'from_portal': True, 'web_pay': '1', 'run_transaction': '1'})._log_sent_message(
                token_ebiz=token_ebiz)

        # Monitor the transaction to make it available in the portal.
        PaymentPostProcessing.monitor_transaction(tx_sudo)
        new_token = request.env['payment.token'].sudo().search([('id', '=', token_id)], limit=1)
        if not new_token.is_card_save:
            new_token.delete_payment_method()
            partner = request.env['res.partner'].sudo().browse(partner_id)
            partner.refresh_payment_methods()
            
        return tx_sudo


    @staticmethod
    def _validate_transaction_kwargs(kwargs, additional_allowed_keys=()):
        """ Verify that the keys of a transaction route's kwargs are all whitelisted.

        The whitelist consists of all the keys that are expected to be passed to a transaction
        route, plus optional contextually allowed keys.

        This method must be called in all transaction routes to ensure that no undesired kwarg can
        be passed as param and then injected in the create values of the transaction.

        :param dict kwargs: The transaction route's kwargs to verify.
        :param tuple additional_allowed_keys: The keys of kwargs that are contextually allowed.
        :return: None
        :raise ValidationError: If some kwargs keys are rejected.
        """
        whitelist = {
            'provider_id',
            'payment_method_id',
            'token_id',
            'amount',
            'flow',
            'tokenization_requested',
            'landing_route',
            'is_validation',
            'token_ebiz',
            'csrf_token',
        }
        whitelist.update(additional_allowed_keys)
        rejected_keys = set(kwargs.keys()) - whitelist
        if rejected_keys:
            raise ValidationError(
                _("The following kwargs are not whitelisted: %s", ', '.join(rejected_keys))
            )

    def validate_card_runcustomertransaction(self, partner, token):
        try:
            # security_code = self.security_code
            instance = partner.ebiz_profile_id

            ebiz = request.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            params = {
                "securityToken": ebiz._generate_security_json(),
                "custNum": partner.ebizcharge_customer_token,
                "paymentMethodID": token.ebizcharge_profile,
                "tran": {
                    "isRecurring": False,
                    "IgnoreDuplicate": False,
                    "Details": self.transaction_details(),
                    "Software": 'Odoo CRM',
                    "MerchReceipt": True,
                    "CustReceiptName": '',
                    "CustReceiptEmail": '',
                    "CustReceipt": False,
                    # "CardCode": security_code,
                    "Command": 'AuthOnly',
                },
            }
            resp = ebiz.client.service.runCustomerTransaction(**params)
            resp_void = ebiz.execute_transaction(resp['RefNum'], {'command': 'Void'})
        except Exception as e:
            raise ValidationError(e)
        return resp

    def transaction_details(self):
        return {
            'OrderID': "Token",
            'Invoice': "Token",
            'PONum': "Token",
            'Description': 'description',
            'Amount': 0.05,
            'Tax': 0,
            'Shipping': 0,
            'Discount': 0,
            'Subtotal': 0.05,
            'AllowPartialAuth': False,
            'Tip': 0,
            'NonTax': True,
            'Duty': 0
        }



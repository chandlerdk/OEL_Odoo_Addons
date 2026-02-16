# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
from odoo import _ , http
from odoo.exceptions import AccessError, MissingError, ValidationError
from odoo.fields import Command
from odoo.http import request, route
from odoo.addons.payment.controllers import portal as payment_portal
import json


class PaymentPortal(payment_portal.PaymentPortal):


    @http.route('/my/orders/<int:order_id>/transaction', type='json', auth='public')
    def portal_order_transaction(self, order_id, access_token, **kwargs):
        """ Create a draft transaction and return its processing values.

        :param int order_id: The sales order to pay, as a `sale.order` id
        :param str access_token: The access token used to authenticate the request
        :param dict kwargs: Locally unused data passed to `_create_transaction`
        :return: The mandatory values for the processing of the transaction
        :rtype: dict
        :raise: ValidationError if the invoice id or the access token is invalid
        """
        # Check the order id and the access token
        try:
            order_sudo = self._document_check_access('sale.order', order_id, access_token)
        except MissingError as error:
            raise error
        except AccessError:
            raise ValidationError(_("The access token is invalid."))

        prv_id_ebiz = 0
        if 'token_id' in kwargs and kwargs['token_id']!=None:
            ebiztokenid = request.env['payment.token'].search([('id','=',int(kwargs['token_id']))], limit=1)
            prv_id_ebiz = ebiztokenid.provider_id.id
            kwargs.update({
                'provider_id': ebiztokenid.provider_id.id
            })
        # if request.env['payment.provider'].sudo().browse(kwargs['provider_id']).code != 'ebizcharge':
        #     res = super(PaymentPortal, self).portal_order_transaction(order_id, access_token, **kwargs)
        #     return res
        logged_in = not request.env.user._is_public()
        partner_sudo = request.env.user.partner_id if logged_in else order_sudo.partner_invoice_id
        self._validate_transaction_kwargs(kwargs)
        kwargs.update({
            'partner_id': partner_sudo.id,
            'currency_id': order_sudo.currency_id.id,
            # 'provider_id': prv_id_ebiz,
                'sale_order_id': order_id,  # Include the SO to allow Subscriptions tokenizing the tx
        })
        tx_sudo = self._create_transaction(
            custom_create_values={'sale_order_ids': [Command.set([order_id])]}, **kwargs,
        )

        return tx_sudo._get_processing_values()


    @route('/invoice/transaction/<int:invoice_id>', type='json', auth='public')
    def invoice_transaction(self, invoice_id, access_token, **kwargs):
        """ Create a draft transaction and return its processing values.

        :param int invoice_id: The invoice to pay, as an `account.move` id
        :param str access_token: The access token used to authenticate the request
        :param dict kwargs: Locally unused data passed to `_create_transaction`
        :return: The mandatory values for the processing of the transaction
        :rtype: dict
        :raise: ValidationError if the invoice id or the access token is invalid
        """
        # Check the invoice id and the access token
        try:
            invoice_sudo = self._document_check_access('account.move', invoice_id, access_token)
        except MissingError as error:
            raise error
        except AccessError:
            raise ValidationError(_("The access token is invalid."))
            
        prv_id_ebiz = 0
        if 'token_id' in kwargs and kwargs['token_id']!=None :
            ebiztokenid = request.env['payment.token'].search([('id','=',int(kwargs['token_id']))], limit=1)
            prv_id_ebiz = ebiztokenid.provider_id.id
            kwargs.update({
                'provider_id': ebiztokenid.provider_id.id
            })    
        # if request.env['payment.provider'].sudo().browse(kwargs['provider_id']).code != 'ebizcharge':
        #     res = super(PaymentPortal, self).invoice_transaction(invoice_id, access_token, **kwargs)
        #     return res
        logged_in = not request.env.user._is_public()
        partner_sudo = request.env.user.partner_id if logged_in else invoice_sudo.partner_id
        self._validate_transaction_kwargs(kwargs)
        kwargs.update({
            'currency_id': invoice_sudo.currency_id.id,
            'partner_id': partner_sudo.id,
            'web_pay':  "1",
            # 'provider_id': prv_id_ebiz,
        })  # Inject the create values taken from the invoice into the kwargs.
        kwargs.pop('custom_create_values', None)  # Don't allow passing arbitrary create values
        tx_sudo = self._create_transaction(
            custom_create_values={'invoice_ids': [Command.set([invoice_id])]}, **kwargs,
        )

        return tx_sudo._get_processing_values()


    @http.route(['/refresh_payment_profiles'], type='json', auth='public', )
    def refresh_payment_profiles(self, **kw):
        request.env.user.partner_id.sync_to_ebiz()
        request.env.user.partner_id.refresh_payment_methods(ecom_side=True)
        res = {
            'result': True,
        }
        json_object = json.dumps(res)
        return json_object
        

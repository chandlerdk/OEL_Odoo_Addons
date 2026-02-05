
import logging
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import ValidationError, UserError
from odoo.tools import format_amount

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'
    _description = "Payment Transactions"

    _ebizcharge_valid_tx_status = 1
    _ebizcharge_pending_tx_status = 4
    _ebizcharge_cancel_tx_status = 2

    # --------------------------------------------------
    # FORM RELATED METHODS
    # --------------------------------------------------
    ebiz_auth_code = fields.Char(string='Auth Code')
    security_code = fields.Char(string="Security Code")
    transaction_type = fields.Selection([
        ('pre_auth', 'Pre-Authorize'),
        ('deposit', 'Deposit'),
    ], string='Transaction Type', index=True)
    surcharge_percent = fields.Float(string='Surcharge %')
    is_pay_method_eligible = fields.Boolean(string='Card Eligible')
    is_zip_code_allowed = fields.Boolean(string='Zip Code Allowed')
    surcharge_amt = fields.Monetary(string='Surcharge Amount')
    emv_transaction = fields.Boolean(string='EMV', default=False)

    reminder_amount = fields.Monetary(string='Reminder Amount')
    captured_amount = fields.Monetary(string='Captured Amount')
    ebiz_avs_street = fields.Char(string='AVS Street')
    ebiz_avs_zip_code = fields.Char(string='AVS Zip Code')
    ebiz_cvv_resp = fields.Char(string='CVV Resp')
    ebiz_transaction_status = fields.Char(string='Transaction Status')
    ebiz_transaction_result = fields.Char(string='Result')

    def _check_amount_and_confirm_order(self):
        """ Confirm the sales order based on the amount of a transaction.

        Confirm the sales orders only if the transaction amount (or the sum of the partial
        transaction amounts) is equal to or greater than the required amount for order confirmation

        Grouped payments (paying multiple sales orders in one transaction) are not supported.

        :return: The confirmed sales orders.
        :rtype: a `sale.order` recordset
        """
        confirmed_orders = self.env['sale.order']
        for tx in self:
            # We only support the flow where exactly one quotation is linked to a transaction.
            if len(tx.sale_order_ids) == 1:
                if tx.transaction_type not in ['pre_auth',
                                               'deposit'] or 'from_portal' in self.env.context or tx.provider_id.code != 'ebizcharge':
                    quotation = tx.sale_order_ids.filtered(lambda so: so.state in ('draft', 'sent'))
                    if quotation and quotation._is_confirmation_amount_reached():
                        quotation.with_context(send_email=True).action_confirm()
                        confirmed_orders |= quotation
        return confirmed_orders

    def _send_invoice(self):
        template_id = int(self.env['ir.config_parameter'].sudo().get_param(
            'sale.default_invoice_email_template',
            default=0
        ))
        if not template_id:
            return
        template = self.env['mail.template'].browse(template_id).exists()
        if not template:
            return

        for tx in self:
            if tx.provider_id.code != 'ebizcharge':
                tx = tx.with_company(tx.company_id).with_context(
                    company_id=tx.company_id.id,
                )
                invoice_to_send = tx.invoice_ids.filtered(
                    lambda i: not i.is_move_sent and i.state == 'posted' and i._is_ready_to_be_sent()
                )
                invoice_to_send.is_move_sent = True  # Mark invoice as sent
                invoice_to_send.with_user(SUPERUSER_ID)._generate_pdf_and_send_invoice(template)

    @api.model_create_multi
    def create(self, val_list):
        # The reference is used in the Authorize form to fill a field (invoiceNumber) which is
        # limited to 20 characters. We truncate the reference now, since it will be reused at
        # payment validation to find back the transaction.

        for vals in val_list:
            if 'provider_id' in vals:
                acquirer = self.env['payment.provider'].browse(vals['provider_id'])
                if acquirer.code == 'ebizcharge':
                    if 'reference' in vals and 'provider_id' in vals:
                        if vals['reference']:
                            if acquirer.code == 'ebizcharge':
                                vals['reference'] = vals.get('reference', '')[:20]
                    if 'payment_data' in self._context and 'invoice_ids' in vals:
                        vals.pop('invoice_ids')
                    if 'reference' in vals and 'invoice_ids' not in vals:
                        if vals['reference']:
                            if 'payment_data' in self._context and 'invoice_id' in self._context['payment_data']:
                                vals['invoice_ids'] = [(6, 0, self._context['payment_data']['invoice_id'])]
                            else:
                                if 'payment_id' in vals:
                                    if 'active_id' in self._context and self._context.get('active_id'):
                                        inv_dett = self.env['account.move'].search([('id', '=', self._context.get('active_id'))]).ids
                                        if inv_dett:
                                            vals['invoice_ids'] = [(6, 0, inv_dett)]
                                    else:
                                        invoice_ref = self.env['account.payment'].search(
                                            [('id', '=', vals['payment_id'])]).move_id.ref
                                        vals['invoice_ids'] = [
                                            (6, 0, self.env['account.move'].search(
                                                [('name', '=', invoice_ref)]).ids)]
                                else:
                                    vals['invoice_ids'] = [
                                        (6, 0,
                                         self.env['account.move'].search([('invoice_origin', '=', vals['reference'])]).ids)]
                    if 'default_order_id' in self._context:
                        vals.update({'sale_order_ids': [self._context['default_order_id']]})
                    if 'reference' in vals:
                        vals.pop('reference')
        res = super().create(val_list)
        for trans in res:
            if trans.provider_id.code == 'ebizcharge':
                prefix = trans.reference.split('-')[0]
                if not prefix or prefix == 'tx':
                    payment_id = trans.payment_id
                    if payment_id and payment_id.payment_type == 'inbound':
                        prefix = self.env['ir.sequence'].next_by_code('advance.payment.transaction',
                                                                      sequence_date=trans.last_state_change)
                    if payment_id and payment_id.payment_type == 'outbound':
                        prefix = self.env['ir.sequence'].next_by_code('advance.payment.transaction',
                                                                      sequence_date=trans.last_state_change)
        return res

    def _get_tx_from_notification_data(self, provider_code, notification_data):
        """ Override of payment to find the transaction based on EBizCharge data.

        :param str provider_code: The code of the provider that handled the transaction
        :param dict notification_data: The notification data sent by the provider
        :return: The transaction if found
        :rtype: recordset of `payment.transaction`
        :raise: ValidationError if inconsistent data were received
        :raise: ValidationError if the data match no transaction
        """
        tx = super()._get_tx_from_notification_data(provider_code, notification_data)
        if provider_code != 'ebizcharge' or len(tx) == 1:
            return tx
        _logger.info(notification_data)
        reference = notification_data.get("TransactionLookupKey")
        if not reference:
            error_msg = _('EBizCharge: received data with missing reference (%s)') % (reference)
            _logger.info(error_msg)
            raise ValidationError(error_msg)
        tx = self.search([('reference', '=', reference)])
        _logger.info(str(tx))
        if not tx or len(tx) > 1:
            error_msg = 'EBizCharge: received data for reference %s' % (reference)
            if not tx:
                error_msg += '; no order found'
            else:
                error_msg += '; multiple order found'
            _logger.info(error_msg)
            raise ValidationError(error_msg)
        return tx[0]

    def _get_received_message(self):
        """ Return the message stating that the transaction has been received by the provider.

        Note: self.ensure_one()
        """
        self.ensure_one()
        if self.provider_code != 'ebizcharge':
            return super()._get_received_message()
        if 'from_invoice' in self.env.context:
            formatted_amount = self.env.context.get('invoice_id').amount_residual if self.env.context.get(
                'invoice_id').payment_state != 'paid' else self.env.context.get('invoice_id').amount_total
        else:
            formatted_amount = format_amount(self.env, self.amount, self.currency_id)
        if self.state == 'pending':
            message = _(
                "The transaction with reference %(ref)s for amount %(amount)s is pending (%(acq_name)s).",
                ref=self.reference, amount=formatted_amount, acq_name=self.provider_id.name
            )
        elif self.state == 'authorized':
            msg = 'authorized'
            if 'from_invoice' in self.env.context:
                msg = 'captured'
            message = _(
                "The transaction with reference %(ref)s for amount %(amount)s has been %(msg)s "
                "(%(acq_name)s).", ref=self.reference, msg=msg, amount=formatted_amount,
                acq_name=self.provider_id.name
            )
        elif self.state == 'done':
            msg = 'confirmed'
            if self.transaction_type == 'deposit':
                msg = 'deposited'
            if self.transaction_type == 'pre_auth':
                msg = 'captured'
            message = _(
                "The transaction with reference %(ref)s for amount %(amount)s has been  %(msg)s "
                "(%(acq_name)s).", ref=self.reference, msg=msg, amount=formatted_amount,
                acq_name=self.provider_id.name
            )
        elif self.state == 'error':
            message = _(
                "The transaction with reference %(ref)s for amount %(amount)s encountered an error"
                " (%(acq_name)s).",
                ref=self.reference, amount=formatted_amount, acq_name=self.provider_id.name
            )
            if self.state_message:
                message += "<br />" + _("Error: %s", self.state_message)
        else:
            message = _(
                "The transaction with reference %(ref)s for amount %(amount)s is canceled (%(acq_name)s).",
                ref=self.reference, amount=formatted_amount, acq_name=self.provider_id.name
            )
            if self.state_message:
                message += "<br />" + _("Reason: %s", self.state_message)
        if self.reference =='EBiz_EMV':
            message = "EMV Device Transaction Completed"        
        return message

    def _log_sent_message(self, token_ebiz=None):
        """ Override of payment to simulate a payment request.
                Note: self.ensure_one()
                :return: None
                """
        super()._log_sent_message()
        if self.provider_code != 'ebizcharge':
            return
        if token_ebiz!=None and 'cardData' in token_ebiz:
            self.ensure_one()
            command = 'Sale'
            token_latest = self.env['payment.token'].search([('partner_id', '=', self.partner_id.id)], order='create_date DESC',
                                                           limit=1)
            if self.sale_order_ids:
                if 'pre_auth_order' not in self.env.context:
                    if self.partner_id.ebiz_profile_id.ebiz_website_allowed_command == 'pre-auth':
                        command = "AuthOnly"
                    else:
                        command = "Sale"
                else:
                    command = "AuthOnly"
                    if self.transaction_type == 'deposit':
                        command = "Sale"
                        self.payment_id.action_post()
                if self.security_code:
                    resp = self.sale_order_ids.run_ebiz_transaction(token_latest, command, token_ebiz=token_ebiz['cardData'])
                    if 'full_amount' not in self.env.context and resp['ResultCode'] not in ["D", "E"]:
                        self._set_authorized()
                    # self._set_done()
                    # self._reconcile_after_done()
                else:
                    resp = self.sale_order_ids.run_ebiz_transaction(token_latest, command, token_ebiz=token_ebiz['cardData'])
                    if 'full_amount' not in self.env.context and resp['ResultCode'] not in ["D", "E"]:
                        self._set_authorized()
                    if 'set_done' in self.env.context and resp['ResultCode'] not in ["D",
                                                                                     "E"] and self.partner_id.ebiz_profile_id.ebiz_website_allowed_command != 'pre-auth' and 'pre_auth_order' not in self.env.context:
                        self._set_done()
                    # if 'pre_auth_order' not in self.env.context:
                    # self._set_done()
                    # self._reconcile_after_done()
                resp['x_type'] = 'capture' if command == "Sale" else command
            elif self.invoice_ids:
                command = 'Sale'
                if self.env.context.get('run_transaction') and 'cardData' in token_ebiz:
                    card = token_ebiz['cardData']['cardNumber']
                    resp = self.invoice_ids.with_context({'run_transaction': True}).run_ebiz_transaction(token_latest,
                                                                                                         command,
                                                                                                         token_ebiz=token_ebiz['cardData'])
                else:
                    resp = self.invoice_ids.run_ebiz_transaction(token_latest, command, token_ebiz=token_ebiz['cardData'])
                resp['x_type'] = command
                self.invoice_ids[0].is_payment_processed = True
                if self.invoice_ids[0].save_payment_link:
                    self.invoice_ids[0].delete_ebiz_invoice()
            else:
                ebiz = self.get_ebiz_charge_obj(website_id=self.env['website'].get_current_website(fallback=False),
                                                instance=self.partner_id.ebiz_profile_id)
                resp = ebiz.run_transaction_without_invoice(self)
                resp['x_type'] = 'Sale'

            if self.invoice_ids and self.invoice_ids[0].move_type == 'out_refund':
                resp['x_type'] = 'refunded'
            self._ebizcharge_s2s_validate_tree(resp)

            # portal user command
            # if self.env.user._is_public():
            #     self.payment_token_id.write({'card_number': 'Processed'})
            # # The payment request response would normally transit through the controller but in the end,
            # # all that interests us is the reference. To avoid making a localhost request, we bypass the
            # # controller and handle the fake feedback data directly.
            # self._handle_feedback_data('ebizcharge', resp)
            self.write({
                'state_message': resp['Error'],
                "provider_reference": resp['RefNum'],
                "ebiz_auth_code": resp['AuthCode']
            })
            if 'web_pay' in self.env.context and self.env.context['web_pay'] == '1' and resp['ResultCode'] not in ["D","E"]:
                self._set_authorized()
                if self.sale_order_ids and self.partner_id.ebiz_profile_id.ebiz_website_allowed_command != 'pre-auth':
                    self._set_done()
                    self._reconcile_after_done()
                if self.invoice_ids:
                    self._set_done()
            self.get_surcharge_amount_new(card_num=token_ebiz['cardData']['cardNumber'] , zip_code=token_ebiz['cardData']['zip'])
            return resp

    def get_surcharge_amount_new(self, card_num=None, zip_code=None):
        ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=self.partner_id.ebiz_profile_id)
        if zip_code and card_num and ebiz:
            params = {
                'securityToken': ebiz._generate_security_json(),
                'customerInternalId': self.partner_id.ebiz_internal_id,
                'amount': self.amount,
                'cardNumber': card_num ,
                'cardZipCode': zip_code ,
            }
            resp = ebiz.client.service.CalculateSurchargeAmount(**params)
            self.update({
                'is_zip_code_allowed': resp['IsSurchargeAllowedForZipCode'],
                'is_pay_method_eligible': resp['IsSurchargeAllowedForPaymentMethod'],
                'surcharge_percent': float(resp['SurchargePercentage']) if resp['IsSurchargeEnabled'] else 0,
                'surcharge_amt': float(resp['SurchargeAmount']) if resp['IsSurchargeEnabled'] else 0,
            })

    def get_avs_street_zip(self, resp):
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

        self.ebiz_avs_street = address
        self.ebiz_avs_zip_code = zip_code
        return address.strip(), zip_code.strip()

    def _send_payment_request(self):
        """ Override of payment to simulate a payment request.
                Note: self.ensure_one()
                :return: None
                """
        super()._send_payment_request()
        if self.provider_code != 'ebizcharge':
            return
        if not self.token_id:
            raise UserError("EBizCharge: " + _("The transaction is not linked to a token."))
        self.ensure_one()
        if self.sale_order_ids:
            if 'pre_auth_order' not in self.env.context:
                if self.partner_id.ebiz_profile_id.ebiz_website_allowed_command == 'pre-auth':
                    command = "AuthOnly"
                else:
                    command = "Sale"
            else:
                command = "AuthOnly"
                if self.transaction_type == 'deposit':
                    command = "Sale"
                    self.payment_id.action_post()
            if self.security_code:
                resp = self.sale_order_ids.run_ebiz_transaction(self.token_id, command)
                if 'full_amount' not in self.env.context and resp['ResultCode'] not in ["D", "E"]:
                    self._set_authorized()
                # self._set_done()
                # self._reconcile_after_done()
            else:
                resp = self.sale_order_ids.run_ebiz_transaction(self.token_id, command)
                if 'full_amount' not in self.env.context and resp['ResultCode'] not in ["D", "E"]:
                    self._set_authorized()
                if 'set_done' in self.env.context and resp['ResultCode'] not in ["D", "E"] and self.partner_id.ebiz_profile_id.ebiz_website_allowed_command != 'pre-auth' and 'pre_auth_order' not in self.env.context:
                    self._set_done()
                # if 'pre_auth_order' not in self.env.context:
                # self._set_done()
                # self._reconcile_after_done()
            resp['x_type'] = 'capture' if command == "Sale" else command
        elif self.invoice_ids:
            command = 'Sale'
            if self.env.context.get('run_transaction'):
                resp = self.invoice_ids.with_context({'run_transaction': True}).run_ebiz_transaction(self.token_id,
                                                                                                     command,
                                                                                                     self.env.context.get('card'))
            else:
                resp = self.invoice_ids.run_ebiz_transaction(self.token_id, command)

            self.invoice_ids[0].is_payment_processed = True
            if self.invoice_ids[0].save_payment_link:
                self.invoice_ids[0].delete_ebiz_invoice()
            resp['x_type'] = command
        else:
            web_sale = self.env['ir.module.module'].sudo().search(
                [('name', '=', 'website_sale'), ('state', 'in', ['installed', 'to upgrade', 'to remove'])])
            if web_sale:
                website = self.env['website'].get_current_website(fallback=False)
            else:
                website = False

            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(website_id=website,
                                            instance=self.partner_id.ebiz_profile_id)
            resp = ebiz.run_transaction_without_invoice(self)
            resp['x_type'] = 'Sale'

        if self.invoice_ids and self.invoice_ids[0].move_type == 'out_refund':
            resp['x_type'] = 'refunded'
        self._ebizcharge_s2s_validate_tree(resp)
        # if self.env.user._is_public():
        #     self.payment_token_id.write({'card_number': 'Processed'})

        result = self.get_avs_street_zip(resp)
        self.write({
            'state_message': resp['Error'],
            "provider_reference": resp['RefNum'],
            "ebiz_auth_code": resp['AuthCode'],
            "ebiz_avs_street": result[0],
            "ebiz_avs_zip_code": result[1],
            "ebiz_cvv_resp": resp['CardCodeResult'],
            "ebiz_transaction_status": resp['Status'],
            "ebiz_transaction_result": resp['Result'],
        })

        if 'web_pay' in self.env.context and self.env.context['web_pay'] == '1' and resp['ResultCode'] not in ["D", "E"]:
            self._set_authorized()
            if self.sale_order_ids and self.partner_id.ebiz_profile_id.ebiz_website_allowed_command != 'pre-auth':
                self._set_done()
                self._reconcile_after_done()
            if self.invoice_ids:
                self._set_done()
                # raise UserError(str(self))
        if self.token_id.token_type == 'credit' and self.partner_id.ebiz_profile_id.is_surcharge_enabled and self.partner_id.ebiz_profile_id.surcharge_type_id == 'DailyDiscount':
            self.get_surcharge_amount()
        return resp

    def get_surcharge_amount(self):
        ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=self.partner_id.ebiz_profile_id)
        params = {
            'securityToken': ebiz._generate_security_json(),
            'customerInternalId': self.token_id.partner_id.ebiz_internal_id,
            'paymentMethodId': self.token_id.ebizcharge_profile,
            'amount': self.amount,
            'cardZipCode': self.token_id.avs_zip,
        }
        resp = ebiz.client.service.CalculateSurchargeAmount(**params)
        self.update({
            'is_zip_code_allowed': resp['IsSurchargeAllowedForZipCode'],
            'is_pay_method_eligible': resp['IsSurchargeAllowedForPaymentMethod'],
            'surcharge_percent': float(resp['SurchargePercentage']) if resp['IsSurchargeEnabled'] else 0,
            'surcharge_amt': float(resp['SurchargeAmount']) if resp['IsSurchargeEnabled'] else 0,
        })
        return resp

    def _send_capture_request(self,amount_to_capture=None):
        capture_child_tx = super()._send_capture_request(amount_to_capture=amount_to_capture)
        if self.provider_code != 'ebizcharge':
            return capture_child_tx
        for trans in self:
            ebiz_obj = self.env['ebiz.charge.api']
            if trans.payment_id:
                sale_order_ids = trans.sale_order_ids
                web_sale = self.env['ir.module.module'].sudo().search(
                    [('name', '=', 'website_sale'), ('state', 'in', ['installed', 'to upgrade', 'to remove'])])

                if web_sale:
                    ebiz = ebiz_obj.get_ebiz_charge_obj(sale_order_ids.website_id,
                                                              instance=self.partner_id.ebiz_profile_id)
                else:
                    ebiz = ebiz_obj.get_ebiz_charge_obj(instance=self.partner_id.ebiz_profile_id)
                if 'from_invoice' in self.env.context:
                    tree = ebiz.capture_transaction(trans, invoice=self.env.context.get('invoice_id'),
                                                    ebiz_transaction_amt=trans.amount)
                    capture_amount_calc = self.env.context.get('invoice_id').amount_residual if self.env.context.get(
                        'invoice_id').payment_state != 'paid' else self.env.context.get('invoice_id').amount_total

                    trans.captured_amount += capture_amount_calc
                    trans.reminder_amount = trans.amount - trans.captured_amount
                    trans.payment_id.amount = capture_amount_calc
                else:
                    if 'emv_trans' in self.env.context:
                        tree = ebiz.capture_transaction(trans, emv_trans=self.env.context.get('emv_trans'))
                        trans.captured_amount = trans.amount
                    elif sale_order_ids:
                        tree = ebiz.capture_transaction(trans, sale=sale_order_ids)
                        trans.captured_amount = trans.amount
                        
                if trans.payment_id.state == 'draft':
                    trans.payment_id.action_post()
                tree['x_type'] = 'capture'
                is_validated = trans._ebizcharge_s2s_validate_tree(tree)

                if trans.sale_order_ids.invoice_ids:
                    if trans.sale_order_ids.invoice_ids.filtered(lambda i: i.state == 'draft'):
                        for inv in trans.sale_order_ids.invoice_ids.filtered(lambda i: i.state == 'draft'):
                            inv.action_post()
                    trans.sale_order_ids.invoice_ids.action_capture_reconcile(trans.payment_id)
                if not trans.sale_order_ids.invoice_ids and trans.invoice_ids.filtered(lambda i: i.payment_state == 'not_paid'):
                    trans.invoice_ids.action_capture_reconcile(trans.payment_id)

            else:
                sale_order_ids = trans.sale_order_ids
                ebiz = ebiz_obj.get_ebiz_charge_obj(sale_order_ids.website_id,
                                                    instance=self.partner_id.ebiz_profile_id)

                if sale_order_ids:
                    if 'from_invoice' in self.env.context:
                        tree = ebiz.capture_transaction(trans, invoice=self.env.context.get('invoice_id'),
                                                        ebiz_transaction_amt=trans.amount)
                        trans.captured_amount = trans.amount
                    else:
                        tree = ebiz.capture_transaction(trans, sale=sale_order_ids)
                        trans.captured_amount = trans.amount
                else:
                    tree = ebiz.capture_transaction(trans)
                trans._reconcile_after_done()
                trans.is_post_processed = True
                # tree = ebiz.capture_transaction(trans)
                if tree:
                    tree['x_type'] = 'capture'
                    is_validated = trans._ebizcharge_s2s_validate_tree(tree)
                if trans.sale_order_ids.invoice_ids:
                    if trans.sale_order_ids.invoice_ids.filtered(lambda i: i.state == 'draft'):
                        for inv in trans.sale_order_ids.invoice_ids.filtered(lambda i: i.state == 'draft'):
                            inv.action_post()
                    trans.sale_order_ids.invoice_ids.action_capture_reconcile(trans.payment_id)
                if not trans.sale_order_ids.invoice_ids and trans.invoice_ids.filtered(
                        lambda i: i.payment_state == 'not_paid'):
                    trans.invoice_ids.action_capture_reconcile(trans.payment_id)
        return capture_child_tx

    def ebizcharge_s2s_capture_transaction(self):
        for trans in self:
            sale_order_ids = trans.sale_order_ids
            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(sale_order_ids.website_id,
                                                      instance=self.partner_id.ebiz_profile_id)
            tree = ebiz.capture_transaction(trans)
            tree['x_type'] = 'capture'
            is_validated = trans._ebizcharge_s2s_validate_tree(tree)
        return is_validated

    def _send_void_request(self, amount_to_void=None):
        super()._send_void_request()
        if self.provider_code != 'ebizcharge':
            return
        self.ensure_one()
        ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=self.partner_id.ebiz_profile_id)
        trans = self
        tree = ebiz.void_transaction(self, trans)
        tree['x_type'] = 'void'
        return self._ebizcharge_s2s_validate_tree(tree)

    def _create_refund_transaction(self, amount_to_refund=False,  **kwargs):
        rec = super()._create_refund_transaction(amount_to_refund=amount_to_refund)
        if self.provider_code == 'ebizcharge':
            self.ensure_one()
            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=self.partner_id.ebiz_profile_id)
            kwargs["ref_num"] = self.provider_reference
            resp = ebiz.return_transaction(**kwargs)
            resp['x_type'] = "refunded"
            return self._ebizcharge_s2s_validate_tree(resp)
        else:
            return rec

    def _ebizcharge_s2s_validate_tree(self, tree):
        return self._ebizcharge_s2s_validate(tree)

    def _ebizcharge_s2s_validate(self, tree, command="authorized"):
        self.ensure_one()
        if tree['ResultCode'] == "A":
            if tree['x_type'] in ['AuthOnly', 'sale', 'Sale']:
                self.write({
                    'provider_reference': tree['RefNum'],
                    'ebiz_auth_code': tree['AuthCode'],
                    'last_state_change': fields.Datetime.now(),
                })
                if 'full_amount' not in self.env.context:
                    self._set_authorized()
                return True
            if tree['x_type'] in ['capture', 'Check']:
                self.write({
                    'provider_reference': tree['RefNum'],
                    'ebiz_auth_code': tree['AuthCode'],
                })
                self._set_done()
            if tree['x_type'] == 'void':
                self._set_canceled()
            if tree['x_type'] == 'refunded':
                self.write({
                    'provider_reference': tree['RefNum'],
                    'ebiz_auth_code': tree['AuthCode'],
                })
            return True

        if tree['ResultCode'] in ["D", "E"]:
            self.write({
                'state_message': tree['Error'],
                'provider_reference': tree['RefNum'],
                'ebiz_auth_code': tree['AuthCode'],
            })
            self._set_canceled()
            if self.payment_id.state not in ['posted', 'cancel']:
                self.payment_id.action_cancel()

    def _set_authorized(self,state_message=None, **kwargs):
        """ Update the transactions' state to 'authorized'.

        :param str state_message: The reason for which the transaction is set in 'authorized' state
        :return: None
        """
        trans_type = "Authorized"
        if self.provider_code != 'ebizcharge':
            return super(PaymentTransaction, self)._set_authorized()

        self._ebiz_create_application_transaction(trans_type)
        return super(PaymentTransaction, self)._set_authorized()

    def _set_done(self):
        if self.provider_code != 'ebizcharge':
            return super(PaymentTransaction, self)._set_done()
        if self.state == 'authorized':
            trans_type = "Captured"
        else:
            trans_type = "Sale"
        self._ebiz_create_application_transaction(trans_type)
        return super(PaymentTransaction, self)._set_done()

    def _set_canceled(self):
        if self.provider_code != 'ebizcharge':
            return super(PaymentTransaction, self)._set_done()
        if self.state == 'authorized':
            trans_type = 'Voided'
            self._ebiz_create_application_transaction(trans_type)
        return super(PaymentTransaction, self)._set_canceled()

    def _ebiz_create_application_transaction(self, trans_type):
        if self.sale_order_ids:
            params = {
                "partner_id": self.sale_order_ids[0].partner_id.id,
                "sale_order_id": self.sale_order_ids[0].id,
                "transaction_id": self.id,
                "transaction_type": trans_type
            }
            app_trans = self.env['ebiz.application.transaction'].create(params)
            if self.sale_order_ids.ebiz_internal_id:
                app_trans.ebiz_add_application_transaction()
        return True

from odoo import fields, models, _


class WizardSaleOrderTransactionValidation(models.TransientModel):
    _name = 'wizard.ebiz.sale.order.transaction.validation'
    _description = "Wizard EBiz Sale Order Transaction Validation"

    wizard_process_id = fields.Many2one('custom.register.payment')
    address = fields.Char('Address', default="Match")
    zip_code = fields.Char('Zip/Postal Code', default="Match")
    card_code = fields.Char('CVV2/CVC', default="Match")
    check_avs_match = fields.Boolean(compute="_compute_avs_validation_resp")
    is_card_denied = fields.Boolean("Is Card Denied")
    denied_message = fields.Char("Denied Message")
    transaction_id = fields.Many2one('payment.transaction')
    full_amount_avs = fields.Boolean("Full Amount AVS")
    order_id = fields.Many2one('sale.order')

    def _compute_avs_validation_resp(self):
        self.check_avs_match = (self.card_code.strip() == 'Match') & (self.address.strip() == 'Match') & (
                    self.zip_code.strip() == 'Match')

    def process_transaction_anyway(self):
        """
        Kuldeep's implementation
        Proceed with transaction
        """
        if not self.wizard_process_id.payment_token_id:
            token_id = self.create_credit_card_payment_method().id
            self.wizard_process_id.payment_token_id = token_id
        if not self.transaction_id:
            ebiz_method = self.env['account.payment.method.line'].search(
                [('journal_id', '=', self.wizard_process_id.journal_id.id), ('payment_method_id.code', '=', 'ebizcharge')], limit=1)

            payment = self.env['account.payment'].sudo().create({'journal_id': self.wizard_process_id.journal_id.id,
                                                          'payment_method_id': ebiz_method.payment_method_id.id,
                                                          'payment_method_line_id':ebiz_method.id,
                                                          'payment_token_id': self.wizard_process_id.payment_token_id.id,
                                                          'amount': abs(self.wizard_process_id.amount),
                                                          'partner_id': self.wizard_process_id.sub_partner_id.id,
                                                          'partner_type': 'customer',
                                                          'payment_type': 'inbound',
                                                          'ebiz_avs_street': self.wizard_process_id.ebiz_avs_street,
                                                          'ebiz_avs_zip': self.wizard_process_id.ebiz_avs_zip,
                                                          'ebiz_send_receipt': self.wizard_process_id.ebiz_send_receipt,
                                                          'ebiz_receipt_emails': self.wizard_process_id.ebiz_receipt_emails,
                                                          })
            transactions = payment.with_context({'default_order_id': self.order_id.id}).sudo()._create_payment_transaction()
            self.transaction_id = transactions.id
            transactions.sudo().write({
                'payment_id': payment.id,
                'sale_order_ids': [self.wizard_process_id.order_id.id],
                'invoice_ids': False,
                'reference': self.wizard_process_id.order_id.name, 
                'transaction_type': self.wizard_process_id.transaction_type,
            })
            resp = transactions.with_context({'pre_auth_order': True}).sudo()._send_payment_request()
            transactions.update({'invoice_ids': False}),
            self.wizard_process_id.order_id.transaction_ids = [transactions.id]
            payment.payment_transaction_id = transactions.id
            payment.ref = transactions.reference or self.wizard_process_id.memo
            if not self.wizard_process_id.card_save and not self.wizard_process_id.card_id:
                self.wizard_process_id.payment_token_id.delete_payment_method()
                self.wizard_process_id.partner_id.refresh_payment_methods()

            context = dict()
            eligible = False
            if payment.payment_transaction_id.is_pay_method_eligible and payment.payment_transaction_id.is_zip_code_allowed:
                eligible = True
            context['message'] = 'Transaction has been successfully processed!'
            context['default_is_ach'] = False if self.wizard_process_id.token_type == 'credit' else True
            context['default_is_surcharge'] = True if self.order_id.partner_id.ebiz_profile_id.is_surcharge_enabled else False
            context['default_is_eligible'] = eligible
            context['default_surcharge_subtotal'] = payment.amount
            context['default_surcharge_amount'] = payment.payment_transaction_id.surcharge_amt
            context['default_surcharge_percentage'] = payment.payment_transaction_id.surcharge_percent
            context['default_surcharge_total'] = payment.amount + float(
                payment.payment_transaction_id.surcharge_amt)
            context['default_currency_id'] = self.env.company.currency_id.id
            context['default_partner_id'] = payment.payment_transaction_id.token_id.partner_id.name if payment.payment_transaction_id.token_id else payment.partner_id.name
            context['default_transaction_type'] = 'Auth Only' if payment.payment_transaction_id.transaction_type=='pre_auth' else 'Sale'
            context['default_surcharge_percent'] = str(payment.payment_transaction_id.surcharge_percent) +' %'
            context['default_currency_id'] = payment.payment_transaction_id.currency_id.id
            context['default_document_number'] = payment.payment_transaction_id.reference
            context['default_reference_number'] = payment.payment_transaction_id.provider_reference
            context['default_auth_code'] = payment.payment_transaction_id.ebiz_auth_code
            display_name = payment.payment_transaction_id.token_id.get_encrypted_name() if payment.payment_transaction_id.token_id else payment.partner_id.name
            context['default_payment_method'] = display_name
            context['default_date_paid'] = payment.payment_transaction_id.last_state_change
            context['default_subtotal'] = payment.payment_transaction_id.amount
            context['default_avs_street'] = payment.ebiz_avs_street if payment.ebiz_avs_street else payment.payment_transaction_id.ebiz_avs_street
            context['default_avs_zip_code'] = payment.ebiz_avs_zip if payment.ebiz_avs_zip else payment.payment_transaction_id.ebiz_avs_zip_code
            context['default_cvv'] = payment.payment_transaction_id.ebiz_cvv_resp
            return self.message_wizard(context)
        else:
            transactions = self.transaction_id
            self.transaction_id.sudo()._set_authorized()
            self.wizard_process_id.order_id.transaction_ids = [transactions.id]
            self.transaction_id.payment_id.payment_transaction_id = transactions.id
            self.transaction_id.payment_id.ref = transactions.reference or self.wizard_process_id.memo
            if not self.wizard_process_id.card_save and not self.wizard_process_id.card_id:
                self.wizard_process_id.payment_token_id.delete_payment_method()
                self.wizard_process_id.partner_id.refresh_payment_methods()
            context = dict()
            eligible = False
            if self.transaction_id.is_pay_method_eligible and self.transaction_id.is_zip_code_allowed:
                eligible = True
            context['message'] = 'Transaction has been successfully processed!'
            context['default_is_ach'] = False if self.wizard_process_id.token_type == 'credit' else True
            context['default_is_surcharge'] = True if self.order_id.partner_id.ebiz_profile_id.is_surcharge_enabled else False
            context['default_is_eligible'] = eligible
            context['default_surcharge_subtotal'] = self.transaction_id.payment_id.amount
            context['default_surcharge_amount'] = self.transaction_id.payment_id.payment_transaction_id.surcharge_amt
            context['default_surcharge_percentage'] = self.transaction_id.surcharge_percent
            context['default_surcharge_total'] = self.transaction_id.payment_id.amount + float(
                self.transaction_id.surcharge_amt)
            context['default_currency_id'] = self.env.company.currency_id.id
            
            context['default_partner_id'] = self.transaction_id.token_id.partner_id.name if self.transaction_id.token_id else self.transaction_id.partner_id.name
            context['default_transaction_type'] = 'Auth Only' if self.transaction_id.transaction_type=='pre_auth' else 'Sale'
            context['default_surcharge_percent'] = str(self.transaction_id.surcharge_percent) +' %'
            context['default_currency_id'] = self.transaction_id.currency_id.id
            context['default_document_number'] = self.transaction_id.reference
            context['default_reference_number'] = self.transaction_id.provider_reference
            context['default_auth_code'] = self.transaction_id.ebiz_auth_code
            display_name = self.transaction_id.token_id.get_encrypted_name() if self.transaction_id.token_id else self.transaction_id.partner_id.name
            context['default_payment_method'] = display_name
            context['default_date_paid'] = self.transaction_id.last_state_change
            context['default_subtotal'] = self.transaction_id.amount
            context['default_avs_street'] = self.transaction_id.payment_id.ebiz_avs_street if self.transaction_id.payment_id.ebiz_avs_street else self.transaction_id.ebiz_avs_street
            context['default_avs_zip_code'] = self.transaction_id.payment_id.ebiz_avs_zip if self.transaction_id.payment_id.ebiz_avs_zip else self.transaction_id.ebiz_avs_zip_code
            context['default_cvv'] = self.transaction_id.ebiz_cvv_resp
            return self.message_wizard(context)

    def show_void_wizard(self):
        return {
            'name': _('Register Payment'),
            'res_model': 'custom.register.payment',
            'res_id': self.wizard_process_id.id,
            'view_mode': 'form',
            'view_id': self.env.ref('payment_ebizcharge_crm.view_custom_register_payment').id,
            'target': 'new',
            'type': 'ir.actions.act_window',
        }

    def create_credit_card_payment_method(self):
        if not self.order_id.partner_id.ebiz_internal_id:
            self.order_id.partner_id.sync_to_ebiz()
        method = self.env.ref('payment_ebizcharge_crm.payment_method_ebizcharge').id
        params = {
            "payment_details": self.wizard_process_id.card_card_number,
            "payment_method_id": method,
            "account_holder_name": self.wizard_process_id.card_account_holder_name,
            "card_number": self.wizard_process_id.card_card_number,
            "card_exp_year": self.wizard_process_id.card_exp_year,
            "card_exp_month": self.wizard_process_id.card_exp_month,
            "avs_street": self.wizard_process_id.card_avs_street,
            "avs_zip": self.wizard_process_id.card_avs_zip,
            "card_code": self.wizard_process_id.card_card_code,
            "partner_id": self.wizard_process_id.sub_partner_id.id,
            "provider_ref": "Temp",
            "is_card_save": self.wizard_process_id.card_save,
            "user_id": self.env.user.id,
            "active": True,
            'provider_id': self.env['payment.provider'].search(
                [('company_id', '=', self.order_id.company_id.id), ('code', '=', 'ebizcharge')]).id
        }
        self.wizard_process_id.reset_credit_card_fields()
        token = self.env['payment.token'].create(params)
        token.action_sync_token_to_ebiz()
        return token


    def void_transaction(self):
        self.transaction_id.sudo()._send_void_request()
        if self.transaction_id.state != 'cancel':
            self.transaction_id.sudo()._set_canceled()
        if self.transaction_id.payment_id.state != 'cancel':
            self.transaction_id.payment_id.sudo().action_cancel()
        if not self.wizard_process_id.card_save and not self.wizard_process_id.card_id:
            self.wizard_process_id.payment_token_id.delete_payment_method()
            self.wizard_process_id.partner_id.refresh_payment_methods()

    def message_wizard(self, context):
        """
            Niaz Implementation:
            Generic Function for successful message indication for the user to enhance user experience
            param: Message string will be passed to context
            return: wizard
        """
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

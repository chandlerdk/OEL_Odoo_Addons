from odoo import fields, models,api
import json
from odoo import _
from ..models.ebiz_charge import message_wizard
import ast


class WizardTransactionValidation(models.TransientModel):
    _name = 'wizard.ebiz.transaction.validation'
    _description = "Wizard EBiz Transaction Validation"
    """
    Kuldeep's implementation
    Wizard for showing avs validation response
    """
    transaction_id = fields.Many2one('payment.transaction')
    wizard_process_id = fields.Many2one('wizard.order.process.transaction')
    transaction_result = fields.Html('Html field')
    address = fields.Char('Address', default="Match")
    zip_code = fields.Char('Zip/Postal Code', default="Match")
    card_code = fields.Char('CVV2/CVC', default="Match")
    check_avs_match = fields.Boolean()
    is_card_denied = fields.Boolean("Is Card Denied")
    denied_message = fields.Char("Denied Message")
    payment_id = fields.Many2one("account.payment")
    full_amount_avs = fields.Boolean("Full Amount AVS")    

    def void_transaction(self):
        """
        Kuldeep's implementation
        return void Transaction Wizard
        """
        if self.payment_id and self.payment_id.move_id:
            receipts_exists = self.env['account.move.receipts'].search([])
            if receipts_exists:
                if int(self.env['account.move.receipts'].search([])[-1].invoice_id) in self.env['account.move'].search([('name', '=', self.payment_id.ref)]).ids:
                    receipts_exists[-1].unlink()
            if self.transaction_id:
                if 'payment_data' in self._context:
                    if not self._context['payment_data']['card_save'] or not self._context['payment_data']['ach_save']:
                        self.transaction_id.token_id.delete_payment_method()
                        self.transaction_id.partner_id.refresh_payment_methods()
                return self.transaction_id.sudo()._send_void_request()

        if self.payment_id:
            if self.env.context.get('my_full_amount'):
                token_id = self.payment_id.create_credit_card_payment_method().id
                self.payment_id.payment_token_id = token_id
            if 'payment_data' in self._context:
                if not self._context['payment_data']['card_save'] or not self._context['payment_data']['ach_save']:
                    if 'customer_token_to_dell' in self._context and 'payment_method_id_to_dell' in self._context:
                        token_to_delete = self.env['payment.token'].search([('ebizcharge_profile', '=', self.env.context.get('payment_method_id_to_dell')),('partner_id', '=', self.payment_id.partner_id.id)])
                        if token_to_delete:
                            token_to_delete.delete_payment_method()
                            token_to_delete.partner_id.refresh_payment_methods()
            return self.payment_id.sudo().action_cancel()
        return True

    def proceed_with_transaction(self):
        """
        Kuldeeps implementation
        Proceed With transaction
        """
        if self.wizard_process_id.card_id:
            return True

        if 'active_id' in self.env.context and not self.env['account.payment.register'].browse(self.env.context['active_id']).card_id and not self.transaction_id:
            token_id = self.payment_id.create_credit_card_payment_method().id
            self.payment_id.payment_token_id = token_id

        if self.payment_id:
            if not self.transaction_id:
                if self.env.context.get('my_full_amount'):
                    self.payment_id.sudo().with_context({'active_model': 'account.move', 'active_id': self.env['account.payment.register'].browse(self.env.context['active_id']).line_ids[0].move_id.id,'avs_bypass': True, 'bypass_card_creation': True, 'payment_data': self._context['payment_data']}).action_post()
                elif self.env.context.get('ebiz_charge_profile'):
                    self.payment_id.sudo().with_context({'active_model': 'account.move', 'active_id': self.env['account.payment.register'].browse(self.env.context['active_id']).line_ids[0].move_id.id, 'avs_bypass': True, 'get_customer_profile': self.env.context.get('ebiz_charge_profile'), 'payment_data': self._context['payment_data']}).action_post()
                else:
                    self.payment_id.sudo().with_context({'active_model': 'account.move', 'active_id': self.env['account.payment.register'].browse(self.env.context['active_id']).line_ids[0].move_id.id,'avs_bypass': True, 'payment_data': self._context['payment_data']}).action_post()
                if not self.payment_id.is_reconciled and 'payment_data' in self._context:
                    if 'to_reconcile' in self._context['payment_data']:
                        self.payment_id.ebiz_reconcile_payment(source=self._context['payment_data'])

                context = dict()
                for inv in self.payment_id.payment_transaction_id.invoice_ids:
                    inv.sync_to_ebiz()
                invoice_ids = self.payment_id.payment_transaction_id.invoice_ids
                if self.payment_id.partner_id.ebiz_profile_id.is_surcharge_enabled and any(inv.move_type == 'out_invoice' for inv in invoice_ids):
                    eligible = False
                    if self.payment_id.payment_transaction_id.is_pay_method_eligible and self.payment_id.payment_transaction_id.is_zip_code_allowed:
                        eligible = True
                    context['default_is_surcharge'] = True
                    context['default_is_eligible'] = eligible
                    context['default_surcharge_subtotal'] = self.payment_id.amount
                    context['default_surcharge_amount'] = self.payment_id.payment_transaction_id.surcharge_amt
                    context['default_surcharge_percentage'] = self.payment_id.payment_transaction_id.surcharge_percent
                    context['default_surcharge_total'] = self.payment_id.amount + float(
                        self.payment_id.payment_transaction_id.surcharge_amt)
                    context['default_currency_id'] = self.env.company.currency_id.id
                else:
                    context['default_surcharge_total'] = self.payment_id.payment_transaction_id.amount

                context['message'] = 'Transaction has been successfully processed!'
                context['default_is_ach'] = False if self.env['account.payment.register'].browse(self.env.context['active_id']).token_type == 'credit' else True
                context['default_currency_id'] = self.env.company.currency_id.id
                context['default_partner_id'] = self.payment_id.payment_transaction_id.token_id.partner_id.name if self.payment_id.payment_transaction_id.token_id else self.payment_id.partner_id.name
                context['default_transaction_type'] = 'Auth Only' if self.payment_id.payment_transaction_id.transaction_type=='pre_auth' else 'Sale'
                context['default_surcharge_percent'] = str(self.payment_id.payment_transaction_id.surcharge_percent) +' %'
                context['default_document_number'] = self.payment_id.payment_transaction_id.reference
                context['default_reference_number'] = self.payment_id.payment_transaction_id.provider_reference
                context['default_auth_code'] = self.payment_id.payment_transaction_id.ebiz_auth_code
                display_name = self.payment_id.payment_transaction_id.token_id.get_encrypted_name() if self.payment_id.payment_transaction_id.token_id else self.payment_id.partner_id.name
                context['default_payment_method'] = display_name
                context['default_date_paid'] = self.payment_id.payment_transaction_id.last_state_change
                context['default_subtotal'] = self.payment_id.payment_transaction_id.amount
                context['default_avs_street'] = self.payment_id.ebiz_avs_street
                context['default_avs_zip_code'] = self.payment_id.ebiz_avs_zip
                context['default_cvv'] = self.payment_id.payment_transaction_id.ebiz_cvv_resp
                return self.message_wizard(context)

            # if we ran transactoin with deposite command we need to set the state to done to bypass capture
            if self.payment_id.transaction_command == "Sale":
                self.payment_id.sudo().with_context({'avs_bypass': True, 'payment_data': self._context['payment_data']}).action_post()
                self.transaction_id.sudo()._set_done()
                context = dict()
                for inv in self.payment_id.payment_transaction_id.invoice_ids:
                    inv.sync_to_ebiz()
                if 'payment_data' in self._context:
                    if not self._context['payment_data']['card_save'] or not self._context['payment_data']['ach_save']:
                        if 'customer_token_to_dell' in self._context and 'payment_method_id_to_dell' in self._context:
                            token_to_delete = self.env['payment.token'].search([('ebizcharge_profile', '=', self.env.context.get('payment_method_id_to_dell')),('partner_id', '=', self.payment_id.partner_id.id)])
                            if token_to_delete:
                                token_to_delete.delete_payment_method()
                                token_to_delete.partner_id.refresh_payment_methods()

                invoice_ids = self.payment_id.payment_transaction_id.invoice_ids
                if self.payment_id.partner_id.ebiz_profile_id.is_surcharge_enabled and any(
                        inv.move_type == 'out_invoice' for inv in invoice_ids):
                    eligible = False
                    if self.payment_id.payment_transaction_id.is_pay_method_eligible and self.payment_id.payment_transaction_id.is_zip_code_allowed:
                        eligible = True
                    context['default_is_surcharge'] = True
                    context['default_is_eligible'] = eligible
                    context['default_surcharge_subtotal'] = self.payment_id.amount
                    context['default_surcharge_amount'] = self.payment_id.payment_transaction_id.surcharge_amt
                    context['default_surcharge_percentage'] = self.payment_id.payment_transaction_id.surcharge_percent
                    context['default_surcharge_total'] = self.payment_id.amount + float(
                        self.payment_id.payment_transaction_id.surcharge_amt)
                    context['default_currency_id'] = self.env.company.currency_id.id
                else:
                    context['default_surcharge_total'] = self.payment_id.payment_transaction_id.amount

                context['message'] = 'Transaction has been successfully processed!'
                context['default_is_ach'] = False if self.env['account.payment.register'].browse(self.env.context['active_id']).token_type == 'credit' else True
                context['default_currency_id'] = self.env.company.currency_id.id
                context['default_partner_id'] = self.payment_id.payment_transaction_id.token_id.partner_id.name if self.payment_id.payment_transaction_id.token_id else self.payment_id.partner_id.name
                context['default_transaction_type'] = 'Auth Only' if self.payment_id.payment_transaction_id.transaction_type=='pre_auth' else 'Sale'
                context['default_surcharge_percent'] = str(self.payment_id.payment_transaction_id.surcharge_percent) +' %'
                context['default_document_number'] = self.payment_id.payment_transaction_id.reference
                context['default_reference_number'] = self.payment_id.payment_transaction_id.provider_reference
                context['default_auth_code'] = self.payment_id.payment_transaction_id.ebiz_auth_code
                display_name = self.payment_id.payment_transaction_id.token_id.get_encrypted_name() if self.payment_id.payment_transaction_id.token_id else self.payment_id.partner_id.name
                context['default_payment_method'] = display_name
                context['default_date_paid'] = self.payment_id.payment_transaction_id.last_state_change
                context['default_subtotal'] = self.payment_id.payment_transaction_id.amount
                context['default_avs_street'] = self.payment_id.ebiz_avs_street
                context['default_avs_zip_code'] = self.payment_id.ebiz_avs_zip
                context['default_cvv'] = self.payment_id.payment_transaction_id.ebiz_cvv_resp
                return self.message_wizard(context)

        else:
            self.wizard_process_id.process_new_card_transaction()
            return message_wizard('Successful!')


    def get_surcharge_info(self):
        context = dict()
        eligible = False
        if self.payment_id.payment_transaction_id.is_pay_method_eligible and self.payment_id.payment_transaction_id.is_zip_code_allowed:
            eligible = True
        context['message'] = 'Transaction has been successfully processed!'
        context['default_is_ach'] = False if self.env['account.payment.register'].browse(
            self.env.context['active_id']).token_type == 'credit' else True
        context[
            'default_is_surcharge'] = True if self.payment_id.partner_id.ebiz_profile_id.is_surcharge_enabled else False
        context['default_is_eligible'] = eligible
        context['default_surcharge_subtotal'] = self.payment_id.amount
        context['default_surcharge_amount'] = self.payment_id.payment_transaction_id.surcharge_amt
        context['default_surcharge_percentage'] = self.payment_id.payment_transaction_id.surcharge_percent
        context['default_surcharge_total'] = self.payment_id.amount + float(
            self.payment_id.payment_transaction_id.surcharge_amt)
        context['default_currency_id'] = self.env.company.currency_id.id
        return context


    def update_and_retry(self):
        """
        Kuldeep's implementation
        Proceed With transaction
        """
        if self.wizard_process_id:
            self.wizard_process_id.write({
                "card_id": None,
                "card_card_number": "",
                "card_exp_year": "",
                "card_exp_month": "",
                "card_card_code": "",
                })
            action = self.env.ref('payment_ebizcharge_crm.action_process_ebiz_transaction').read()[0]
            action['res_id'] = self.wizard_process_id.id
            return action
        else:
            self.payment_id.write({
                "card_id": None,
                "card_card_number": "",
                "card_exp_year": "",
                "card_exp_month": "",
                "card_card_code": "",
                })
            context = dict(self.env.context)
            context['active_model'] = 'account.move'
            if self.payment_id:
                self.payment_id.action_cancel()
            return {
                'name': _('Register Payment'),
                'res_model': 'account.payment.register',
                'res_id': context['active_id'],
                'view_mode': 'form',
                'view_id': self.env.ref('account.view_account_payment_register_form').id,
                'context': context,
                'target': 'new',
                'type': 'ir.actions.act_window',
            }

    def show_void_wizard(self):
        """
        Kuldeep's implementation
        function for void transaction wizard
        """
        wiz = self.env['wizard.ebiz.transaction.void'].create({
            'transaction_id': self.transaction_id.id,
            'wizard_process_id': self.wizard_process_id.id})
        action = self.env.ref('payment_ebizcharge_crm.action_ebiz_transaction_void_form').read()[0]
        action['res_id'] = wiz.id
        return action

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

from odoo import fields, models, api, _
from datetime import datetime, timedelta
from ..models.ebiz_charge import message_wizard


class MessageWizard(models.TransientModel):
    _name = 'message.wizard'
    _description = "Message Wizard"

    def get_default(self):
        if self.env.context.get("message", False):
            return self.env.context.get("message")
        return False

    text = fields.Text('Message', readonly=True, default=get_default)
    transaction_id = fields.Many2one('emv.device.transaction', string='Transaction')
    is_eligible = fields.Boolean()
    is_surcharge = fields.Boolean()
    surcharge_subtotal = fields.Float()
    surcharge_percentage = fields.Float()
    surcharge_amount = fields.Monetary()

    surcharge_total = fields.Monetary()

    partner_id = fields.Char(string='Partner')
    transaction_type = fields.Char(string='Transaction Type')
    document_number = fields.Char(string='Document Number')
    reference_number = fields.Char(string='Reference Number')
    auth_code = fields.Char(string='Auth Code')
    payment_method = fields.Char(string='Payment Method')
    date_paid = fields.Datetime(string='Date &amp; Time Paid')
    subtotal = fields.Monetary(string='Subtotal')
    surcharge_percent = fields.Char(string='Surcharge %')
    surcharge_amount = fields.Monetary(string='Surcharge Amount')
    avs_street = fields.Char(string='AVS Street')
    avs_zip_code = fields.Char(string='AVS Zip / Postal Code')
    cvv = fields.Char(string='CVV')

    currency_id = fields.Many2one('res.currency')
    is_ach = fields.Boolean()
    
    def action_confirm(self):
        if self.transaction_id:
            self.transaction_id.action_check(trans=self.transaction_id.id)
        else:
            pass


class SuccessPaymentMethods(models.TransientModel):
    _name = 'success.payment.methods'
    _description = "Success Payment Methods"

    def get_default(self):
        if self.env.context.get("message", False):
            return self.env.context.get("message")
        return False

    text = fields.Text('Message', readonly=True, default=get_default)
    wizard_process_id = fields.Many2one('wizard.order.process.transaction')

    def open_register_wizard(self):
        context = dict(self.env.context)
        if 'move_context' in self._context:
            context = dict(self._context['move_context'])
        context['active_model'] = 'account.move'
        return {
            'name': 'Register Payment',
            'view_type': 'form',
            'view_mode': 'form',
            'view_id': self.env.ref('account.view_account_payment_register_form').id,
            'res_model': 'account.payment.register',
            'type': 'ir.actions.act_window',
            'target': 'new',
            'res_id': self.env.context['active_id'],
            'context': context
        }


class WizardDeleteToken(models.TransientModel):
    _name = 'wizard.token.delete.confirmation'
    _description = "Wizard Token Delete Confirmation"

    record_id = fields.Integer('Record Id')
    record_model = fields.Char('Record Model')
    text = fields.Text('Message', readonly=True)

    def delete_record(self):
        self.env[self.record_model].browse(self.record_id).token_action_archive()
        return message_wizard('The payment method has been deleted successfully!')


class WizardDeleteEmailPay(models.TransientModel):
    _name = 'wizard.delete.email.pay'
    _description = "Wizard Delete Email Pay"

    record_id = fields.Integer('Record Id')
    record_model = fields.Char('Record Model')
    text = fields.Text('Message', readonly=True)

    def delete_record(self):
        values = self.env.context.get('kwargs_values')
        pending_received_msg = self.env.context.get('pending_received')
        success = 0
        for invoice in values:
            odoo_invoice = self.env['account.move'].search([('id', '=', invoice['invoice_id'])])
            instance = None
            if odoo_invoice.partner_id.ebiz_profile_id:
                instance = odoo_invoice.partner_id.ebiz_profile_id

            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            if pending_received_msg == 'Pending Requests':
                form_url = ebiz.client.service.DeleteEbizWebFormPayment(**{
                    'securityToken': ebiz._generate_security_json(),
                    'paymentInternalId': odoo_invoice.payment_internal_id,
                })
                if form_url.Status == 'Success':
                    odoo_invoice.ebiz_invoice_status = 'delete'
                    success += 1
                    pending_payments = self.env['payment.request.bulk.email'].search([])
                    for payment in pending_payments:
                        if payment.transaction_history_line_pending:
                            for pending in payment.transaction_history_line_pending:
                                if pending.id == invoice['invoice_id']:
                                    payment.transaction_history_line_pending = [[2, invoice['invoice_id']]]
                                    dict2 = {
                                        'name': invoice['name'],
                                        'customer_name': odoo_invoice.partner_id.id,
                                        'customer_id': str(odoo_invoice.partner_id.id),
                                        'email_id': odoo_invoice.partner_id.email,
                                        'invoice_id': odoo_invoice.id,
                                        'invoice_date': odoo_invoice.date,
                                        'sales_person': self.env.user.id,
                                        'amount': odoo_invoice.amount_total,
                                        "currency_id": odoo_invoice.currency_id.id,
                                        'amount_due': odoo_invoice.amount_residual_signed,
                                        'tax': odoo_invoice.amount_untaxed_signed,
                                        'invoice_due_date': odoo_invoice.invoice_date_due,
                                        'sync_transaction_id': payment.id,
                                    }
                                    new_sync_invoice = self.env['sync.request.payments.bulk'].create(dict2)
                                    payment.transaction_history_line = [[4, new_sync_invoice.id]]

            elif pending_received_msg == 'Received Email Payments':
                form_url = ebiz.client.service.MarkEbizWebFormPaymentAsApplied(**{
                    'securityToken': ebiz._generate_security_json(),
                    'paymentInternalId': odoo_invoice.payment_internal_id,
                })

                if form_url.Status == 'Success':
                    odoo_invoice.email_received_payments = False
                    odoo_invoice.ebiz_invoice_status = False
                    success += 1
                    received_payments = self.env['payment.request.bulk.email'].search([])
                    for payment in received_payments:
                        if payment.transaction_history_line_received:
                            for pending in payment.transaction_history_line_received:
                                if pending.id == invoice['invoice_id']:
                                    payment.transaction_history_line_received = [[2, invoice['invoice_id']]]
                                    dict2 = {
                                        'name': invoice['name'],
                                        'customer_name': odoo_invoice.partner_id.id,
                                        'customer_id': str(odoo_invoice.partner_id.id),
                                        'email_id': odoo_invoice.partner_id.email,
                                        'invoice_id': odoo_invoice.id,
                                        'invoice_date': odoo_invoice.date,
                                        'sales_person': self.env.user.id,
                                        'amount': odoo_invoice.amount_total,
                                        "currency_id": odoo_invoice.currency_id.id,
                                        'amount_due': odoo_invoice.amount_residual_signed,
                                        'tax': odoo_invoice.amount_untaxed_signed,
                                        'invoice_due_date': odoo_invoice.invoice_date_due,
                                        'sync_transaction_id': payment.id,
                                    }
                                    new_sync_invoice = self.env['sync.request.payments.bulk'].create(dict2)
                                    payment.transaction_history_line = [[4, new_sync_invoice.id]]

            odoo_invoice.save_payment_link = False
            odoo_invoice.request_amount = 0
            odoo_invoice.last_request_amount = 0
        rec = self.env['payment.request.bulk.email'].browse([self.record_id]).exists()
        if rec:
            rec.search_transaction()
        if pending_received_msg == 'Pending Requests':
            return message_wizard(f'{success} request(s)  were successfully removed from {pending_received_msg}!')
        elif pending_received_msg == 'Received Email Payments':
            return message_wizard(f'{success} payment(s)  were successfully removed from {pending_received_msg}!')


class WizardDeletePaymentMethods(models.TransientModel):
    _name = 'wizard.delete.payment.methods'
    _description = "Wizard Delete Payment Methods"

    record_id = fields.Integer('Record Id')
    record_model = fields.Char('Record Model')
    text = fields.Text('Message', readonly=True)

    def delete_record(self):
        values = self.env.context.get('kwargs_values')
        pending_received_msg = self.env.context.get('pending_received')
        success = 0
        for record in values:
            partner = self.env['res.partner'].browse([int(record.get('customer_id'))])
            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=partner.ebiz_profile_id)
            if pending_received_msg == 'Pending Requests':
                form_url = ebiz.client.service.DeleteEbizWebFormPayment(**{
                    'securityToken': ebiz._generate_security_json(),
                    'paymentInternalId': record['payment_internal_id'],
                })
                success += 1
                pending_methods = self.env['payment.method.ui'].search([])
                for method in pending_methods:
                    if method.transaction_history_line_pending:
                        for pending in method.transaction_history_line_pending:
                            try:
                                if pending.id == record['id']:
                                    method.transaction_history_line_pending = [[2, record['id']]]
                            except Exception:
                                pass
            elif pending_received_msg == 'Added Payment Methods':
                form_url = ebiz.client.service.MarkEbizWebFormPaymentAsApplied(**{
                    'securityToken': ebiz._generate_security_json(),
                    'paymentInternalId': record['payment_internal_id'],
                })
                success += 1
                received_methods = self.env['payment.method.ui'].search([])
                for method in received_methods:
                    if method.transaction_history_line_received:
                        for pending in method.transaction_history_line_received:
                            if pending.id == record['id']:
                                method.transaction_history_line_received = [[2, record['id']]]
        if pending_received_msg == 'Pending Requests':
            return message_wizard(f'{success} request(s) were successfully removed from Pending Requests!')
        elif pending_received_msg == 'Added Payment Methods':
            return message_wizard(
                f'{success} payment method(s) were successfully removed from Added Payment Methods!')


class WizardDeleteDownloadLogs(models.TransientModel):
    _name = 'wizard.delete.logs.download'
    _description = "Wizard Delete Logs Download"

    record_id = fields.Integer('Record Id')
    record_model = fields.Char('Record Model')
    text = fields.Text('Message', readonly=True)

    def delete_record(self):
        values = self.env.context.get('kwargs_values')
        success = 0
        for record in values:
            record_check = self.env['sync.logs'].search(
                [('invoice_number', '=', record['invoice_number']), ('ref_num', '=', record['ref_num'])])
            if record_check:
                record_check.unlink()
                success += 1
        else:
            return message_wizard(f'{success} payment(s) were successfully cleared from the Log!')


class WizardDeleteUploadLogs(models.TransientModel):
    _name = 'wizard.delete.upload.logs'
    _description = "Wizard Delete Upload Logs"

    record_id = fields.Integer('Record Id')
    record_model = fields.Char('Record Model')
    text = fields.Text('Message', readonly=True)

    def delete_record(self):
        values = self.env.context.get('list_of_records')
        model_type = self.env.context.get('model')
        success = 0

        for record in values:
            record_to_dell = self.env[model_type].search([('id', '=', record)])
            if record_to_dell:
                record_to_dell.unlink()
                success += 1
        else:
            return message_wizard(f'{success} {self.record_model}(s) were successfully cleared from the Log!')


class WizardDeleteInactiveCustomer(models.TransientModel):
    _name = 'wizard.inactive.customers'
    _description = "Wizard Inactive Customers"

    record_id = fields.Integer('Record Id')
    record_model = fields.Char('Record Model')
    text = fields.Text('Message', readonly=True)

    def delete_record(self):
        values = self.env.context.get('kwargs_values')
        success = 0
        for record in values:
            ebiz_customer = self.env['res.partner'].search([('id', '=', record)])
            if ebiz_customer:
                instance = None
                if ebiz_customer.ebiz_profile_id:
                    instance = ebiz_customer.ebiz_profile_id
                ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
                templates = ebiz.client.service.MarkCustomerAsInactive(**{
                    'securityToken': ebiz._generate_security_json(),
                    'customerInternalId': ebiz_customer.ebiz_internal_id,
                })
                ebiz_customer.active = False
                success += 1

        else:
            return message_wizard(f'{success} customer(s) were successfully deactivated in Odoo and EBizCharge Hub!')


class WizardReceivedEmailPay(models.TransientModel):
    _name = 'wizard.receive.email.pay'
    _description = "Wizard Receive Email Pay"

    record_id = fields.Integer('Record Id')
    odoo_invoice = fields.Many2one('account.move', 'Odoo Invoice')
    text = fields.Text('Message', readonly=True)

    def apply_record(self):
        import ast
        self.odoo_invoice.received_apply_email_after_confirmation(
            ast.literal_eval(f"{self.env.context.get('invoice')}"))


class WizardReceivedEmailPayPaymentLink(models.TransientModel):
    _name = 'wizard.receive.email.payment.link'
    _description = "Wizard Receive Email Payment Link"

    record_id = fields.Integer('Record Id')
    odoo_invoice = fields.Many2one('account.move', 'Odoo Invoice')
    text = fields.Text('Message', readonly=True)
    order_id = fields.Many2one('sale.order', 'Order')
    is_pay_link = fields.Boolean(string='Pay Link')
    invoice_ids = fields.Many2many('account.move', string='Invoices')
    sale_ids = fields.Many2many('sale.order', string='Orders')

    def send_email(self):
        if self.order_id:
            record = self.order_id
        else:
            record = self.env['account.move'].search([('id', '=', self.record_id)])
        instance = None
        if record.partner_id.ebiz_profile_id:
            instance = record.partner_id.ebiz_profile_id
        if record.save_payment_link:
            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            ebiz.client.service.DeleteEbizWebFormPayment(**{
                'securityToken': ebiz._generate_security_json(),
                'paymentInternalId': record.payment_internal_id,
            })
            if record and  record.save_payment_link and not record.is_email_request:
                message_log = 'EBizCharge Payment Link invalidated: '+str(record.save_payment_link)
                record.message_post(body=message_log)
            record.save_payment_link = False
        if 'from_payment_link' in self.env.context:
            instance = False
            is_profile = False
            allow_credit_card_pay = False
            merchant_data = False
            if record.partner_id.ebiz_profile_id:
                allow_credit_card_pay = record.partner_id.ebiz_profile_id.enable_cvv
                merchant_data = record.partner_id.ebiz_profile_id.merchant_data
                instance = record.partner_id.ebiz_profile_id
                is_profile = True
            return {
                'name': 'Register Payment',
                'view_type': 'form',
                'view_mode': 'form',
                'res_model': 'custom.register.payment',
                'view_id': False,
                'type': 'ir.actions.act_window',
                'target': 'new',
                'context': {
                    'default_amount': record.ebiz_amount_residual,
                    'default_date': datetime.now().date(),
                    'default_ebiz_receipt_emails': record.partner_id.email,
                    'default_order_id': record.id,
                    'default_memo': record.name,
                    'partner_id': record.partner_id.id,
                    'sub_partner_id': record.partner_id.id,
                    'default_card_functionality_hide': allow_credit_card_pay,
                    'default_ach_functionality_hide': merchant_data,
                    'default_is_ebiz_profile': is_profile,
                    'default_ebiz_profile_id': instance.id,
                    'default_required_security_code': instance.verify_card_before_saving,
                }
            }
        elif 'batch_processing' in self.env.context:
            rec = self.env.ref('payment_ebizcharge_crm.my_record_78')
            return rec.with_context(for_batch_processing=True).process_invoices(self.env.context.get('kwargs'))
        elif self.is_pay_link and self.invoice_ids:
            payment_lines = []
            for inv in self.invoice_ids:
                if inv and inv.payment_state not in ("paid", "in_payment"):
                    payment_line = {
                        "invoice_id": int(inv.id),
                        "name": inv.name,
                        "customer_name": inv.partner_id.id,
                        "amount_due": inv.amount_residual_signed,
                        "amount_residual_signed": inv.amount_residual_signed,
                        "amount_total_signed": inv.amount_total,
                        "request_amount": inv.amount_residual_signed,
                        "odoo_payment_link": inv.odoo_payment_link,
                        "currency_id": self.env.user.currency_id.id,
                        "email_id": inv.partner_id.email,
                        "ebiz_profile_id": inv.partner_id.ebiz_profile_id.id,
                    }
                    payment_lines.append([0, 0, payment_line])
                profile = inv.partner_id.ebiz_profile_id.id
            wiz = self.env['wizard.ebiz.generate.link.payment.bulk'].with_context(
                profile=profile).create(
                {'payment_lines': payment_lines,
                 'invoice_link': True,
                 'ebiz_profile_id': profile})
            action = self.env.ref('payment_ebizcharge_crm.wizard_generate_link_form_views_action').read()[0]
            action['res_id'] = wiz.id
            action['context'] = self.env.context
            return action
        elif self.sale_ids:
            payment_lines = []
            profile = False
            for order in self.sale_ids:
                if order.save_payment_link:
                    ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=order.partner_id.ebiz_profile_id)
                    ebiz.client.service.DeleteEbizWebFormPayment(**{
                        'securityToken': ebiz._generate_security_json(),
                        'paymentInternalId': order.payment_internal_id,
                    })
                    if order and order.save_payment_link and not order.is_email_request:
                        message_log = 'EBizCharge Payment Link invalidated: ' + str(order.save_payment_link)
                        order.message_post(body=message_log)
                    order.save_payment_link = False
                    order.request_amount = False
                search_so = self.env['sale.order'].search([('id', '=', order.id)], limit=1)
                if search_so:
                    if not search_so.save_payment_link:
                        payment_line = {
                            "order_id": int(search_so.id),
                            "partner_id": search_so.partner_id.id,
                            "transaction_type": search_so.partner_id.ebiz_profile_id.gpl_pay_sale,
                            "amount_total_signed": search_so.amount_total,
                            "request_amount": search_so.ebiz_order_amount_residual,
                            "so_payment_link": search_so.odoo_payment_link,
                            "currency_id": self.env.user.currency_id.id,
                            "email_id": search_so.partner_id.email,
                            "ebiz_profile_id": search_so.partner_id.ebiz_profile_id.id,
                        }
                        payment_lines.append([0, 0, payment_line])
                    profile = search_so.partner_id.ebiz_profile_id.id
            wiz = self.env['wizard.generate.so.link.payment'].with_context(
                profile=profile).create(
                {'payment_lines': payment_lines,
                 'sale_link': True,
                 'ebiz_profile_id': profile})
            action = \
                self.env.ref('payment_ebizcharge_crm.wizard_generate_so_link_form_views_action').read()[0]
            action['res_id'] = wiz.id
            action['context'] = self.env.context
            return action

        elif self.odoo_invoice and 'email_pay' not in self.env.context:
            self.odoo_invoice.request_amount -= self.odoo_invoice.last_request_amount
            return {'type': 'ir.actions.act_window',
                    'name': _('Generate Payment Link'),
                    'res_model': 'ebiz.payment.link.wizard',
                    'target': 'new',
                    'view_mode': 'form',
                    'view_type': 'form',
                    'context': {
                        'default_ebiz_profile_id': record.partner_id.ebiz_profile_id.id,
                        'active_id': record.id,
                        'active_model': 'account.move',
                    }}

        else:
            if 'email_pay' in self.env.context and self.odoo_invoice:
                self.odoo_invoice.request_amount -= self.odoo_invoice.last_request_amount
            return {'type': 'ir.actions.act_window',
                    'name': _('Email Pay Request'),
                    'res_model': 'email.invoice',
                    'target': 'new',
                    'view_mode': 'form',
                    'view_type': 'form',
                    'context': {
                        'default_contacts_to': [[6, 0, [record.partner_id.id]]],
                        'default_record_id': record.id,
                        'default_partner_ids': [(6,0,  record.partner_id.ids)],
                        'default_ebiz_profile_id': record.partner_id.ebiz_profile_id.id,
                        'default_currency_id': record.currency_id.id,
                        'default_amount': record.amount_residual if record.amount_residual else record.amount_total,
                        'default_model_name': 'account.move',
                        'default_email_customer': str(record.partner_id.email if record.partner_id.email else ''),
                        'selection_check': 1,
                    }}

    # def send_email(self):
    #     if self.order_id:
    #         record = self.order_id
    #     else:
    #         record = self.env['account.move'].search([('id', '=', self.record_id)])
    #     instance = None
    #     if record.partner_id.ebiz_profile_id:
    #         instance = record.partner_id.ebiz_profile_id
    #     if record.save_payment_link:
    #         ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
    #         ebiz.client.service.DeleteEbizWebFormPayment(**{
    #             'securityToken': ebiz._generate_security_json(),
    #             'paymentInternalId': record.payment_internal_id,
    #         })
    #         record.save_payment_link = False
    #     if 'from_payment_link' in self.env.context:
    #         instance = False
    #         is_profile = False
    #         allow_credit_card_pay = False
    #         merchant_data = False
    #         if record.partner_id.ebiz_profile_id:
    #             allow_credit_card_pay = record.partner_id.ebiz_profile_id.enable_cvv
    #             merchant_data = record.partner_id.ebiz_profile_id.merchant_data
    #             instance = record.partner_id.ebiz_profile_id
    #             is_profile = True
    #         return {
    #             'name': 'Register Payment',
    #             'view_type': 'form',
    #             'view_mode': 'form',
    #             'res_model': 'custom.register.payment',
    #             'view_id': False,
    #             'type': 'ir.actions.act_window',
    #             'target': 'new',
    #             'context': {
    #                 'default_amount': record.ebiz_amount_residual,
    #                 'default_date': datetime.now().date(),
    #                 'default_ebiz_receipt_emails': record.partner_id.email,
    #                 'default_order_id': record.id,
    #                 'default_memo': record.name,
    #                 'partner_id': record.partner_id.id,
    #                 'sub_partner_id': record.partner_id.id,
    #                 'default_card_functionality_hide': allow_credit_card_pay,
    #                 'default_ach_functionality_hide': merchant_data,
    #                 'default_is_ebiz_profile': is_profile,
    #                 'default_ebiz_profile_id': instance.id,
    #                 'default_required_security_code': instance.verify_card_before_saving,
    #             }
    #         }

    #     elif self.odoo_invoice:
    #         self.odoo_invoice.request_amount -= self.odoo_invoice.last_request_amount
    #         return {'type': 'ir.actions.act_window',
    #                 'name': _('Generate Payment Link'),
    #                 'res_model': 'ebiz.payment.link.wizard',
    #                 'target': 'new',
    #                 'view_mode': 'form',
    #                 'view_type': 'form',
    #                 'context': {
    #                     'default_ebiz_profile_id': record.partner_id.ebiz_profile_id.id,
    #                     'active_id': record.id,
    #                     'active_model': 'account.move',
    #                 }}
    #     else:
    #         return {'type': 'ir.actions.act_window',
    #                 'name': _('Email Pay Request'),
    #                 'res_model': 'email.invoice',
    #                 'target': 'new',
    #                 'view_mode': 'form',
    #                 'view_type': 'form',
    #                 'context': {
    #                     'default_contacts_to': [[6, 0, [record.partner_id.id]]],
    #                     'default_record_id': record.id,
    #                     'default_ebiz_profile_id': record.partner_id.ebiz_profile_id.id,
    #                     'default_currency_id': record.currency_id.id,
    #                     'default_amount': record.amount_residual if record.amount_residual else record.amount_total,
    #                     'default_model_name': 'account.move',
    #                     'default_email_customer': str(record.partner_id.email if record.partner_id.email else ''),
    #                     'selection_check': 1,
    #                 }}


class WizardCreditNoteValidation(models.TransientModel):
    _name = 'wizard.credit.note.validate'
    _description = "Wizard Credit Note Validate"

    invoice_id = fields.Many2one('account.move')
    text = fields.Text('Message', readonly=True)

    def proceed(self):
        context = dict(self._context)
        context['bypass_credit_note_restriction'] = True
        return self.invoice_id.with_context(context).action_reverse()


class EmailPayMessage(models.TransientModel):
    _name = 'wizard.email.pay.message'
    _description = "Wizard Email Pay Message"

    name = fields.Char("Customer")
    success_count = fields.Integer("Success Count")
    failed_count = fields.Integer("Failed Count")
    total = fields.Integer("Total")
    lines_ids = fields.One2many('wizard.email.pay.message.line', 'message_id')


class EmailPayMessageLine(models.TransientModel):
    _name = "wizard.email.pay.message.line"
    _description = "Wizard Email Pay Message Line"

    message_id = fields.Many2one('wizard.email.pay.message')
    status = fields.Char("Status")
    customer_id = fields.Integer("Customer ID")
    number = fields.Many2one('account.move', "Number")
    customer_name = fields.Many2one('res.partner', string="Customer")


class MultiPaymentMsg(models.TransientModel):
    _name = 'wizard.multi.payment.message'
    _description = "Wizard Multi Payment Message"

    name = fields.Char("Customer")
    success_count = fields.Integer("Success Count")
    failed_count = fields.Integer("Failed Count")
    total = fields.Integer("Total")
    lines_ids = fields.One2many('wizard.multi.payment.message.line', 'message_id')


class MultiPaymentMsgLine(models.TransientModel):
    _name = "wizard.multi.payment.message.line"
    _description = "Wizard Multi Payment Message Line"

    message_id = fields.Many2one('wizard.multi.payment.message')
    status = fields.Char("Status")
    customer_id = fields.Integer("Customer ID")
    email_address = fields.Char("Email")
    customer_name = fields.Many2one('res.partner', string="Customer")


class MultiTransactionsMsg(models.TransientModel):
    _name = 'wizard.transaction.history.message'
    _description = "Wizard Transaction History Message"

    name = fields.Char("Customer")
    success_count = fields.Integer("Success Count")
    failed_count = fields.Integer("Failed Count")
    lines_ids = fields.One2many('wizard.transaction.history.message.line', 'message_id')


class MultiTransactionsLine(models.TransientModel):
    _name = "wizard.transaction.history.message.line"
    _description = "Wizard Transaction History Message Line"

    message_id = fields.Many2one('wizard.transaction.history.message')
    status = fields.Char("Status")
    customer_id = fields.Char("Customer Id")
    ref_num = fields.Char("Reference Number")
    customer_name = fields.Char("Customer")
    type = fields.Char("Type")

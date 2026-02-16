# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError,ValidationError
from datetime import datetime, timedelta
import logging
from .ebiz_charge import message_wizard

_logger = logging.getLogger(__name__)


class AccountMoveInh(models.Model):
    _inherit = 'account.move'
    # _inherit = ['mail.thread']


    def _get_default_ebiz_auto_sync(self):
        ebiz_auto_sync_invoice = False
        if self.partner_id.ebiz_profile_id:
            ebiz_auto_sync_invoice = self.partner_id.ebiz_profile_id.ebiz_auto_sync_invoice
        return ebiz_auto_sync_invoice

    def _get_default_ebiz_auto_sync_credit_note(self):
        ebiz_auto_sync_credit_notes = False
        if self.partner_id.ebiz_profile_id:
            ebiz_auto_sync_credit_notes = self.partner_id.ebiz_profile_id.ebiz_auto_sync_credit_notes
        return ebiz_auto_sync_credit_notes

    def _compute_ebiz_auto_sync(self):
        self.ebiz_auto_sync = False

    def _compute_ebiz_auto_sync_credit_note(self):
        self.ebiz_auto_sync_credit_note = False

    def _compute_receipt_status(self):
        config = self.env['account.move.receipts'].search(
            [('invoice_id', '=', self.id)])
        self.receipt_status = True if config else False

    ebiz_auto_sync = fields.Boolean(compute="_compute_ebiz_auto_sync", default=_get_default_ebiz_auto_sync)
    ebiz_auto_sync_credit_note = fields.Boolean(compute="_compute_ebiz_auto_sync_credit_note",
                                                default=_get_default_ebiz_auto_sync_credit_note)
    ebiz_internal_id = fields.Char(string='EBizCharge Internal Id', copy=False)
    done_transaction_ids = fields.Many2many('payment.transaction', compute='_compute_done_transaction_ids',
                                            string='Done Authorized Transactions', copy=False, readonly=True)
    is_refund_processed = fields.Boolean(default=False)
    is_payment_processed = fields.Boolean(default=False, copy=False)
    payment_internal_id = fields.Char(string='EBizCharge Email Response', copy=False)

    log_status_emv = fields.Char(string="Logs EMV", tracking=True, copy=False)
    emv_transaction_id = fields.Many2one('emv.device.transaction', string='Transaction ID', copy=False)
    ebiz_invoice_status = fields.Selection([
        ('default', ''),
        ('pending', 'Pending'),
        ('received', 'Received'),
        ('partially_received', 'Partially Received'),
        ('delete', 'Deleted'),
        ('applied', 'Applied'),
    ], string='Email Pay Status', default='default', readonly=True, copy=False, index=True)

    receipt_ref_num = fields.Char(string='Receipt RefNum')
    sync_status = fields.Char(string="EBizCharge Upload Status", compute="_compute_sync_status")
    sync_response = fields.Char(string="Sync Status", copy=False)
    last_sync_date = fields.Datetime(string="Upload Date & Time", copy=False)
    receipt_status = fields.Boolean(compute="_compute_receipt_status", default=False)
    credit_note_ids = fields.One2many('account.move', 'reversed_entry_id', string='Credit Notes')

    date_time_sent_for_email = fields.Datetime('Date & Time Sent')
    customer_id = fields.Char(string="Customer ID", compute="_compute_customer_id")
    email = fields.Char(string="Email", compute="_compute_customer_id")
    default_payment_method_name = fields.Char(string="Payment Method Name", compute="_compute_customer_id")
    default_payment_method_id = fields.Integer(string="Default Payment Method", compute="_compute_customer_id")
    email_for_pending = fields.Char(string='Email Pay Pending')
    email_received_payments = fields.Boolean(string='Email Pay Received Payments', default=False, copy=False)
    email_requested_amount = fields.Float(string='Requested Amount')
    no_of_times_sent = fields.Integer(string='# of Times Sent')
    save_payment_link = fields.Char(string='Save Payment Link', copy=False)
    odoo_payment_link = fields.Boolean(string='Payment Link', copy=False)
    request_amount = fields.Float(string='Request Amount' ,copy=False)
    last_request_amount = fields.Float(string='Last Request Amount', copy=False)
    odoo_payment_link_doc = fields.Char(string='Payment Link Doc', copy=False)
    is_email_request = fields.Boolean(string='Email Pay sent', copy=False)
    ebiz_payment_link = fields.Selection([
        ('default', ''),
        ('pending', 'Pending'),
        ('received', 'Received'),
        ('applied', 'Applied'),
    ], string='Pay link Status', default='default', readonly=True, copy=False, index=True)


    def _log_pay_link(self):
        for line in self:
            if line.odoo_payment_link_doc:
                message_log ='New Payment Link has been generated: '+str(line.odoo_payment_link_doc)
                line.message_post(body=message_log)


    @api.depends('partner_id', 'partner_id.email')
    def _compute_customer_id(self):
        """
           Computing customer information on invoice.
        """
        for inv in self:
            token = inv.partner_id.get_default_token()
            inv.customer_id = inv.partner_id.id
            inv.email = inv.partner_id.email
            inv.default_payment_method_name = token.display_name if token else 'N/A'
            inv.default_payment_method_id = token.id if token else 0

    @api.depends('ebiz_internal_id')
    def _compute_sync_status(self):
        """
            Computing invoice's sync status.
        """
        for order in self:
            order.sync_status = "Synchronized" if order.ebiz_internal_id else "Pending"

    @api.depends('transaction_ids')
    def _compute_done_transaction_ids(self):
        """
          Computing done transactions.
        """
        for trans in self:
            trans.done_transaction_ids = trans.transaction_ids.filtered(lambda t: t.state == 'done')

    def action_post(self):
        #if 'hash_version' in self.env.context or 'validate_analytic' in self.env.context:
        #    return super().action_post()
        ret = super(AccountMoveInh, self.with_context({'from_post': True})).action_post()
        # on posting invoice auto sync invoice

        if self.partner_id.ebiz_profile_id:
            ebiz_auto_sync_invoice = self.partner_id.ebiz_profile_id.ebiz_auto_sync_invoice
            ebiz_auto_sync_credit_notes = self.partner_id.ebiz_profile_id.ebiz_auto_sync_credit_notes

            for invoice in self:
                if invoice.move_type == "out_invoice" and ebiz_auto_sync_invoice:
                    if invoice.partner_id.customer_rank > 0:
                        invoice.sync_to_ebiz()

                if invoice.move_type == "out_refund" and ebiz_auto_sync_credit_notes:
                    if invoice.partner_id.customer_rank > 0:
                        invoice.sync_to_ebiz()
                elif (invoice.move_type == "out_refund" or invoice.move_type == "out_invoice") and invoice.ebiz_internal_id and not invoice.done_transaction_ids:
                    if invoice.partner_id.customer_rank > 0:
                        invoice.sync_to_ebiz()
                auto_capture_needs = [invoice.partner_id.ebiz_profile_id.apply_sale_pay_inv,
                                      invoice.authorized_transaction_ids,
                                      invoice.authorized_transaction_ids[0].provider_id.code == 'ebizcharge' if invoice.authorized_transaction_ids else False,
                                      invoice.authorized_transaction_ids[0].emv_transaction != True if invoice.authorized_transaction_ids else False,
                                      not invoice.authorized_transaction_ids[0].ebiz_transaction_result if invoice.authorized_transaction_ids else False]
                if all(auto_capture_needs):
                    invoice.payment_action_capture()
                if self.done_transaction_ids and self.done_transaction_ids[0].transaction_type == 'deposit' and invoice.done_transaction_ids[
                        0].provider_id.code == 'ebizcharge' and self.done_transaction_ids[
                        0].emv_transaction != True:

                    if self.done_transaction_ids.payment_id.state not in ['posted', 'cancel']:
                        self.done_transaction_ids.payment_id.ref = self.name
                        self.done_transaction_ids.payment_id.action_post()
                        self.reconcile()
                    elif self.done_transaction_ids.payment_id.state == 'posted':
                        self.with_context({'payment_id': self.done_transaction_ids.payment_id.id}).reconcile()

                    # self.done_transaction_ids.payment_id.ref = self.name
                    # self.done_transaction_ids.payment_id.action_post()
                    # self.reconcile()
                if  invoice.partner_id.ebiz_profile_id.invoice_auto_gpl and invoice.sale_order_count==0 and  invoice.payment_state not in ('in_payment','paid'):
                    invoice.action_generate_pay_ebiz_link()
        return ret

    def _transaction_line(self, line):
        qty = line.product_uom_qty if hasattr(line, 'product_uom_qty') else line.quantity
        tax_ids = line.tax_ids if hasattr(line, 'tax_ids') else line.tax_id
        price_tax = line.price_tax if hasattr(line, 'price_tax') else 0
        return {
            'SKU': line.product_id.id,
            'ProductName': line.product_id.name,
            'Description': line.name,
            'UnitPrice': line.price_unit,
            'Taxable': True if tax_ids else False,
            'TaxAmount': int(price_tax),
            'Qty': int(qty),
        }

    def _transaction_lines(self, lines):
        item_list = []
        for line in lines:
            item_list.append(self._transaction_line(line))
        return {'TransactionLineItem': item_list}

    def action_generate_pay_ebiz_link(self):
        template = self.env['email.templates'].search([('template_type_id', '=', 'WebFormEmail'), (
            'instance_id', '=', self.partner_id.ebiz_profile_id.id) ], limit=1)
        if template:
            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=self.partner_id.ebiz_profile_id)
            fname = self.partner_id.name.split(' ')
            lname = ''
            for name in range(1, len(fname)):
                lname += fname[name]
            address = ''
            if self.partner_id.street:
                address += self.partner_id.street
            if self.partner_id.street2:
                address += ' ' + self.partner_id.street2

            lines = self.invoice_line_ids
            get_merchant_data = False
            get_allow_credit_card_pay = False
            if self.partner_id.ebiz_profile_id:
                get_merchant_data = self.partner_id.ebiz_profile_id.merchant_data
                get_allow_credit_card_pay = self.partner_id.ebiz_profile_id.allow_credit_card_pay
            payment_method = 'CC'
            if get_merchant_data and get_allow_credit_card_pay:
                payment_method = 'CC,ACH'
            elif get_merchant_data:
                payment_method = 'ACH'
            elif get_allow_credit_card_pay:
                payment_method = 'CC'

            ePaymentForm = {
                'FormType': 'PayLinkOnly',
                'FromEmail': 'support@ebizcharge.com',
                'FromName': 'EBizCharge',
                'EmailSubject': template.template_subject,
                'EmailAddress': self.partner_id.email if self.partner_id.email else ' ',
                'EmailTemplateID': template.template_id,
                'EmailTemplateName': template.name,
                'ShowSavedPaymentMethods': True,
                'CustFullName': self.partner_id.name,
                'TotalAmount': self.amount_total,
                'PayByType': payment_method,
                'AmountDue': self.amount_residual,
                'ShippingAmount': 0,
                'CustomerId': self.partner_id.ebiz_customer_id or self.partner_id.id,
                'ShowViewInvoiceLink': True,
                'SendEmailToCustomer': False,
                'TaxAmount': self.amount_tax,
                'SoftwareId': 'ODOOPayLinkOnly',
                'InvoiceInternalId': self.ebiz_internal_id,
                'Description': 'Invoice' ,
                'DocumentTypeId': 'Invoice' ,
                'InvoiceNumber': str(self.id) if str(self.name) == '/' else str(self.name),
                'PoNum': self.ref if self.ref else self.name,
                'BillingAddress': {
                    "FirstName": fname[0],
                    "LastName": lname,
                    "CompanyName": self.partner_id.company_name if self.partner_id.company_name else '',
                    "Address1": address,
                    "City": self.partner_id.city if self.partner_id.city else '',
                    "State": self.partner_id.state_id.code or 'CA',
                    "ZipCode": self.partner_id.zip or '',
                    "Country": self.partner_id.country_id.code or 'US',
                },
                "LineItems": self._transaction_lines(lines),
            }

            if self.partner_id.ebiz_customer_id:
                ePaymentForm['CustomerId'] = self.partner_id.ebiz_customer_id

            ePaymentForm[
                    'Date'] = self.invoice_date if self.invoice_date else self.invoice_date_due if self.invoice_date_due else ''
            form_url = ebiz.client.service.GetEbizWebFormURL(**{
                'securityToken': ebiz._generate_security_json(),
                'ePaymentForm': ePaymentForm
            })
            self.save_payment_link = form_url
            self.is_email_request = False
            self.payment_internal_id = form_url.split('=')[1]
            # self.ebiz_invoice_status = ' '
            if self.save_payment_link:
                message_log ='New EBizCharge Payment Link has been generated: '+str(form_url)
                self.message_post(body=message_log)



    def show_ebiz_invoice(self):
        """ Show EBizCharge invoice """
        return {
            'name': 'Go to website',
            'res_model': 'ir.actions.act_url',
            'type': 'ir.actions.act_url',
            'target': 'new',
            'url': f"https://cloudview1.ebizcharge.net/ViewInvoice1.aspx?InvoiceInternalId={self.ebiz_internal_id}"
        }

    def sync_to_ebiz(self, time_sample=None):
        """
        Kuldeep Implementation
        Sync single Invoice to EBizCharge
        """
        update_params = {}
        self.ensure_one()
        sale_id = self.invoice_line_ids[0].sale_line_ids.order_id if self.invoice_line_ids else False
        instance = None
        if self.partner_id.ebiz_profile_id:
            instance = self.partner_id.ebiz_profile_id
        else:
            default_instance = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_default', '=', True)], limit=1)
            if default_instance:
                instance = default_instance
        web_sale = self.env['ir.module.module'].sudo().search(
            [('name', '=', 'website_sale'), ('state', 'in', ['installed', 'to upgrade', 'to remove'])])
        ebiz_obj = self.env['ebiz.charge.api']
        if web_sale:
            ebiz = ebiz_obj.get_ebiz_charge_obj(
                website_id=sale_id.website_id.id if sale_id and hasattr(sale_id, 'website_id') else None, instance=instance)
        else:
            ebiz = ebiz_obj.get_ebiz_charge_obj(instance=instance)
        credit_obj = self.env['logs.credit.notes']
        log_obj = self.env['ebiz.log.invoice']
        credit_notes_upload = self.env['upload.credit.notes'].search([], limit=1)
        invoice_upload = self.env['ebiz.upload.invoice'].search([], limit=1)
        if not self.partner_id.ebiz_internal_id:
            self.partner_id.sync_to_ebiz()
        if self.ebiz_internal_id:
            resp = ebiz.update_invoice(self)
            if resp['Error']=='Not Found ':
                resp_search = False
                resp = ebiz.sync_invoice(self)
                if resp['ErrorCode'] == 2:
                    resp_search = self.ebiz_search_invoice()
                update_params.update({'ebiz_internal_id': resp['InvoiceInternalId'] or resp_search['InvoiceInternalId'],
                                      'sync_response': 'Success' if resp['ErrorCode'] in [0, 2] else resp['Error']})
                logs_dict = self.get_log_dict(resp)
                if self.move_type == 'out_refund':
                    logs_dict['sync_log_id'] = credit_notes_upload.id
                    credit_obj.create(logs_dict)
                else:
                    logs_dict['sync_log_id'] = invoice_upload.id
                    log_obj.create(logs_dict)
            else:
                update_params = {'sync_response': resp['Error'] or resp['Status']}
                logs_dict = self.get_log_dict(resp)
                if self.move_type == 'out_refund':
                    logs_dict['sync_log_id'] = credit_notes_upload.id
                    credit_obj.create(logs_dict)
                else:
                    logs_dict['sync_log_id'] = invoice_upload.id
                    log_obj.create(logs_dict)
        else:
            resp_search = False
            resp = ebiz.sync_invoice(self)
            if resp['ErrorCode'] == 2:
                resp_search = self.ebiz_search_invoice()
            update_params.update({'ebiz_internal_id': resp['InvoiceInternalId'] or resp_search['InvoiceInternalId'],
                                  'sync_response': 'Success' if resp['ErrorCode'] in [0, 2] else resp['Error']})
            logs_dict = self.get_log_dict(resp)
            if self.move_type == 'out_refund':
                logs_dict['sync_log_id'] = credit_notes_upload.id
                credit_obj.create(logs_dict)
            else:
                logs_dict['sync_log_id'] = invoice_upload.id
                log_obj.create(logs_dict)

        update_params.update({
            'last_sync_date': fields.Datetime.now()
        })
        self.write(update_params)
        return resp


    def get_log_dict(self, resp):
        return {
            'invoice': self.id,
            'partner_id': self.partner_id.id,
            'customer_id': self.partner_id.id,
            'sync_status': 'Success' if resp['ErrorCode'] in [0, 2] else resp['Error'],
            'last_sync_date': datetime.now(),
            'currency_id': self.env.user.currency_id.id,
            'amount_untaxed': self.amount_untaxed_signed,
            'amount_total_signed': self.amount_total,
            'amount_residual_signed': self.amount_residual,
            'invoice_date_due': self.invoice_date_due,
            'invoice_date': self.invoice_date,
            'name': self.name,
        }

    def process_invoices(self, send_receipt):
        """
            Niaz Implementation:
            Email the receipt to customer, if email receipts templates not there in odoo, it will fetch.
            return: wizard to select the receipt template
        """
        try:
            message_lines = []
            for record in self:
                record.sync_to_ebiz()
                record.ebiz_batch_procssing_reg(record.default_payment_method_id, send_receipt)
                message_lines.append([0, 0, {'customer_id': record.customer_id,
                                             "customer_name": record.partner_id.name,
                                             'invoice_no': record.name,
                                             'status': record.transaction_ids.state}])
            self.create_log_lines()
            wizard = self.env['batch.process.message'].create({'name': "Batch Process", 'lines_ids': message_lines})
            action = self.env.ref('payment_ebizcharge_crm.wizard_batch_process_message_action').read()[0]
            action['context'] = self._context
            action['res_id'] = wizard.id
            return action

        except Exception as e:
            _logger.exception(e)
            raise UserError(e)

    def create_log_lines(self):
        list_of_invoices = []
        for invoice in self:
            partner = invoice.partner_id
            transaction_id = invoice.transaction_ids[0]
            dict1 = {
                "name": invoice['name'],
                "customer_name": partner.id,
                "customer_id": partner.id,
                "date_paid": transaction_id.date,
                "currency_id": invoice.currency_id.id,
                "amount_paid": invoice.amount_total,
                "transaction_status": transaction_id.state,
                "payment_method": invoice.default_payment_method_name,
                "auth_code": transaction_id.ebiz_auth_code,
                "transaction_ref": transaction_id.provider_reference,
                'email': invoice.email,
            }
            list_of_invoices.append(dict1)
        self.env['sync.batch.log'].search([]).unlink()
        self.env['sync.batch.log'].create(list_of_invoices)

    def sync_to_ebiz_invoice(self):
        if self.move_type == "out_invoice":
            self.sync_to_ebiz()
            return message_wizard('Invoice uploaded successfully!')
        else:
            return False

    def sync_to_ebiz_credit_note(self):
        if self.move_type == "out_refund":
            self.sync_to_ebiz()
            return message_wizard('Credit Note uploaded successfully!')
        else:
            return False

    def ebiz_search_invoice(self):
        instance = None
        if self.partner_id.ebiz_profile_id:
            instance = self.partner_id.ebiz_profile_id

        ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
        resp = ebiz.client.service.SearchInvoices(**{
            'securityTokenCustomer': ebiz._generate_security_json(),
            'customerId': self.partner_id.id,
            'invoiceNumber': self.name,
            'start': 0,
            'limit': 0,
            'includeItems': False
        })
        if resp:
            return resp[0]
        return resp

    def action_register_payment(self):
        for line in self:
            if line.emv_transaction_id:
                line.emv_transaction_id.action_check(trans=line.emv_transaction_id.id)
        ret = super(AccountMoveInh, self).action_register_payment()
        ebiz_obj = self.env['ebiz.charge.api']
        check = any(inv.save_payment_link for inv in self)
        for line in self:
            if line.ebiz_internal_id:
                instance = None
                if line.partner_id.ebiz_profile_id:
                    instance = line.partner_id.ebiz_profile_id

                ebiz = ebiz_obj.get_ebiz_charge_obj(instance=instance)
                from_date = datetime.strftime((line.create_date - timedelta(days=1)), '%Y-%m-%dT%H:%M:%S')
                to_date = datetime.strftime((datetime.now() + timedelta(days=1)), '%Y-%m-%dT%H:%M:%S')
                params = {
                    'securityToken': ebiz._generate_security_json(),
                    "fromDateTime": from_date,
                    "toDateTime": to_date,
                    "customerId": line.partner_id.id,
                    "limit": 1000,
                    "start": 0,
                }
                payments = ebiz.client.service.GetPayments(**params)
                payments = list(filter(lambda x: x['InvoiceNumber'] == line.name, payments or []))
                if payments:
                    for payment in payments:
                        line.ebiz_create_payment_line(payment['PaidAmount'])
                        resp = ebiz.client.service.MarkPaymentAsApplied(**{
                            'securityToken': ebiz._generate_security_json(),
                            'paymentInternalId': payment['PaymentInternalId'],
                            'invoiceNumber': line.name
                        })
                    inv_type = 'invoice' if line.move_type == 'out_invoice' else 'credit note'
                    return message_wizard(f'This {inv_type} has already been processed on the EBizCharge portal!')
        emv_device_id = 0
        if line.partner_id.ebiz_profile_id:
            if line.partner_id.ebiz_profile_id and line.partner_id.ebiz_profile_id.is_emv_enabled:
                line.partner_id.ebiz_profile_id.action_get_devices()
        for line in self:
            emv_devices = self.env['ebizcharge.emv.device'].search([('is_default_emv', '=', True),('merchant_id','=',line.partner_id.ebiz_profile_id.id)], limit=1)
            if emv_devices:
                emv_device_id = emv_devices
        ret['context'].update({
           # 'default_emv_device_id': emv_device_id.id if emv_device_id else False,
            'default_is_pay_link': check
        })
        return ret

    def action_reverse(self):
        if not self.env.context.get('bypass_credit_note_restriction'):
            total_credit_amount = 0

            for notes in self.credit_note_ids:
                total_credit_amount += notes.amount_total

            if self.amount_total <= total_credit_amount:
                params = {
                    "invoice_id": self.id,
                    "text": "You have already given the customer credit for the full amount of invoice. Do you want "
                            "to give more credit to customer against this invoice?"
                }
                wiz = self.env['wizard.credit.note.validate'].create(params)
                action = self.env.ref('payment_ebizcharge_crm.wizard_credit_note_validate_action').read()[0]
                action['res_id'] = wiz.id
                return action

        action = super(AccountMoveInh, self).action_reverse()
        if self._context.get('active_model') == 'account.move':
            action['context'] = dict(self._context)
        return action

    def run_ebiz_transaction(self, payment_token_id, command, card=None, token_ebiz=None):
        """
        Kuldeep implemented
        run ebiz transaction on the Invoice
        """
        self.ensure_one()
        if not self.partner_id.ebiz_internal_id and payment_token_id and payment_token_id.partner_id.id==self.partner_id.id:
            self.partner_id.sync_to_ebiz()
        #if not self.commercial_partner_id.payment_token_ids:
        #    raise UserError("Please enter payment method profile on the customer.")
        instance = None
        if payment_token_id:
            instance = payment_token_id.partner_id.ebiz_profile_id
        elif self.partner_id.ebiz_profile_id:
            instance = self.partner_id.ebiz_profile_id

        elif self.env.user.partner_id.ebiz_profile_id:
            instance = self.env.user.partner_id.ebiz_profile_id
        else:
            default_instance = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_default', '=', True)], limit=1)
            if default_instance:
                instance = default_instance

        ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
        if self.env.context.get('run_transaction'):
            resp = ebiz.run_full_amount_transaction(self, payment_token_id, command, card, token_ebiz=token_ebiz)
        else:
            resp = ebiz.run_customer_transaction(self, payment_token_id, command, current_user=self.env.user.partner_id)
        return resp


    def payment_action_capture(self):
        if self.authorized_transaction_ids and self.authorized_transaction_ids[0].provider_id.code == 'ebizcharge' and self.authorized_transaction_ids[0].emv_transaction!=True:
            ret = super(AccountMoveInh,
                        self.with_context({'from_invoice': True, 'invoice_id': self})).payment_action_capture()
            return ret
        elif self.authorized_transaction_ids[0].emv_transaction==True:
            ret = super(AccountMoveInh,
                        self.with_context({'from_invoice': True,'invoice_id': self, 'emv_trans': self.authorized_transaction_ids})).payment_action_capture()
            return ret
        ret = super(AccountMoveInh, self).payment_action_capture()
        return ret
        

    def payment_action_void(self):
        ret = super(AccountMoveInh, self).payment_action_void()
        receipt_check = self.env['account.move.receipts'].search([('invoice_id', '=', self.id)])
        if receipt_check:
            receipt_check[-1].unlink()
        return ret

    def ebiz_create_payment_line(self, amount):
        acquirer = self.env['payment.provider'].search(
            [('company_id', '=', self.company_id.id), ('code', '=', 'ebizcharge')])
        journal_id = acquirer.journal_id
        ebiz_method = self.env['account.payment.method.line'].search(
            [('journal_id', '=', journal_id.id), ('payment_method_id.code', '=', 'ebizcharge')], limit=1)
        payment = self.env['account.payment'] \
            .sudo().with_context(active_ids=self.ids, active_model='account.move', active_id=self.id) \
            .create({'journal_id': journal_id.id,
                     'payment_method_id': ebiz_method.payment_method_id.id,
                     'payment_method_line_id':ebiz_method.id,
                     'token_type': None,
                     'amount': amount,
                     'partner_id': self.partner_id.id,
                     'ref': self.name or None,
                     'payment_type': 'outbound' if self.move_type == 'out_refund' else 'inbound'
                     })

        payment.with_context({'do_not_run_transaction': True}).action_post()
        self.reconcile()
        self.write({'ebiz_invoice_status': 'partially_received' if self.amount_residual else 'received',
                    'is_payment_processed': True})
        if self.save_payment_link:
            self.request_amount = self.amount_residual
            self.last_request_amount = 0
            self.ebiz_payment_link = 'applied'
            self.save_payment_link = False
            instance = self.partner_id.ebiz_profile_id
            if self.payment_internal_id and instance:
                ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
                ebiz.client.service.DeleteEbizWebFormPayment(**{
                    'securityToken': ebiz._generate_security_json(),
                    'paymentInternalId': self.payment_internal_id,
                })
        return super(AccountMoveInh, self).payment_action_capture()

    def ebiz_batch_procssing_reg(self, default_card_id, ebiz_send_receipt):
        provider_obj = self.env['payment.provider']
        acquirer = provider_obj.search([('company_id', '=', self.company_id.id), ('code', '=', 'ebizcharge')])
        journal_id = acquirer.journal_id
        company_acquirer = provider_obj.search([('company_id', '=', self.company_id.id), ('code', '=', 'ebizcharge')])
        if not company_acquirer:
            raise UserError('There is no Acquirer link to this ' + self.company_id.name + '.')
        token = self.env['payment.token'].browse(default_card_id)
        ebiz_method = self.env['account.payment.method.line'].search(
            [('journal_id', '=', journal_id.id), ('payment_method_id.code', '=', 'ebizcharge')], limit=1)
        payment = self.env['account.payment'] \
            .sudo().with_context(active_ids=self.ids, active_model='account.move', active_id=self.id) \
            .create({'journal_id': journal_id.id,
                     'card_id': default_card_id if token.token_type == "credit" else None,
                     'ach_id': default_card_id if token.token_type == "ach" else None,
                     'payment_token_id': default_card_id,
                     'token_type': token.token_type,
                     'amount': self.amount_residual_signed,
                     'transaction_command': 'Sale',
                     'ebiz_send_receipt': ebiz_send_receipt,
                     'ebiz_receipt_emails': self.partner_id.email,
                     'payment_method_id': ebiz_method.payment_method_id.id,
                     'payment_method_line_id': ebiz_method.id,
                     'partner_id': self.partner_id.id,
                     'ref': self.name or None,
                     'payment_type': 'inbound'
                     })
        payment.with_context({'payment_data': {
            'token_type': token.token_type,
            'card_id': token if token.token_type == "credit" else None,
            'ach_id': token if token.token_type == "ach" else None,
            'card_card_number': payment.card_card_number,
            'security_code': False,
            'ebiz_send_receipt': ebiz_send_receipt,
            'ebiz_receipt_emails': self.partner_id.email,
        }, 'batch_processing': True}).action_post()
        if payment.state == 'posted':
            self.reconcile()
            self.write({'ebiz_invoice_status': 'partially_received' if self.amount_residual else 'received',
                        'is_payment_processed': True})
        return super(AccountMoveInh, self).payment_action_capture()

    def process_refund_payment(self):
        acquirer = self.env['payment.provider'].search(
            [('company_id', '=', self.company_id.id), ('code', '=', 'ebizcharge')])
        journal_id = acquirer.journal_id
        ebiz_method = self.env['account.payment.method.line'].search(
            [('journal_id', '=', journal_id.id), ('payment_method_id.code', '=', 'ebizcharge')], limit=1)

        payment_method_id = self.env['account.payment.method'].search([('code', '=', 'electronic')]).id
        payment = self.env['account.payment'] \
            .sudo().with_context(active_ids=self.ids, active_model='account.move', active_id=self.id) \
            .create({'journal_id': journal_id.id, 'payment_method_id': ebiz_method.payment_method_id.id, 'payment_method_line_id':ebiz_method.id})
        payment.with_context({'pass_validation': True}).action_post()

    def ebiz_sync_multiple_invoices(self):
        resp_lines = []
        success = 0
        failed = 0
        total = len(self)

        for inv in self:
            resp_line = {
                'customer_name': inv.partner_id.name,
                'customer_id': inv.partner_id.id,
                'invoice_number': inv.name
            }
            try:
                resp = inv.sync_to_ebiz()
                resp_line['record_message'] = resp['Error'] or resp['Status']

            except Exception as e:
                _logger.exception(e)
                resp_line['record_message'] = str(e)

            if resp_line['record_message'] == 'Success' or resp_line['record_message'] == 'Record already exists':
                success += 1
            else:
                failed += 1
            resp_lines.append([0, 0, resp_line])

        wizard = self.env['wizard.multi.sync.message'].create({'name': 'invoices', 'invoice_lines_ids': resp_lines,
                                                               'success_count': success, 'failed_count': failed,
                                                               'total': total})
        action = self.env.ref('payment_ebizcharge_crm.wizard_multi_sync_message_action').read()[0]
        action['context'] = self._context
        action['res_id'] = wizard.id
        return action

    def sync_multi_customers_from_upload_invoices(self, list):
        invoice_records = self.env['account.move'].browse(list).exists()
        resp_lines = []
        success = 0
        failed = 0
        total = len(invoice_records)
        for inv in invoice_records:
            resp_line = {
                'customer_name': inv.partner_id.name,
                'customer_id': inv.partner_id.id,
                'invoice_number': inv.name
            }
            try:
                resp = inv.sync_to_ebiz()
                resp_line['record_message'] = resp['Error'] or resp['Status']

            except Exception as e:
                _logger.exception(e)
                resp_line['record_message'] = str(e)

            if resp_line['record_message'] == 'Success' or resp_line['record_message'] == 'Record already exists':
                success += 1
            else:
                failed += 1
            resp_lines.append([0, 0, resp_line])

        if self.env.context.get('credit') == 'credit_notes':
            wizard = self.env['wizard.multi.sync.message'].create(
                {'name': 'credit_notes', 'invoice_lines_ids': resp_lines,
                 'success_count': success, 'failed_count': failed, 'total': total})
        else:
            wizard = self.env['wizard.multi.sync.message'].create({'name': 'invoices', 'invoice_lines_ids': resp_lines,
                                                                   'success_count': success, 'failed_count': failed,
                                                                   'total': total})
        action = self.env.ref('payment_ebizcharge_crm.wizard_multi_sync_message_action').read()[0]
        action['context'] = self._context
        action['res_id'] = wizard.id
        return action

    def write(self, values):
        ret = super(AccountMoveInh, self).write(values)
        if self._ebiz_check_invoice_update(values):
            for invoice in self:
                if invoice.ebiz_internal_id and invoice.partner_id.customer_rank > 0:
                    invoice.sync_to_ebiz()
        return ret

    def email_invoice_ebiz(self):
        """
        Niaz Implementation:
        Call the wizard, use to send email invoice to customer, fetch the email templates incase not present before
        return: Wizard
        """
        try:

            if self.ebiz_invoice_status == 'pending':
                raise UserError(f'An email pay request has already been sent.')

            if self.state == 'draft':
                self.action_post()

            if not self.ebiz_internal_id:
                self.sync_to_ebiz()

            self.env.cr.commit()




            if self.save_payment_link:
                instance = None
                if self.partner_id.ebiz_profile_id:
                    instance = self.partner_id.ebiz_profile_id
                else:
                    default_instance = self.env['ebizcharge.instance.config'].search(
                        [('is_valid_credential', '=', True), ('is_default', '=', True), ('is_active', '=', True)],
                        limit=1)
                    if default_instance:
                        instance = default_instance
                ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
                today = datetime.now()
                end = today + timedelta(days=1)
                start = today + timedelta(days=-365)

                received_payments = ebiz.client.service.SearchEbizWebFormReceivedPayments(**{
                    'securityToken': ebiz._generate_security_json(),
                    'fromPaymentRequestDateTime': str(start.date()),
                    'toPaymentRequestDateTime': str(end.date()),
                    'start': 0,
                    'limit': 10000,
                })

                if received_payments:
                    for invoice in received_payments:
                        odoo_invoice = self.env['account.move'].search(
                            [('payment_internal_id', '=', invoice['PaymentInternalId'])])
                        try:
                            if odoo_invoice and odoo_invoice.id == self.id:
                                text = f"There is a payment of {self.env.user.company_id.currency_id.symbol}{float(invoice['PaidAmount'])} received on this {self.name}.\nWould you like to apply this payment?"
                                wizard = self.env['wizard.receive.email.pay'].create({"record_id": self.id,
                                                                                      "odoo_invoice": odoo_invoice.id,
                                                                                      "text": text})
                                action = self.env.ref('payment_ebizcharge_crm.wizard_recieved_email_pay').read()[0]
                                action['res_id'] = wizard.id
                                action['context'] = dict(
                                    invoice=invoice,
                                )
                                return action
                            else:
                                continue
                        except Exception:
                            pass
                    else:
                        text = f"This document has an existing payment link. Proceeding will invalidate the existing link. Do you want to continue?"
                        wizard = self.env['wizard.receive.email.payment.link'].create({"record_id": self.id,
                                                                                       "odoo_invoice": self.id,
                                                                                       "text": text})
                        action = self.env.ref('payment_ebizcharge_crm.wizard_received_email_pay_payment_link').read()[0]
                        action['res_id'] = wizard.id
                        action['context'] = dict(
                            invoice=invoice,
                            email_pay=True,
                        )
                        return action
            if self.odoo_payment_link:
                text = f"This document has a pending payment link. Proceeding may increase the risk of double payments. Do you want to continue?"
                wizard = self.env['wizard.receive.email.payment.link'].create({"record_id": self.id,
                                                                               "odoo_invoice": self.id,
                                                                               "text": text})
                action = self.env.ref('payment_ebizcharge_crm.wizard_received_email_pay_payment_link').read()[0]
                action['res_id'] = wizard.id
                action['context'] = dict(
                    invoice=self.id,
                    email_pay=True,
                )
                return action

            return {'type': 'ir.actions.act_window',
                    'name': _('Email Pay Request'),
                    'res_model': 'email.invoice',
                    'target': 'new',
                    'view_mode': 'form',
                    'view_type': 'form',
                    'context': {
                        'default_contacts_to': [[6, 0, [self.partner_id.id]]],
                        'default_partner_ids': [[6, 0, [self.partner_id.id]]],
                        'default_record_id': self.id,
                        'default_ebiz_profile_id': self.partner_id.ebiz_profile_id.id,
                        'default_currency_id': self.currency_id.id,
                        'default_amount': self.amount_residual if self.amount_residual else self.amount_total,
                        'default_model_name': str(self._inherit),
                        'default_email_customer': str(self.partner_id.email if self.partner_id.email else ''),
                        'selection_check': 1,
                    },
                    }

        except Exception as e:
            raise ValidationError(e)

    def resend_email_invoice_ebiz(self):
        """
            Niaz Implementation:
            Use to resend the email invoice
        """
        try:
            instance = None
            if self.partner_id.ebiz_profile_id:
                instance = self.partner_id.ebiz_profile_id

            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            form_url = ebiz.client.service.ResendEbizWebFormEmail(**{
                'securityToken': ebiz._generate_security_json(),
                'paymentInternalId': self.payment_internal_id,
            })

            self.no_of_times_sent += 1
            return message_wizard('Email pay request has been successfully resent!')

        except Exception as e:
            if e.args[0] == 'Error: Object reference not set to an instance of an object.':
                raise UserError('This Invoice Either Paid Or Deleted!')
            raise UserError(e)


    def get_payments_sales_apply(self,instance=None):
        ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
        params = {
            'securityToken': ebiz._generate_security_json(),
            "filters": {'SearchFilter': {'FieldName': 'SoftwareId', 'ComparisonOperator': 'eq',
                                         'FieldValue': 'IOSMobileApp'}, },
            "countOnly": False,
            "limit": 1000,
            "start": 0,
        }
        payments = ebiz.client.service.SearchApplicationTransactions(**params)
        payment_lines = []

        def ref_date(date):
            if not date:
                return date
            if '-' in date:
                rf_date = date.split('-')
            else:
                rf_date = date.split('/')
            return f"{rf_date[1]}/{rf_date[2]}/{rf_date[0]}"

        if payments:
            for payment in payments['ApplicationTransactions']['ApplicationTransactionDetails']:
                if 'CustomerInternalId' in payment and payment['CustomerInternalId'] != 'False':
                    odooCustomer = self.env['res.partner'].search(
                        [('ebiz_internal_id', '=', payment['CustomerInternalId'])], limit=1)
                    if odooCustomer:
                        currency_id = odooCustomer.property_product_pricelist.currency_id.id
                        saleordrr = self.env['sale.order'].search([('name', '=', payment['LinkedToExternalUniqueId'])], limit=1)
                        if saleordrr:
                            if saleordrr:
                                resp = ebiz.client.service.MarkApplicationTransactionAsApplied(**{
                                    'securityToken': ebiz._generate_security_json(),
                                    'applicationTransactionInternalId': payment['ApplicationTransactionInternalId'],
                                })

                                if resp and resp['Status'] == 'Success':
                                    payment_acq = self.env['payment.provider'].search(
                                        [('company_id', '=',
                                          odooCustomer.company_id.id if odooCustomer.company_id else self.env.company.id),
                                         ('code', '=', 'ebizcharge')])
                                    ebiz_method = self.env['account.payment.method.line'].search(
                                        [('journal_id', '=', payment_acq.journal_id.id),
                                         ('payment_method_id.code', '=', 'ebizcharge')], limit=1)
                                    payment = self.env['account.payment'].sudo().create({
                                        'journal_id': payment_acq.journal_id.id,
                                        'payment_method_id': ebiz_method.payment_method_id.id,
                                        'payment_method_line_id':ebiz_method.id,
                                        'partner_id': odooCustomer.id,
                                        'payment_reference': payment['TransactionId'],

                                        'amount': float(payment['TransactionAmount'] or "0"),
                                        'partner_type': 'customer',
                                        'payment_type': 'inbound',
                                        'ref': payment['LinkedToExternalUniqueId'] if payment['LinkedToExternalUniqueId'] else '',
                                    })
                                    payment.action_post()

    def get_pending_invoices(self, instance=None):
        """
            Niaz Implementation:
            Get received payments paid via email invoice, change status of email to received.
        """
        try:
            filters_list = []
            if not instance:
                filters_list.append(
                    {'FieldName': 'InvoiceNumber', 'ComparisonOperator': 'eq', 'FieldValue': self.name})
                instance = None
                if self.partner_id.ebiz_profile_id:
                    instance = self.partner_id.ebiz_profile_id

            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            today = datetime.now()
            end = today + timedelta(days=1)
            start = today + timedelta(days=-365)
            filters_list = []
            if 'for_pay_link' in self.env.context and self.ebiz_invoice_status != 'pending':
                filters_list.append(
                    {'FieldName': 'FormType', 'ComparisonOperator': 'eq',
                     'FieldValue': 'PayLinkOnly'})
            received_payments = ebiz.client.service.SearchEbizWebFormReceivedPayments(**{
                'securityToken': ebiz._generate_security_json(),
                'fromPaymentRequestDateTime': str(start.date()),
                'toPaymentRequestDateTime': str(end.date()),
                'start': 0,
                'limit': 10000,
                "filters": {'SearchFilter': filters_list},
            })
            if received_payments:
                for invoice in received_payments:
                    try:
                        odoo_invoice = self.env['account.move'].search(
                            [('payment_internal_id', '=', invoice['PaymentInternalId']), '|',
                             ('ebiz_invoice_status', 'in', ['pending']), ('ebiz_payment_link', 'in', ['received'])])
                        if odoo_invoice:
                            if odoo_invoice.state != 'posted':
                                odoo_invoice.action_post()
                            if odoo_invoice['amount_residual'] - float(invoice['PaidAmount']) > 0:
                                odoo_invoice.write({
                                    'ebiz_invoice_status': 'partially_received',
                                    'receipt_ref_num': invoice['RefNum'],
                                    'is_payment_processed': True
                                })
                            else:
                                odoo_invoice.write({
                                    'ebiz_invoice_status': 'received',
                                    'receipt_ref_num': invoice['RefNum'],
                                    'is_payment_processed': True
                                })

                            self.env['account.move.receipts'].create({
                                'invoice_id': odoo_invoice.id,
                                'name': self.env.user.currency_id.symbol + invoice['PaidAmount'][:-2] + ' Paid On ' +
                                        invoice['PaymentRequestDateTime'].split('T')[0],
                                'ref_nums': invoice['RefNum'],
                                'model': str(self._inherit),
                            })
                            journal_id = False
                            payment_acq = self.env['payment.provider'].search(
                                [('company_id', '=', odoo_invoice.company_id.id), ('code', '=', 'ebizcharge')])
                            if payment_acq and payment_acq.state == 'enabled':
                                journal_id = payment_acq.journal_id

                            if journal_id:
                                ebiz_method = self.env['account.payment.method.line'].search(
                                    [('journal_id', '=', journal_id.id), ('payment_method_id.code', '=', 'ebizcharge')],
                                    limit=1)
                                payment = self.env['account.payment'] \
                                    .sudo().with_context(active_ids=odoo_invoice.ids, active_model='account.move',
                                                  active_id=odoo_invoice.id) \
                                    .create(
                                    {'journal_id': journal_id.id,
                                     'payment_method_id': ebiz_method.payment_method_id.id,
                                     'payment_method_line_id': ebiz_method.id,
                                     'amount': float(invoice['PaidAmount']),
                                     'token_type': None,
                                     'partner_id': odoo_invoice.partner_id.id,
                                     'ref': odoo_invoice.name or None,
                                     'payment_type': 'inbound'
                                     })
                                payment.with_context({'pass_validation': True}).action_post()
                                odoo_invoice.reconcile()
                                odoo_invoice.sync_to_ebiz()
                                odoo_invoice.save_payment_link = False
                                odoo_invoice.ebiz_payment_link = 'applied'
                                odoo_invoice.request_amount = 0
                                odoo_invoice.last_request_amount = 0
                                if odoo_invoice['amount_residual'] <= 0:
                                    res = super(AccountMoveInh, odoo_invoice).payment_action_capture()
                                    odoo_invoice.mark_as_applied()
                                else:
                                    ebiz.client.service.MarkEbizWebFormPaymentAsApplied(**{
                                        'securityToken': ebiz._generate_security_json(),
                                        'paymentInternalId': odoo_invoice.payment_internal_id,
                                    })
                    except Exception:
                        continue
            if 'for_pay_link' not in self.env.context:
                quick_payments = ebiz.client.service.GetPayments(**{
                    'securityToken': ebiz._generate_security_json(),
                    'fromDateTime': str(start.date()),
                    'toDateTime': str(end.date()),
                    'start': 0,
                    'limit': 10000,
                })

                if quick_payments:
                    for pay in quick_payments:
                        try:
                            if pay['InvoiceInternalId']:
                                is_odoo_invoice = self.env['account.move'].search(
                                    [('ebiz_internal_id', '=', pay['InvoiceInternalId'])])
                                is_credit = self.env['account.payment'].search([('name', '=', pay['InvoiceNumber'])])
                                if is_odoo_invoice:
                                    is_odoo_invoice.ebiz_create_payment_line(pay['PaidAmount'])
                                    resp = ebiz.client.service.MarkPaymentAsApplied(**{
                                        'securityToken': ebiz._generate_security_json(),
                                        'paymentInternalId': pay['PaymentInternalId'],
                                        'invoiceNumber': pay['InvoiceNumber'],
                                    })
                                    self.env['account.move.receipts'].create({
                                        'invoice_id': is_odoo_invoice.id,
                                        'name': self.env.user.currency_id.symbol + pay['PaidAmount'][:-2] + ' Paid On ' +
                                                pay['DatePaid'].split('T')[0],
                                        'ref_nums': pay['RefNum'],
                                        'model': str(self._inherit),
                                    })
                                if is_credit:
                                    resp = ebiz.client.service.MarkPaymentAsApplied(**{
                                        'securityToken': ebiz._generate_security_json(),
                                        'paymentInternalId': pay['PaymentInternalId'],
                                        'invoiceNumber': pay['InvoiceNumber'],
                                    })
                                    is_credit.action_draft()
                                    is_credit.cancel()
                            else:
                                partner = self.env['res.partner'].search([('id', '=', pay['CustomerId'])])
                                if partner and pay['TypeId'] in ['QuickPay']:
                                    payment_acq = self.env['payment.provider'].search(
                                        [('company_id', '=',
                                          partner.company_id.id if partner.company_id else self.env.company.id),
                                         ('code', '=', 'ebizcharge')])
                                    if payment_acq:
                                        ebiz_method = self.env['account.payment.method.line'].search(
                                            [('journal_id', '=', payment_acq.journal_id.id),
                                             ('payment_method_id.code', '=', 'ebizcharge')], limit=1)
                                        resp = ebiz.client.service.MarkPaymentAsApplied(**{
                                            'securityToken': ebiz._generate_security_json(),
                                            'paymentInternalId': pay['PaymentInternalId'],
                                            'invoiceNumber': pay['InvoiceNumber'] if pay['InvoiceNumber'] else '',
                                        })
                                        payment = self.env['account.payment'].sudo().create({
                                            'journal_id': payment_acq.journal_id.id,
                                            'payment_method_id': ebiz_method.payment_method_id.id,
                                            'payment_method_line_id':ebiz_method.id,
                                            'partner_id': partner.id,
                                            'payment_reference': pay['RefNum'],
                                            'amount': pay['PaidAmount'],
                                            'partner_type': 'customer',
                                            'payment_type': 'inbound',
                                            'ref': pay['InvoiceNumber'] if pay['InvoiceNumber'] else '',
                                        })
                                        payment.action_post()

                        except Exception:
                            continue

                recurring_payments = ebiz.client.service.SearchRecurringPayments(**{
                    'securityToken': ebiz._generate_security_json(),
                    "fromDateTime": str(start.date()),
                    "toDateTime": str(end.date()),
                    "limit": 1000,
                    "start": 0,
                })
                if recurring_payments:
                    for r_pay in recurring_payments:
                        try:
                            partner = self.env['res.partner'].search([('id', '=', r_pay['CustomerId'])])
                            if partner:
                                payment_acq = self.env['payment.provider'].search(
                                    [('company_id', '=',
                                      partner.company_id.id if partner.company_id else self.env.company.id),
                                     ('code', '=', 'ebizcharge')])
                                if payment_acq:
                                    ebiz_method = self.env['account.payment.method.line'].search(
                                        [('journal_id', '=', payment_acq.journal_id.id),
                                         ('payment_method_id.code', '=', 'ebizcharge')], limit=1)
                                    resp = ebiz.client.service.MarkRecurringPaymentAsApplied(**{
                                        'securityToken': ebiz._generate_security_json(),
                                        'paymentInternalId': r_pay['PaymentInternalId'],
                                        'invoiceNumber': r_pay['InvoiceNumber'] if r_pay['InvoiceNumber'] else '',
                                    })
                                    payment = self.env['account.payment'].sudo().create({
                                        'journal_id': payment_acq.journal_id.id,
                                        'payment_method_id': ebiz_method.payment_method_id.id,
                                        'payment_method_line_id':ebiz_method.id,
                                        'partner_id': partner.id,
                                        'payment_reference': r_pay['RefNum'],
                                        'amount': r_pay['PaidAmount'],
                                        'partner_type': 'customer',
                                        'payment_type': 'inbound',
                                        'ref': r_pay['InvoiceNumber'] if r_pay['InvoiceNumber'] else '',
                                    })
                                    payment.action_post()
                        except Exception:
                            continue

        except Exception as e:
            raise UserError(e)

    def received_apply_email_after_confirmation(self, invoice):
        try:
            odoo_invoice = self
            if odoo_invoice.state != 'posted':
                odoo_invoice.action_post()

            if odoo_invoice['amount_residual'] - float(invoice['PaidAmount']) > 0:
                odoo_invoice.write({
                    'ebiz_invoice_status': 'partially_received',
                    'receipt_ref_num': invoice['RefNum'],
                    'is_payment_processed': True
                })
            else:
                odoo_invoice.write({
                    'ebiz_invoice_status': 'received',
                    'receipt_ref_num': invoice['RefNum'],
                    'is_payment_processed': True
                })

            self.env['account.move.receipts'].create({
                'invoice_id': odoo_invoice.id,
                'name': self.env.user.currency_id.symbol + invoice['PaidAmount'][:-2] + ' Paid On ' +
                        invoice['PaymentRequestDateTime'].split('T')[0],
                'ref_nums': invoice['RefNum'],
                'model': str(self._inherit),
            })
            journal_id = False
            payment_acq = self.env['payment.provider'].search(
                [('company_id', '=', self.company_id.id), ('code', '=', 'ebizcharge')])
            if payment_acq and payment_acq.state == 'enabled':
                journal_id = payment_acq.journal_id
            if journal_id:
                ebiz_method = self.env['account.payment.method.line'].search(
                    [('journal_id', '=', journal_id.id), ('payment_method_id.code', '=', 'ebizcharge')], limit=1)
                payment = self.env['account.payment'] \
                    .sudo().with_context(active_ids=odoo_invoice.ids, active_model='account.move',
                                  active_id=odoo_invoice.id) \
                    .create(
                    {'journal_id': journal_id.id,
                     'payment_method_id': ebiz_method.payment_method_id.id,
                     'payment_method_line_id': ebiz_method.id,
                     'amount': float(invoice['PaidAmount']),
                     'token_type': None,
                     'partner_id': self.partner_id.id,
                     'ref': self.name or None,
                     'payment_type': 'inbound'
                     })
                payment.with_context({'pass_validation': True}).action_post()
                self.reconcile()
                odoo_invoice.sync_to_ebiz()
                odoo_invoice.save_payment_link = False
                if odoo_invoice['amount_residual'] <= 0:
                    res = super(AccountMoveInh, odoo_invoice).payment_action_capture()
                    odoo_invoice.mark_as_applied()
                    return res
                else:
                    instance = None
                    if self.partner_id.ebiz_profile_id:
                        instance = self.partner_id.ebiz_profile_id

                    ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
                    ebiz.client.service.MarkEbizWebFormPaymentAsApplied(**{
                        'securityToken': ebiz._generate_security_json(),
                        'paymentInternalId': odoo_invoice.payment_internal_id,
                    })
            else:
                raise UserError('EBizCharge Journal Not Found!')

        except Exception as e:
            raise UserError(e)

    def reconcile(self):
        for invoice_payment in self:
            payments = self.env['account.payment'].sudo().search(
                [('ref', '=', invoice_payment.name), ('company_id', '=', invoice_payment.company_id.id)])
            if not payments and self.env.context.get('payment_id'):
                payments = self.env['account.payment'].sudo().browse(self.env.context.get('payment_id'))
            for payment in payments:
                if not payment.is_reconciled and payment.state == 'posted':
                    payment_ref = self.env['account.move.line'].search(
                        [('move_name', '=', payment.name), ('move_id.company_id', '=', invoice_payment.company_id.id)])
                    if payment_ref:
                        index = len(payment_ref) - 1
                        invoice_payment.js_assign_outstanding_line(payment_ref[index].id)

    def action_capture_reconcile(self, payments):
        for invoice_payment in self:
            for payment in payments:
                if not payment.is_reconciled and payment.state == 'posted':
                    payment_ref = self.env['account.move.line'].search(
                        [('move_name', '=', payment.name), ('move_id.company_id', '=', invoice_payment.company_id.id)])
                    if payment_ref:
                        index = len(payment_ref) - 1
                        invoice_payment.js_assign_outstanding_line(payment_ref[index].id)

    @api.model
    def read(self, fields, load='_classic_read'):
        if len(self) == 1 and (self.ebiz_invoice_status == 'pending' or self.ebiz_payment_link == 'pending') and not self.email_received_payments:
            instance = None
            if self.partner_id.ebiz_profile_id:
                instance = self.partner_id.ebiz_profile_id

            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            today = datetime.now()
            end = today + timedelta(days=1)
            start = today + timedelta(days=-365)
            received_payments = ebiz.client.service.SearchEbizWebFormReceivedPayments(**{
                'securityToken': ebiz._generate_security_json(),
                'fromPaymentRequestDateTime': str(start.date()),
                'toPaymentRequestDateTime': str(end.date()),
                'start': 0,
                'limit': 10000,
            })
            if received_payments:
                invoice_obj = self.env['account.move']
                for invoice in received_payments:
                    odoo_invoice = invoice_obj.search(
                        [('payment_internal_id', '=', invoice['PaymentInternalId'])
                            , '|', ('ebiz_invoice_status', '=', 'pending'), ('ebiz_payment_link', '=', 'pending')])
                    if odoo_invoice:
                        if odoo_invoice.save_payment_link:
                            odoo_invoice.ebiz_payment_link = 'received'
                        else:
                            odoo_invoice.email_received_payments = True

        return super(AccountMoveInh, self).read(fields, load=load)

    def delete_ebiz_invoice(self):
        """
            Niaz Implementation:
            Delete the  pending invoice
        """

        try:
            instance = None
            if self.partner_id.ebiz_profile_id:
                instance = self.partner_id.ebiz_profile_id

            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)

            received_payments = ebiz.client.service.DeleteEbizWebFormPayment(**{
                'securityToken': ebiz._generate_security_json(),
                'paymentInternalId': self.payment_internal_id,
            })

            if received_payments.Status == 'Success' or self.save_payment_link:
                self.ebiz_invoice_status = 'delete'
                self.email_received_payments = False
                self.save_payment_link = False
                self.odoo_payment_link = False
                self.env.cr.commit()
                self.request_amount -= self.last_request_amount

                return message_wizard('Email pay request has been successfully canceled!')

        except Exception as e:
            raise UserError(e)

    def mark_as_applied(self):
        """
            Niaz Implementation:
            Once invoice paid via email, this function mark it as applied and remove from received list.
        """
        try:
            instance = None
            if self.partner_id.ebiz_profile_id:
                instance = self.partner_id.ebiz_profile_id

            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            received_payments = ebiz.client.service.MarkEbizWebFormPaymentAsApplied(**{
                'securityToken': ebiz._generate_security_json(),
                'paymentInternalId': self.payment_internal_id,

            })
            if received_payments.Status == 'Success':
                self.ebiz_invoice_status = 'applied'
                self.is_payment_processed = True
                self.env.cr.commit()
        except Exception as e:
            raise UserError(e)

    def show_pending_ebiz_email(self):
        instance = None
        if self.partner_id.ebiz_profile_id:
            instance = self.partner_id.ebiz_profile_id

        ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
        today = datetime.now()
        end = today + timedelta(days=1)
        start = today + timedelta(days=-365)

        received_payments = ebiz.client.service.SearchEbizWebFormReceivedPayments(**{
            'securityToken': ebiz._generate_security_json(),
            'fromPaymentRequestDateTime': str(start.date()),
            'toPaymentRequestDateTime': str(end.date()),
            'start': 0,
            'limit': 10000,
        })
        if received_payments:
            for invoice in received_payments:
                odoo_invoice = self.env['account.move'].search(
                    [('payment_internal_id', '=', invoice['PaymentInternalId']),
                     ('ebiz_invoice_status', '=', 'pending')])

                if odoo_invoice and odoo_invoice.id == self.id:
                    self.email_received_payments = True
                    text = f"There is an email payment of {float(invoice['PaidAmount'])} received on this {self.name}.\nDo you want to apply?"
                    wizard = self.env['wizard.receive.email.pay'].create({"record_id": self.id,
                                                                          "odoo_invoice": odoo_invoice.id,
                                                                          "text": text})
                    action = self.env.ref('payment_ebizcharge_crm.wizard_received_email_pay').read()[0]
                    action['res_id'] = wizard.id
                    action['context'] = dict(
                        invoice=invoice,
                    )
                    return action
                else:
                    continue

        today = datetime.now()
        end = today + timedelta(days=1)
        start = today + timedelta(days=-7)
        params = {
            'securityToken': ebiz._generate_security_json(),
            'fromPaymentRequestDateTime': str(start.date()),
            'toPaymentRequestDateTime': str(end.date()),
            "filters": {
                "SearchFilter": [{
                    'FieldName': 'InvoiceNumber',
                    'ComparisonOperator': 'eq',
                    'FieldValue': str(self.name)
                }]
            },
            "limit": 1000,
            "start": 0,
        }
        payments = ebiz.client.service.SearchEbizWebFormPendingPayments(**params)
        payment_lines = []
        if not payments:
            return message_wizard('Cannot find any pending payments')
        for payment in payments:
            payment_line = {
                "payment_type": payment['PaymentType'],
                "payment_internal_id": payment['PaymentInternalId'],
                "customer_id": payment['CustomerId'],
                "invoice_number": payment['InvoiceNumber'],
                "invoice_internal_id": payment['InvoiceInternalId'],
                "invoice_date": payment['InvoiceDate'],
                "invoice_due_date": payment['InvoiceDueDate'],
                "po_num": payment['PoNum'],
                "currency_id": self.env.user.currency_id.id,
                "invoice_amount": payment['InvoiceAmount'],
                "amount_due": payment['AmountDue'],
                "email_amount": payment['AmountDue'],
                "auth_code": payment['AuthCode'],
                "ref_num": payment['RefNum'],
                "payment_method": payment['PaymentMethod'],
                "date_paid": datetime.strptime(payment['PaymentRequestDateTime'], '%Y-%m-%dT%H:%M:%S'),
                "paid_amount": payment['PaidAmount'],
                "type_id": payment['TypeId'],
                "email_id": payment['CustomerEmailAddress'],
            }
            payment_lines.append([0, 0, payment_line])
        wiz = self.env['ebiz.pending.payment'].create({})
        wiz.payment_lines = payment_lines
        action = self.env.ref('payment_ebizcharge_crm.action_ebiz_pending_payments_form').read()[0]
        action['res_id'] = wiz.id
        return action

    def _ebiz_check_invoice_update(self, values):
        """
        Kuldeeps implementation 
        def: checks if the after updating the Invoice should we run update sync base on the
        values that are updating.
        @params:
        values : update values params
        """
        update_fields = ["partner_id", "name", "invoice_date", "amount_total", "invoice_date_due",
                         "amount_total", "currency_id", "amount_tax", "user_id", "invoice_line_ids", "amount_residual",
                         "ebiz_invoice_status"]
        for update_field in update_fields:
            if update_field in values:
                return True
        return False

    def email_receipt_ebiz(self):
        """
            Niaz Implementation:
            Email the receipt to customer, if email receipts templates not there in odoo, it will fetch.
            return: wizard to select the receipt template
        """
        try:
            ebiz_obj = self.env['ebiz.charge.api']
            email_obj = self.env['email.receipt']
            instance = self.partner_id.ebiz_profile_id
            ebiz = ebiz_obj.get_ebiz_charge_obj(instance=instance)
            receipts = ebiz.client.service.GetEmailTemplates(**{
                'securityToken': ebiz._generate_security_json(),
            })
            if receipts:
                for template in receipts:
                    odoo_temp = email_obj.search(
                        [('receipt_id', '=', template['TemplateInternalId']), ('instance_id', '=', instance.id)])
                    if not odoo_temp:
                        if template['TemplateTypeId'] == 'TransactionReceiptMerchant' or template[
                            'TemplateTypeId'] == 'TransactionReceiptCustomer':
                            email_obj.create({
                                'name': template['TemplateName'],
                                'receipt_subject': template['TemplateSubject'],
                                'receipt_id': template['TemplateInternalId'],
                                'target': template['TemplateDescription'],
                                'content_type': template['TemplateTypeId'],
                                'instance_id': instance.id,
                            })
            self.env.cr.commit()
            return {'type': 'ir.actions.act_window',
                    'name': _('Email Receipt'),
                    'res_model': 'wizard.email.receipts',
                    'target': 'new',
                    'view_mode': 'form',
                    'view_type': 'form',
                    'context': {
                        'default_partner_ids': [[6, 0, [self.partner_id.id]]],
                        'default_record_id': self.id,
                        'default_ebiz_profile_id': self.partner_id.ebiz_profile_id.id,
                        'default_email_transaction_id': self.receipt_ref_num,
                        'default_model_name': str(self._inherit),
                        'default_email_customer': str(self.partner_id.email if self.partner_id.email else ''),
                        'selection_check': 1,
                    }}
        except Exception as e:
            raise UserError(e)

    def _has_to_be_paid(self):
        """
            Default method is inherited to hide pay button for authorised invoices.
        """
        self.ensure_one()
        transactions = self.transaction_ids.filtered(lambda tx: tx.state in ('authorized', 'done'))
        return bool(
            (
                    self.amount_residual
                    # FIXME someplace we check amount_residual and some other amount_paid < amount_total
                    # what is the correct heuristic to check ?
                    or not transactions
            )
            and self.state == 'posted'
            and transactions.filtered(lambda tx: tx.state not in ('authorized', 'done')) if self.payment_state in (
                'paid') else True and self.payment_state in ('not_paid', 'partial')
                             and self.amount_total and self.move_type == 'out_invoice')

    def view_logs(self):
        return {
            'name': (_('Invoices Logs')),
            'view_type': 'form',
            'res_model': 'invoices.logs',
            'target': 'new',
            'view_id': False,
            'view_mode': 'tree,pivot,form',
            'type': 'ir.actions.act_window',
        }

    def request_email_invoice_bulk(self):
        try:
            instances = self.env['ebizcharge.instance.config'].search([('is_valid_credential', '=', True)])
            ebiz_obj = self.env['ebiz.charge.api']
            ebiz_templates = self.env['email.templates']
            for instance in instances:
                ebiz = ebiz_obj.get_ebiz_charge_obj(instance=instance)
                templates = ebiz.client.service.GetEmailTemplates(**{
                    'securityToken': ebiz._generate_security_json(),
                })
                if templates:
                    for template in templates:
                        odoo_temp = ebiz_templates.search(
                            [('template_id', '=', template['TemplateInternalId']), ('instance_id', '=', instance.id)])
                        if not odoo_temp:
                            ebiz_templates.create({
                                'name': template['TemplateName'],
                                'template_id': template['TemplateInternalId'],
                                'template_subject': template['TemplateSubject'],
                                'template_description': template['TemplateDescription'],
                                'template_type_id': template['TemplateTypeId'],
                                'instance_id': instance.id,
                            })
                        else:
                            odoo_temp.write({
                                'template_subject': template['TemplateSubject'],
                            })
            self.env.cr.commit()

            invoice_ids = [ids.id for ids in self if ids.payment_state != 'paid']
            if not invoice_ids:
                raise UserError('The Selected invoices are already Paid!')

            return {'type': 'ir.actions.act_window',
                    'name': _('Email Pay Request'),
                    'res_model': 'multiple.email.invoice.payments',
                    'target': 'new',
                    'view_mode': 'form',
                    'view_type': 'form',
                    'context': {
                        'default_invoice_ids': [[6, 0, invoice_ids]],
                        'selection_check': 1,
                    },
                    }

        except Exception as e:
            raise UserError(e)

    def button_draft(self):
        ret = super(AccountMoveInh, self).button_draft()
        for rec in self:
            sync = False
            if not rec.payment_state == 'paid' and not rec.done_transaction_ids:
                sync = True
            if (rec.move_type == "out_refund" or rec.move_type == "out_invoice") and rec.ebiz_internal_id and sync:
                instance = None
                if rec.partner_id.ebiz_profile_id:
                    instance = rec.partner_id.ebiz_profile_id

                ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
                ebiz.client.service.UpdateInvoice(**{
                    'securityToken': ebiz._generate_security_json(),
                    'invoice': {
                        "AmountDue": 0,
                        "InvoiceAmount": 0,
                        "NotifyCustomer": False,
                    },
                    'customerId': rec.partner_id.id,
                    'invoiceNumber': rec.name,
                    'invoiceInternalId': rec.ebiz_internal_id
                })
        return ret

    def _invoice_lines_params(self, invoice_lines):
        lines_list = []
        for i, line in enumerate(invoice_lines):
            lines_list.append(self._invoice_line_params(line, i + 1))
        array_of_items = self.client.get_type('ns0:ArrayOfItem')
        return array_of_items(lines_list)

    def _get_customer_address(self, partner):
        name_array = partner.name.split(' ') if partner.name else False
        first_name = name_array[0] if name_array else ''
        if name_array and len(name_array) >= 2:
            last_name = " ".join(name_array[1:])
        else:
            last_name = ""
        address = {
            "FirstName": first_name,
            "LastName": last_name,
            "CompanyName": partner.name if partner.company_type == "company" else partner.parent_id.name or "",
            "Address1": partner.street or "",
            "Address2": partner.street2 or "",
            "City": partner.city or "",
            "State": partner.state_id.name or "",
            "ZipCode": partner.zip or "",
            "Country": partner.country_id.code or "US"
        }
        return address

    def _invoice_line_params(self, line, item_no):
        item = {
            "ItemId": line.product_id.id,
            "Name": line.product_id.name,
            "Description": line.product_id.name,
            "UnitPrice": line.price_unit,
            "Qty": line.quantity,
            "Taxable": False,
            "TaxRate": 0,
            "GrossPrice": 0,
            "WarrantyDiscount": 0,
            "SalesDiscount": 0,
            "UnitOfMeasure": line.product_id.uom_id.name,
            "TotalLineAmount": line.price_subtotal,
            "TotalLineTax": 0,
            "ItemLineNumber": item_no
        }
        return item

    def js_assign_outstanding_line(self, line_id):
        instances = self.env['ebizcharge.instance.config'].search(
            [('is_valid_credential', '=', True)])
        if instances and self.move_type in ['out_refund', 'out_invoice']:
            self.ensure_one()
            lines = self.env['account.move.line'].browse(line_id)
            instance = None
            if self.partner_id.ebiz_profile_id:
                instance = self.partner_id.ebiz_profile_id

            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            from_date = datetime.strftime((self.create_date - timedelta(days=1)), '%Y-%m-%dT%H:%M:%S')
            to_date = datetime.strftime((datetime.now() + timedelta(days=1)), '%Y-%m-%dT%H:%M:%S')
            params = {
                'securityToken': ebiz._generate_security_json(),
                "fromDateTime": from_date,
                "toDateTime": to_date,
                "customerId": self.partner_id.id,
                "limit": 1000,
                "start": 0,
            }
            payments = ebiz.client.service.GetPayments(**params)
            payments = list(filter(lambda x: x['InvoiceNumber'] == lines.payment_id.name, payments or []))
            if payments:
                for payment in payments:
                    resp = ebiz.client.service.MarkPaymentAsApplied(**{
                        'securityToken': ebiz._generate_security_json(),
                        'paymentInternalId': payment['PaymentInternalId'],
                        'invoiceNumber': lines.payment_id.name
                    })
                payment_id = lines.payment_id
                payment_id.action_draft()
                payment_id.action_cancel()
            else:
                result = super(AccountMoveInh, self).js_assign_outstanding_line(line_id)
                self.sync_to_ebiz()
                return result
        else:
            return super(AccountMoveInh, self).js_assign_outstanding_line(line_id)

    def action_generate_odoo_payment_link(self):
        """
        Niaz Implementation:
        Call the wizard, Use to generate odoo payment link.
        return: Wizard
        """
        if self.email_received_payments:
            raise UserError('Invoice is already paid. You cannot generate a payment link!')
        instance = None
        if self.partner_id.ebiz_profile_id:
            instance = self.partner_id.ebiz_profile_id
        else:
            default_instance = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_default', '=', True), ('is_active', '=', True)],
                limit=1)
            if default_instance:
                instance = default_instance

        if self.save_payment_link and self.payment_internal_id:
            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            ebiz.client.service.DeleteEbizWebFormPayment(**{
                'securityToken': ebiz._generate_security_json(),
                'paymentInternalId': self.payment_internal_id,
            })

        if self.ebiz_invoice_status in ['pending']:
            if self and self.save_payment_link and self.is_email_request:
                message_log = 'Email Pay Request sent to: ' + str(self.email_for_pending) + '  has been invalidated'
                self.message_post(body=message_log)
                self.save_payment_link = False
        elif self and self.save_payment_link:
            message_log = 'EBizCharge Payment Link invalidated: ' + str(self.save_payment_link)
            self.message_post(body=message_log)
            self.save_payment_link = False

        self.odoo_payment_link = True
        return {
            'type': 'ir.actions.act_window',
            'name': 'Generate a Payment Link',
            'view_id': self.env.ref('payment.payment_link_wizard_view_form', False).id,
            'target': 'new',
            'res_model': 'payment.link.wizard',
            'view_mode': 'form',
        }

    @api.model
    def get_views(self, views, options=None):
        res = super().get_views(views, options)
        action_id = self.env.ref('account_payment.action_invoice_order_generate_link').id or False
        if action_id:
            for view in res['views'].values():
                if 'toolbar' in view and 'action' in view['toolbar']:
                    for button in view['toolbar']['action']:
                        if action_id and button['id'] == action_id:
                            view['toolbar']['action'].remove(button)
        return res


    def generate_payment_link(self):
        try:
            if len(self) == 0:
                raise UserError('Please select a record first!')
            if len(self.partner_id.ebiz_profile_id)>1:
                raise UserError('Filter the Invoices for a specific unique merchant account. Selection of Invoices for more than one merchant account is not allowed.')

            profile = False
            payment_lines = []

            if self:
                odoo_pay_link = False
                ebiz_pay_link = False
                for inv in self:
                    if inv.move_type != 'out_invoice':
                        raise UserError('Generating an EBizCharge payment link is only available for invoice payments.')
                    if inv.odoo_payment_link:
                        odoo_pay_link = True
                    if inv.save_payment_link:
                        ebiz_pay_link = True
                if odoo_pay_link:
                    text = f"This document has a pending payment link. Proceeding may increase the risk of double payments. Do you want to continue?"
                    wizard = self.env['wizard.receive.email.payment.link'].create({
                                                                                   "is_pay_link": True,
                                                                                   "invoice_ids": [(6,0, self.ids)] ,
                                                                                   "text": text})
                    action = self.env.ref('payment_ebizcharge_crm.wizard_received_email_pay_payment_link').read()[0]
                    action['res_id'] = wizard.id
                    # action['context'] = dict(
                    #     invoice=self.id,
                    # )
                    return action

                elif ebiz_pay_link:
                    # raise UserError(str(ebiz_pay_link))
                    text = f"This document has an existing payment link. Proceeding will invalidate the existing link. Do you want to continue?"
                    wizard = self.env['wizard.receive.email.payment.link'].create({

                                                                                   "is_pay_link": True,
                                                                                   "invoice_ids": [(6, 0, self.ids)],
                                                                                   "text": text})
                    action = self.env.ref('payment_ebizcharge_crm.wizard_received_email_pay_payment_link').read()[0]
                    action['res_id'] = wizard.id
                    # action['context'] = dict(
                    #     invoice=self.id,
                    # )
                    return action
                else:
                    for inv in self:
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
        except Exception as e:
            raise ValidationError(e)






    def aagenerate_payment_link(self):
        """
        Niaz Implementation:
        Call the wizard, use to send email invoice to customer, fetch the email templates incase not present before
        return: Wizard
        """
        try:
            if self.move_type != 'out_invoice':
                raise UserError('Generating an EBizCharge payment link is only available for invoice payments.')
            if self.payment_state == "paid":
                raise UserError('The invoice is already paid.')

            if self.amount_residual <= 0:
                raise UserError('The value of the payment amount must be positive.')

            if self.email_received_payments:
                raise UserError('Invoice is already paid. You cannot generate a payment link!')

            # if self.ebiz_invoice_status in ['pending']:
            #     raise UserError(
            #         "Email pay link is already generated. if you want to generate payment link then cancel email payment request link!")
            #
            # if self.odoo_payment_link:
            #     raise UserError('Payment link is already generated.')

            if self.state != 'posted':
                self.action_post()

            if not self.ebiz_internal_id:
                self.sync_to_ebiz()

            if self.save_payment_link:
                text = f"This document has an existing payment link. Proceeding will invalidate the existing link. Do you want to continue?"
                wizard = self.env['wizard.receive.email.payment.link'].create({"record_id": self.id,
                                                                               "odoo_invoice": self.id,
                                                                               "text": text})
                action = self.env.ref('payment_ebizcharge_crm.wizard_received_email_pay_payment_link').read()[0]
                action['res_id'] = wizard.id
                action['context'] = dict(
                    invoice=self,
                )
                return action
                # return {'type': 'ir.actions.act_window',
                #         'name': _('Copy Payment Link'),
                #         'res_model': 'ebiz.payment.link.copy',
                #         'target': 'new',
                #         'view_mode': 'form',
                #         'view_type': 'form',
                #         'context': {
                #             'default_link': self.save_payment_link,
                #         },
                #         }
            else:

                return {'type': 'ir.actions.act_window',
                        'name': _('Generate Payment Link'),
                        'res_model': 'ebiz.payment.link.wizard',
                        'target': 'new',
                        'view_mode': 'form',
                        'view_type': 'form',
                        'context': {
                            'default_ebiz_profile_id': self.partner_id.ebiz_profile_id.id,
                            'active_id': self.id,
                            'active_model': 'account.move',
                        },
                        }

        except Exception as e:
            raise ValidationError(e)


    def action_view_payment_transactions(self):
        action = self.env['ir.actions.act_window']._for_xml_id('payment.action_payment_transaction')
        transactions = self.transaction_ids
        count = len(self.transaction_ids.ids)
        if self.transaction_ids.sale_order_ids:
            other = self.transaction_ids.sale_order_ids.done_transaction_ids.filtered(lambda i:i.id not in self.transaction_ids.ids)
            count = len(transactions.ids) + len(other.ids)
            transactions += other
        if count == 1:
            action['view_mode'] = 'form'
            action['res_id'] = self.transaction_ids.id
            action['views'] = []
        else:
            action['domain'] = [('id', 'in', transactions.ids)]

        return action


class AccountReceipts(models.Model):
    _name = 'account.move.receipts'
    _description = "Account Move Receipts"

    invoice_id = fields.Char(string='Invoice ID')
    name = fields.Char(string='Name')
    ref_nums = fields.Char(string='Ref Num')
    model = fields.Char(string='Model Name')




class IrModuleModule(models.Model):
    _inherit = 'ir.module.module'


class IrActionWindow(models.Model):
    _inherit = 'ir.actions.act_window'


class IrActionWindowView(models.Model):
    _inherit = 'ir.actions.act_window.view'


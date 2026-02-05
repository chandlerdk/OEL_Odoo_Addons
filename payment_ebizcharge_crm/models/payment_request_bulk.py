# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
import logging
from datetime import datetime, timedelta
from .ebiz_charge import message_wizard

_logger = logging.getLogger(__name__)


class PaymentRequestBulkPayment(models.Model):
    _name = 'payment.request.bulk.email'
    _description = "Payment Request Bulk Email"

    def get_default_company(self):
        companies = self.env['ebizcharge.instance.config'].search(
            [('is_active', '=', True), '|', ('company_ids', '=', False), ('company_ids', 'in',
                self._context.get('allowed_company_ids'))]).mapped(
            'company_ids').ids
        return companies

    company_ids = fields.Many2many('res.company', compute='compute_company')
    start_date = fields.Date(string='From Date')
    end_date = fields.Date(string='To Date')
    name = fields.Char(string='Email Pay for Invoices', default='Email Pay for Invoices')
    partner_id = fields.Many2one('res.partner', string='Select Customer',
                                      domain="[('ebiz_internal_id', '!=', False), ('ebiz_profile_id', '=', ebiz_profile_id)]")
    transaction_history_line = fields.One2many('sync.request.payments.bulk', 'sync_transaction_id',
                                               copy=True)
    transaction_history_line_pending = fields.One2many('sync.request.payments.bulk.pending',
                                                       'sync_transaction_id_pending', copy=True)
    transaction_history_line_received = fields.One2many('sync.request.payments.bulk.received',
                                                        'sync_transaction_id_received', copy=True)
    add_filter = fields.Boolean(string='Filters')
    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config', string='EBizCharge Profile')
    is_reopened = fields.Boolean()

    @api.depends('ebiz_profile_id')
    def compute_company(self):
        self.company_ids = self._context.get('allowed_company_ids')

    def _compute_display_name(self):
        self.display_name = 'Email Pay for Invoices'

    def js_flush_customer(self, ecom_side=None):
        rec = self.env['payment.request.bulk.email'].search([])
        if rec:
            # rec.ebiz_profile_id = False
            rec.partner_id = False
            rec.is_reopened = False

    @api.model
    def read(self, fields=None, load='_classic_read'):
        if self.ids and not self.is_reopened:
            self.create_default_records()
            self.is_reopened = True
        result = super(PaymentRequestBulkPayment, self).read(fields, load=load)
        return result

    def create_default_records(self):
        list_of_invoices = [(6, 0, 0)]
        list_of_pending = [(6, 0, 0)]
        list_of_received = [(6, 0, 0)]
        profile_obj = self.env['ebizcharge.instance.config']
        profile = int(profile_obj.get_upload_instance(active_model='payment.request.bulk.email', active_id=self))
        if profile:
            self.ebiz_profile_id = profile
            self.start_date = self.ebiz_profile_id._default_get_start()
            self.end_date = self.ebiz_profile_id._default_get_end_date()
        received_payments = []
        invoices_list = []
        pending_invoices_list = []
        if self.ebiz_profile_id:
            invoices = self.default_invoice(self.start_date, self.end_date, self.ebiz_profile_id)
            pending_invoices = self.default_pending_invoice(self.start_date, self.end_date, self.ebiz_profile_id)
            payments = self.default_received_invoices(self.start_date, self.end_date, self.ebiz_profile_id)
            if payments:
                received_payments += payments
            if invoices:
                invoices_list += invoices.ids
            if pending_invoices:
                pending_invoices_list += pending_invoices.ids

        if invoices_list:
            invoices = self.env['account.move'].browse(invoices_list)
            for invoice in invoices:
                partner = invoice.partner_id
                dict1 = (0, 0, {
                    'name': invoice['name'],
                    'customer_name': partner.id,
                    'customer_id': str(partner.id),
                    'email_id': partner.email,
                    'invoice_id': invoice.id,
                    'invoice_date': invoice.date,
                    'sales_person': self.env.user.id,
                    'amount': invoice.amount_total,
                    "currency_id": invoice.currency_id.id,
                    'amount_due': invoice.amount_residual_signed,
                    'tax': invoice.amount_untaxed_signed,
                    'invoice_due_date': invoice.invoice_date_due,
                    'sync_transaction_id': self.id,
                })
                list_of_invoices.append(dict1)

        if pending_invoices_list:
            pending_invoices = self.env['account.move'].browse(pending_invoices_list)
            for invoice in pending_invoices:
                check = True
                partner = invoice.partner_id
                if received_payments:
                    for invoice_check in received_payments:
                        if invoice.payment_internal_id == invoice_check['PaymentInternalId']:
                            check = False

                if check:
                    date_check = False
                    if invoice.date_time_sent_for_email:
                        date_check = 'due in 3 days' if (datetime.now() - invoice.date_time_sent_for_email).days <= 3 else '3 days overdue'
                    dict2 = (0, 0, {
                        'name': invoice['name'],
                        'customer_name': partner.id,
                        'customer_id': str(partner.id),
                        'invoice_id': invoice.id,
                        'invoice_date': invoice.date,
                        'email_id': invoice.email_for_pending if invoice.email_for_pending else invoice.partner_id.email,
                        'sales_person': self.env.user.id,
                        'amount': invoice.amount_total,
                        "currency_id": invoice.currency_id.id,
                        'amount_due': invoice.amount_residual_signed,
                        'tax': invoice.amount_untaxed_signed,
                        'date_and_time_Sent': invoice.date_time_sent_for_email or None,
                        'over_due_status': date_check if date_check else None,
                        'invoice_due_date': invoice.invoice_date_due,
                        'sync_transaction_id_pending': self.id,
                        'ebiz_status': 'Pending' if invoice.ebiz_invoice_status == 'pending' else invoice.ebiz_invoice_status,
                        'email_requested_amount': invoice.email_requested_amount,
                        'no_of_times_sent': invoice.no_of_times_sent,
                    })
                    list_of_pending.append(dict2)

        if received_payments:
            for portal_invoice in received_payments:
                invoice = self.env['account.move'].search(
                    [('payment_internal_id', '=', portal_invoice['PaymentInternalId']) ])
                if invoice:
                    partner = invoice.partner_id
                    dict3 = (0, 0, {
                        'name': invoice['name'],
                        'customer_name': partner.id,
                        'customer_id': str(partner.id),
                        'invoice_id': invoice.id,
                        'invoice_date': invoice.date,
                        "currency_id": invoice.currency_id.id,
                        'sales_person': self.env.user.id,
                        'amount': float(invoice.amount_total),
                        'amount_due': float(invoice.amount_residual_signed),
                        'paid_amount': float(portal_invoice['PaidAmount']),
                        'email_id': portal_invoice['CustomerEmailAddress'],
                        'ref_num': portal_invoice['RefNum'],
                        'payment_request_date_time': datetime.strptime(portal_invoice['PaymentRequestDateTime'],
                                                                       '%Y-%m-%dT%H:%M:%S'),
                        'payment_method': f"{portal_invoice['PaymentMethod']} ending in {portal_invoice['Last4']}",
                        'sync_transaction_id_received': self.id,
                    })
                    list_of_received.append(dict3)

        self.update({
            'transaction_history_line': list_of_invoices,
            'transaction_history_line_pending': list_of_pending,
            'transaction_history_line_received': list_of_received,
        })

    def default_invoice(self, start_date, end_date, instance):
        return self.env['account.move'].search([('payment_state', '!=', 'paid'),
                                                ('state', '=', 'posted'),
                                                ('odoo_payment_link', '=', False),
                                                ('save_payment_link', '=', False),
                                                ('partner_id.ebiz_profile_id', '=', instance.id),
                                                ('ebiz_invoice_status', '!=', 'pending'),
                                                ('date', '>=', start_date),
                                                ('date', '<=', end_date),
                                                ('amount_residual', '>', 0),
                                                ('move_type', 'not in', ['out_refund', 'in_invoice'])])

    def default_pending_invoice(self, start_date, end_date, instance):
        return self.env['account.move'].search([('payment_state', '!=', 'paid'),
                                                ('state', '=', 'posted'),
                                                ('partner_id.ebiz_profile_id', '=', instance.id),
                                                ('ebiz_invoice_status', '=', 'pending'),
                                                ('ebiz_invoice_status', '!=', 'delete'),
                                                ('date', '>=', start_date),
                                                ('date', '<=', end_date),
                                                ('amount_residual', '>', 0),
                                                ('move_type', '!=', 'out_refund')])

    def default_received_invoices(self, start_date, end_date, instance):
        ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
        dicti = {
            'securityToken': ebiz._generate_security_json(),
            'filters': {'SearchFilter': []},
            'fromPaymentRequestDateTime': start_date,
            'toPaymentRequestDateTime': end_date,
            'start': 0,
            'limit': 100000,
        }
        return ebiz.client.service.SearchEbizWebFormReceivedPayments(**dicti)

    def search_transaction(self):
        try:
            if self.start_date and self.end_date:
                if not self.start_date <= self.end_date:
                    self.env["sync.request.payments.bulk"].search([]).unlink()
                    self.env["sync.request.payments.bulk.pending"].search([]).unlink()
                    self.env["sync.request.payments.bulk.received"].search([]).unlink()
                    return message_wizard('From Date should be lower than the To date!', 'Invalid Date')

            received_payments = []
            ebiz_obj = self.env['ebiz.charge.api']
            if not self.ebiz_profile_id:
                instances = self.env['ebizcharge.instance.config'].search([('is_valid_credential', '=', True), ('is_active', '=', True)])
                for instance in instances:
                    ebiz = ebiz_obj.get_ebiz_charge_obj(instance=instance)
                    dicti = {
                        'securityToken': ebiz._generate_security_json(),
                        'filters': {'SearchFilter': []},
                        'start': 0,
                        'limit': 100000,
                    }

                    if self.partner_id and self.start_date and self.end_date:
                        dicti['fromPaymentRequestDateTime'] = self.start_date
                        dicti['toPaymentRequestDateTime'] = self.end_date
                        dicti['customerId'] = self.partner_id.id

                    elif self.start_date and self.end_date:
                        dicti['fromPaymentRequestDateTime'] = self.start_date
                        dicti['toPaymentRequestDateTime'] = self.end_date

                    elif self.partner_id:
                        today = datetime.now()
                        end = today + timedelta(days=1)
                        start = today + timedelta(days=-365)

                        dicti['fromPaymentRequestDateTime'] = str(start.date())
                        dicti['toPaymentRequestDateTime'] = str(end.date())
                        dicti['customerId'] = self.partner_id.id
                    rec_pay = ebiz.client.service.SearchEbizWebFormReceivedPayments(**dicti)
                    if rec_pay:
                        received_payments += rec_pay
            else:
                instance = None
                if self.ebiz_profile_id:
                    instance = self.ebiz_profile_id
                ebiz = ebiz_obj.get_ebiz_charge_obj(instance=instance)
                dicti = {
                    'securityToken': ebiz._generate_security_json(),
                    'filters': {'SearchFilter': []},
                    'start': 0,
                    'limit': 100000,
                }

                if self.partner_id and self.start_date and self.end_date:
                    dicti['fromPaymentRequestDateTime'] = self.start_date
                    dicti['toPaymentRequestDateTime'] = self.end_date
                    dicti['customerId'] = self.partner_id.id

                elif self.start_date and self.end_date:
                    dicti['fromPaymentRequestDateTime'] = self.start_date
                    dicti['toPaymentRequestDateTime'] = self.end_date

                elif self.partner_id:
                    today = datetime.now()
                    end = today + timedelta(days=1)
                    start = today + timedelta(days=-365)

                    dicti['fromPaymentRequestDateTime'] = str(start.date())
                    dicti['toPaymentRequestDateTime'] = str(end.date())
                    dicti['customerId'] = self.partner_id.id
                rec_pay = ebiz.client.service.SearchEbizWebFormReceivedPayments(**dicti)
                if rec_pay:
                    received_payments += rec_pay

            invoices_filters = [('payment_state', '!=', 'paid'),
                                ('state', '=', 'posted'),
                                ('ebiz_invoice_status', '!=', 'pending'),
                                ('amount_residual', '>', 0),
                                ('save_payment_link', '=', False),
                                ('move_type', '!=', 'out_refund'), ]

            if self.end_date:
                invoices_filters.append(('date', '<=', self.end_date))

            if self.start_date:
                invoices_filters.append(('date', '>=', self.start_date))

            if self.partner_id:
                invoices_filters.append(('partner_id', '=', self.partner_id.id))
            if self.ebiz_profile_id:
                invoices_filters.append(('partner_id.ebiz_profile_id', '=', self.ebiz_profile_id.id))
            else:
                invoices_filters.append(('partner_id.ebiz_profile_id', 'in', instances.ids))

            invoices = self.env['account.move'].search(invoices_filters)

            pending_filters = [('payment_state', '!=', 'paid'),
                               ('state', '=', 'posted'),
                               ('ebiz_invoice_status', '=', 'pending'),
                               ('ebiz_invoice_status', '!=', 'delete'),
                               ('amount_residual', '>', 0),
                               ('move_type', '!=', 'out_refund')]

            if self.end_date:
                pending_filters.append(('date', '<=', self.end_date))

            if self.start_date:
                pending_filters.append(('date', '>=', self.start_date))

            if self.partner_id:
                pending_filters.append(('partner_id', '=', self.partner_id.id))
            if self.ebiz_profile_id:
                pending_filters.append(('partner_id.ebiz_profile_id', '=', self.ebiz_profile_id.id))
            else:
                pending_filters.append(('partner_id.ebiz_profile_id', 'in', instances.ids))

            pending_invoices = self.env['account.move'].search(pending_filters)

            if invoices or pending_invoices or received_payments:
                self.env["sync.request.payments.bulk"].search([]).unlink()
                list_of_trans = []
                if invoices:
                    for invoice in invoices:
                        partner = invoice.partner_id
                        dict1 = {
                            'name': invoice['name'],
                            'customer_name': partner.id,
                            'customer_id': partner.id,
                            'invoice_id': invoice.id,
                            'invoice_date': invoice.date,
                            'sales_person': self.env.user.id,
                            'amount': invoice.amount_total,
                            "currency_id": invoice.currency_id.id,
                            'amount_due': invoice.amount_residual_signed,
                            'tax': invoice.amount_untaxed_signed,
                            'invoice_due_date': invoice.invoice_date_due,
                            'sync_transaction_id': self.id,
                        }
                        list_of_trans.append(dict1)
                    self.env['sync.request.payments.bulk'].create(list_of_trans)

                if pending_invoices:
                    self.env["sync.request.payments.bulk.pending"].search([]).unlink()
                    list_of_pending_trans = []
                    for invoice in pending_invoices:
                        check = True
                        partner = invoice.partner_id
                        if received_payments:
                            for invoice_check in received_payments:
                                if invoice.payment_internal_id == invoice_check['PaymentInternalId']:
                                    check = False
                        if check:
                            date_check = False
                            if invoice.date_time_sent_for_email:
                                date_check = 'due in 3 days' if (datetime.now() - invoice.date_time_sent_for_email).days <= 3 else '3 days overdue'
                            dict2 = {
                                'name': invoice['name'],
                                'customer_name': partner.id,
                                'customer_id': partner.id,
                                'invoice_id': invoice.id,
                                'invoice_date': invoice.date,
                                'email_id': invoice.email_for_pending if invoice.email_for_pending else invoice.partner_id.email,
                                'sales_person': self.env.user.id,
                                'amount': invoice.amount_total,
                                "currency_id": invoice.currency_id.id,
                                'amount_due': invoice.amount_residual_signed,
                                'tax': invoice.amount_untaxed_signed,
                                'date_and_time_Sent': invoice.date_time_sent_for_email or None,
                                'over_due_status': date_check if date_check else None,
                                'invoice_due_date': invoice.invoice_date_due,
                                'sync_transaction_id_pending': self.id,
                                'ebiz_status': 'Pending' if invoice.ebiz_invoice_status == 'pending' else invoice.ebiz_invoice_status,
                                'email_requested_amount': invoice.email_requested_amount,
                                'no_of_times_sent': invoice.no_of_times_sent,
                            }
                            list_of_pending_trans.append(dict2)
                    self.env['sync.request.payments.bulk.pending'].create(list_of_pending_trans)

                else:
                    self.env["sync.request.payments.bulk.pending"].search([]).unlink()

                if received_payments:
                    self.env["sync.request.payments.bulk.received"].search([]).unlink()
                    list_of_received_trans = []
                    for portal_invoice in received_payments:
                        invoice = self.env['account.move'].search(
                            [('payment_internal_id', '=', portal_invoice['PaymentInternalId'])])
                        if invoice:
                            partner = invoice.partner_id
                            dict3 = {
                                'name': invoice['name'],
                                'customer_name': partner.id,
                                'customer_id': partner.id,
                                'invoice_id': invoice.id,
                                'invoice_date': invoice.date,
                                "currency_id": invoice.currency_id.id,
                                'sales_person': self.env.user.id,
                                'amount': invoice.amount_total,
                                'amount_due': invoice.amount_residual_signed,
                                'paid_amount': portal_invoice['PaidAmount'],
                                'email_id': portal_invoice['CustomerEmailAddress'],
                                'ref_num': portal_invoice['RefNum'],
                                'payment_request_date_time': datetime.strptime(portal_invoice['PaymentRequestDateTime'],
                                                                               '%Y-%m-%dT%H:%M:%S'),
                                'payment_method': f"{portal_invoice['PaymentMethod']} ending in {portal_invoice['Last4']}",
                                'sync_transaction_id_received': self.id,
                            }
                            list_of_received_trans.append(dict3)
                    self.env['sync.request.payments.bulk.received'].create(list_of_received_trans)
                else:
                    self.env["sync.request.payments.bulk.received"].search([]).unlink()
            else:
                self.env["sync.request.payments.bulk"].search([]).unlink()
                self.env["sync.request.payments.bulk.pending"].search([]).unlink()
                self.env["sync.request.payments.bulk.received"].search([]).unlink()

        except Exception as e:
            raise UserError(e)

    def process_invoices(self, *args, **kwargs):
        """
            Niaz Implementation:
            Email the receipt to customer, if email receipts templates not there in odoo, it will fetch.
            return: wizard to select the receipt template
        """
        try:
            if len(kwargs['values']) == 0:
                raise UserError('Please select a record first!')

            payment_lines = []
            account_obj = self.env['account.move']
            for record in kwargs['values']:
                search_invoice = account_obj.search([('id', '=', record['invoice_id'])], limit=1)
                payment_line = {
                    "name": search_invoice.name,
                    "customer_name": search_invoice.partner_id.id,
                    "amount_due": search_invoice.amount_residual_signed,
                    "invoice_id": search_invoice.id,
                    "currency_id": self.env.user.currency_id.id,
                    "email_id": search_invoice.partner_id.email,
                    "ebiz_profile_id": search_invoice.partner_id.ebiz_profile_id.id,
                }
                payment_lines.append([0, 0, payment_line])
            wiz = self.env['ebiz.request.payment.bulk'].with_context(partner=search_invoice.partner_id.id, profile=search_invoice.partner_id.ebiz_profile_id.id).create(
                {'payment_lines': payment_lines, 'ebiz_profile_id': search_invoice.partner_id.ebiz_profile_id.id})
            action = self.env.ref('payment_ebizcharge_crm.action_ebiz_request_payments_bulk').read()[0]
            action['res_id'] = wiz.id
            action['context'] = self.env.context
            return action
        except Exception as e:
            raise UserError(e)

    def resend_email(self, *args, **kwargs):
        try:
            if len(kwargs['values']) == 0:
                raise UserError('Please select a record first!')

            resp_lines = []
            success = 0
            failed = 0
            total_count = len(kwargs['values'])
            account_obj = self.env['account.move']
            ebiz_obj = self.env['ebiz.charge.api']
            for invoice in kwargs['values']:
                odoo_invoice = account_obj.search([('id', '=', invoice['invoice_id'])])
                resp_line = {}
                resp_line['customer_name'] = resp_line['customer_id'] = odoo_invoice.partner_id.id
                resp_line['number'] = odoo_invoice.id
                instance = None
                if odoo_invoice.partner_id.ebiz_profile_id:
                    instance = odoo_invoice.partner_id.ebiz_profile_id

                ebiz = ebiz_obj.get_ebiz_charge_obj(instance=instance)
                form_url = ebiz.client.service.ResendEbizWebFormEmail(**{
                    'securityToken': ebiz._generate_security_json(),
                    'paymentInternalId': odoo_invoice.payment_internal_id,
                })
                odoo_invoice.no_of_times_sent += 1
                if self:
                    pending_record = self.transaction_history_line_pending.filtered(lambda r: r.id == invoice['id'])
                    pending_record.no_of_times_sent = odoo_invoice.no_of_times_sent
                resp_line['status'] = 'Success'
                success += 1
                resp_lines.append([0, 0, resp_line])
            wizard = self.env['wizard.email.pay.message'].create(
                {'name': 'resend_email_pay', 'lines_ids': resp_lines,
                 'success_count': success,
                 'failed_count': failed,
                 'total': total_count})

            return {'type': 'ir.actions.act_window',
                    'name': _('Email Pay for Invoices'),
                    'res_model': 'wizard.email.pay.message',
                    'target': 'new',
                    'res_id': wizard.id,
                    'view_mode': 'form',
                    'views': [[False, 'form']],
                    'context': self._context,
                    }

        except Exception as e:
            if e.args[0] == 'Error: Object reference not set to an instance of an object.':
                raise UserError('This Invoice Either Paid Or Deleted!')
            raise UserError(e)

    def delete_invoice(self, *args, **kwargs):
        try:
            if len(kwargs['values']) == 0:
                raise UserError('Please select a record first!')

            text = f"Are you sure you want to remove {len(kwargs['values'])} request(s) from Pending Requests?"
            wizard = self.env['wizard.delete.email.pay'].create({"record_id": self.id,
                                                                 "record_model": self._name,
                                                                 "text": text})
            action = self.env.ref('payment_ebizcharge_crm.wizard_delete_email_pay_action').read()[0]
            action['res_id'] = wizard.id
            action['context'] = dict(
                self.env.context,
                kwargs_values=kwargs['values'],
                pending_received='Pending Requests'
            )
            return action

        except Exception as e:
            raise UserError(e)

    def delete_invoice_received(self, *args, **kwargs):
        try:
            if len(kwargs['values']) == 0:
                raise UserError('Please select a record first!')

            text = f"Are you sure you want to remove {len(kwargs['values'])} payment(s) from Received Email Payments?"
            wizard = self.env['wizard.delete.email.pay'].create({"record_id": self.id,
                                                                 "record_model": self._name, "text": text})
            action = self.env.ref('payment_ebizcharge_crm.wizard_delete_email_pay_action').read()[0]
            action['res_id'] = wizard.id
            action['context'] = dict(
                self.env.context,
                kwargs_values=kwargs['values'],
                pending_received='Received Email Payments'
            )
            return action
        except Exception as e:
            raise UserError(e)

    def mark_applied(self, *args, **kwargs):
        try:
            if len(kwargs['values']) == 0:
                raise UserError('Please select a record first!')

            for invoice in kwargs['values']:
                odoo_invoice = self.env['account.move'].search([('id', '=', invoice['invoice_id'])])
                if odoo_invoice:
                    if odoo_invoice.state == 'draft':
                        odoo_invoice.action_post()

                    if odoo_invoice['amount_residual'] - float(invoice['paid_amount']) > 0:
                        odoo_invoice.write({
                            'ebiz_invoice_status': 'partially_received',
                            'receipt_ref_num': invoice['ref_num'],
                            'save_payment_link': False,
                            'is_payment_processed': True,
                            'request_amount': 0,
                            'last_request_amount': 0,
                            'ebiz_payment_link': 'applied'
                        })
                    else:
                        odoo_invoice.write({
                            'ebiz_invoice_status': 'received',
                            'receipt_ref_num': invoice['ref_num'],
                            'save_payment_link': False,
                            'is_payment_processed': True,
                            'request_amount': 0,
                            'last_request_amount': 0,
                            'ebiz_payment_link': 'applied'
                        })

                    receipt_record = self.env['account.move.receipts'].create({
                        'invoice_id': odoo_invoice.id,
                        'name': self.env.user.currency_id.symbol + str(invoice['paid_amount']) + ' Paid On ' +
                                invoice['payment_request_date_time'].split('T')[0],
                        'ref_nums': invoice['ref_num'],
                        'model': '[\'account.move\', \'ebiz.charge.api\']',
                    })
                    journal_id = False
                    payment_acq = self.env['payment.provider'].search(
                        [('company_id', '=', odoo_invoice.company_id.id), ('code', '=', 'ebizcharge')])
                    if payment_acq and payment_acq.state == 'enabled':
                        journal_id = payment_acq.journal_id

                    if journal_id:
                        ebiz_method = self.env['account.payment.method.line'].search(
                            [('journal_id', '=', payment_acq.journal_id.id),
                             ('payment_method_id.code', '=', 'ebizcharge')], limit=1)
                        if odoo_invoice.state != 'cancel':
                            payment = self.env['account.payment'].sudo().with_context(active_ids=odoo_invoice.ids, active_model='account.move',
                                              active_id=odoo_invoice.id) \
                                .create(
                                {'journal_id': journal_id.id,
                                 'payment_method_id': ebiz_method.payment_method_id.id,
                                 'payment_method_line_id': ebiz_method.id,
                                 'amount': float(invoice['paid_amount']),
                                 'token_type': None,
                                 'partner_id': int(invoice['customer_id']),
                                 'ref': invoice['name'] or None,
                                 'payment_type': 'inbound'
                                 })
                            payment.with_context({'pass_validation': True}).action_post()
                            odoo_invoice.reconcile()
                            odoo_invoice.sync_to_ebiz()
                        else:
                            payment = self.env['account.payment'].sudo().create(
                                {'journal_id': journal_id.id,
                                 'payment_method_id': ebiz_method.payment_method_id.id,
                                 'payment_method_line_id': ebiz_method.id,
                                 'amount': float(invoice['paid_amount']),
                                 'token_type': None,
                                 'partner_id': int(invoice['customer_id']),
                                 'payment_type': 'inbound'
                                 })
                            payment.with_context({'pass_validation': True}).action_post()
                        if odoo_invoice['amount_residual'] <= 0 and odoo_invoice.state != 'cancel':
                            odoo_invoice.mark_as_applied()
                        else:
                            instance = None
                            if odoo_invoice.partner_id.ebiz_profile_id:
                                instance = odoo_invoice.partner_id.ebiz_profile_id

                            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
                            ebiz.client.service.MarkEbizWebFormPaymentAsApplied(**{
                                'securityToken': ebiz._generate_security_json(),
                                'paymentInternalId': odoo_invoice.payment_internal_id,

                            })
                            received_payments = self.env['payment.request.bulk.email'].search([])
                            for payment in received_payments:
                                if payment.transaction_history_line_received:
                                    for pending in payment.transaction_history_line_received:
                                        if pending.id == invoice['invoice_id']:
                                            payment.transaction_history_line_received = [[2, invoice['invoice_id']]]
                    else:
                        raise UserError('EBizCharge Journal Not Found!')

            self.search_transaction()
            return message_wizard('Received payment(s) applied successfully!')

        except Exception as e:
            raise ValidationError(e)


class ListSyncBulkInvoices(models.Model):
    _name = 'sync.request.payments.bulk'
    _order = 'date_time asc'
    _description = "Sync Request Payment Bulk"

    sync_date = fields.Datetime('Execution Date/Time', required=True, default=fields.Datetime.now)
    sync_transaction_id = fields.Many2one('payment.request.bulk.email', string='Partner Reference', required=True,
                                          ondelete='cascade', index=True, copy=False)
    name = fields.Char(string='Number')
    customer_name = fields.Many2one('res.partner', string='Customer')
    customer_id = fields.Char(string='Customer ID')
    invoice_id = fields.Char(string='Invoice ID')
    account_holder = fields.Char(string='Account Holder')
    date_time = fields.Datetime(string='Date Time')
    currency_id = fields.Many2one('res.currency', string='Company Currency')
    amount = fields.Float(string='Invoice Total')
    amount_due = fields.Float(string='Amount Due')
    tax = fields.Float(string='Tax Excluded')
    card_no = fields.Char(string='Card Number')
    status = fields.Char(string='Status')
    email_id = fields.Char(string='Email', related='customer_name.email')
    invoice_date = fields.Date(string='Invoice Date')
    invoice_due_date = fields.Date(string='Due Date')
    sales_person = fields.Many2one('res.users', string='Sales Person')
    payment_method = fields.Char('Payment Method')
    default_card_id = fields.Integer(string='Default Credit Card ID')


class ListPendingBulkInvoices(models.Model):
    _name = 'sync.request.payments.bulk.pending'
    _order = 'date_time asc'
    _description = "Sync Request Payments Bulk Pending"

    sync_date = fields.Datetime('Execution Date/Time', required=True, default=fields.Datetime.now)
    sync_transaction_id_pending = fields.Many2one('payment.request.bulk.email', string='Partner Reference',
                                                  required=True, ondelete='cascade', index=True, copy=False)
    name = fields.Char(string='Number')
    customer_name = fields.Many2one('res.partner', string='Customer')
    customer_id = fields.Char(string='Customer ID')
    invoice_id = fields.Char(string='Invoice ID')
    currency_id = fields.Many2one('res.currency', string='Company Currency')
    account_holder = fields.Char(string='Account Holder')
    date_time = fields.Datetime(string='Date Time')
    amount = fields.Float(string='Invoice Total')
    amount_due = fields.Float(string='Amount Due')
    tax = fields.Float(string='Tax Excluded')
    card_no = fields.Char(string='Card Number')
    status = fields.Char(string='Status')
    email_id = fields.Char(string='Email')
    invoice_date = fields.Date(string='Invoice Date')
    invoice_due_date = fields.Date(string='Due Date')
    sales_person = fields.Many2one('res.users', string='Sales Person')
    payment_method = fields.Char('Payment Method')
    ebiz_status = fields.Char('Ebiz Status')
    over_due_status = fields.Char('Overdue Status')
    date_and_time_Sent = fields.Datetime('Org. Date & Time Sent')
    email_requested_amount = fields.Float('Requested Amount')
    no_of_times_sent = fields.Integer("# of Times Sent")
    default_card_id = fields.Integer(string='Default Credit Card ID')


class ListReceivedBulkInvoices(models.Model):
    _name = 'sync.request.payments.bulk.received'
    _order = 'date_time asc'
    _description = "Sync Request Payments Bulk Received"

    sync_date = fields.Datetime('Execution Date/Time', required=True, default=fields.Datetime.now)
    sync_transaction_id_received = fields.Many2one('payment.request.bulk.email', string='Partner Reference',
                                                   required=True,
                                                   ondelete='cascade', index=True, copy=False)
    name = fields.Char(string='Number')
    customer_name = fields.Many2one('res.partner', string='Customer')
    customer_id = fields.Char(string='Customer ID')
    invoice_id = fields.Char(string='Invoice ID')
    account_holder = fields.Char(string='Account Holder')
    currency_id = fields.Many2one('res.currency', string='Company Currency')
    date_time = fields.Datetime(string='Date Time')
    amount = fields.Float(string='Invoice Total')
    amount_due = fields.Float(string='Amount Due')
    tax = fields.Float(string='Tax Excluded')
    card_no = fields.Char(string='Card Number')
    status = fields.Char(string='Status')
    email_id = fields.Char(string='Email ID')
    invoice_date = fields.Date(string='Invoice Date')
    sales_person = fields.Many2one('res.users', string='Sales Person')
    payment_method = fields.Char('Payment Method')
    paid_amount = fields.Float('Amount Paid')
    ref_num = fields.Char('Reference Number')
    payment_request_date_time = fields.Datetime('Date & Time Paid')

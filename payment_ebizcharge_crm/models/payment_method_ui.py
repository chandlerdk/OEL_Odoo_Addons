# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging
from datetime import datetime, timedelta
from .ebiz_charge import message_wizard

_logger = logging.getLogger(__name__)


class PaymentMethodUI(models.Model):
    _name = 'payment.method.ui'
    _description = "Payment Method Ui"

    def get_default_company(self):
        companies = self.env['ebizcharge.instance.config'].search(
            [('is_active', '=', True), '|', ('company_ids', '=', False), (
                'company_ids', 'in',
                self._context.get('allowed_company_ids'))]).mapped(
            'company_ids').ids
        return companies

    name = fields.Char(string='Request Payments Via Email', default='Request Payment Methods')
    is_adjustment = fields.Char(string='Is Adjustment')
    transaction_history_line = fields.One2many('list.ebiz.customers', 'sync_transaction_id', copy=True, )
    transaction_history_line_pending = fields.One2many('list.pending.payments.methods',
                                                       'sync_transaction_id_pending', copy=True, )
    transaction_history_line_received = fields.One2many('list.received.payments.methods',
                                                        'sync_transaction_id_received', copy=True)
    add_filter = fields.Boolean(string='Filters')
    customer_selection = fields.Selection([
        ('all_customers', 'All Customers'),
        ('no_save_card', 'Customers with no saved cards'),
        ('no_save_back_ach', 'Customers with no saved bank accounts'),
        ('no_payment_method', 'Customers with no saved payment methods'),
        ('card_expiring_soon', 'Customers with cards expiring soon'),
        ('expired_card', 'Customers with expired cards'),
    ], string='Display', help="Select which customer you'd like to display", default='all_customers')
    is_reopened = fields.Boolean()
    start_date = fields.Date(string='From Date')
    end_date = fields.Date(string='To Date')
    start_date_received = fields.Date(string='From Date Received')
    end_date_received = fields.Date(string='To Date Received')
    show_hide_div_send = fields.Boolean("Show Send")
    show_hide_div_pending = fields.Boolean("Show Pending")
    show_hide_div_added = fields.Boolean("Show")
    company_ids = fields.Many2many('res.company', compute='compute_company')
    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config', string='EBizCharge Merchant Account', )
    ebiz_profile_pending_id = fields.Many2one('ebizcharge.instance.config',
                                              string='EBizCharge Pending Merchant Account')
    ebiz_profile_received_id = fields.Many2one('ebizcharge.instance.config',
                                               string='EBizCharge Received Merchant Account')

    def _compute_display_name(self):
        self.display_name = 'Request Payment Methods'

    @api.depends('ebiz_profile_id')
    def compute_company(self):
        self.company_ids = self._context.get('allowed_company_ids')

    def js_flush_customer(self, ecom_side=None):
        rec = self.env['payment.method.ui'].search([])
        if rec:
            # rec.ebiz_profile_id = False
            # rec.ebiz_profile_pending_id = False
            # rec.ebiz_profile_received_id = False
            rec.is_reopened = False
            rec.customer_selection = "all_customers"

    def create_send_request_record(self):
        list_of_customers = [(6, 0, 0)]
        profile_obj = self.env['ebizcharge.instance.config']
        profile = int(profile_obj.get_upload_instance(active_model='payment.method.ui', active_id=self))
        if profile:
            self.ebiz_profile_id = profile
            self.start_date = self.ebiz_profile_id._default_get_start()
            self.end_date = self.ebiz_profile_id._default_get_end_date()

        if self.ebiz_profile_id:
            customers = self.env['res.partner'].search(
                [('ebiz_internal_id', '!=', False), ('ebiz_profile_id', '=', self.ebiz_profile_id.id)])
        else:
            customers = self.env['res.partner'].search([('ebiz_internal_id', '!=', False)])

        for customer in customers:
            line = (0, 0, {
                'customer_name': customer.id,
                'customer_id': str(customer.id),
                'email_id': customer.email,
                'customer_phone': customer.phone,
                'customer_city': customer.city,
                'sync_transaction_id': self.id,
            })
            list_of_customers.append(line)
        return list_of_customers

    def create_pending_request_record(self):
        if self.start_date and self.end_date:
            if not self.start_date <= self.end_date:
                return message_wizard('From Date should be lower than the To date!', 'Invalid Date')
        list_of_pending = [(6, 0, 0)]
        profile_obj = self.env['ebizcharge.instance.config']
        default_instance = profile_obj.search(
            [('is_valid_credential', '=', True), ('is_default', '=', True), ('is_active', '=', True), '|',
             ('company_ids', '=', False),
             ('company_ids', 'in', self._context.get('allowed_company_ids'))],
            limit=1)
        if not self.ebiz_profile_pending_id:
            if default_instance:
                if not default_instance.company_ids:
                    profile = default_instance.id
                elif default_instance.company_ids and default_instance.company_ids.ids in self._context.get('allowed_company_ids'):
                    profile = default_instance.id
                else:
                    profile = profile_obj.search(
                        [('is_valid_credential', '=', True), ('is_active', '=', True), '|', ('company_ids', '=', False),
                         ('company_ids', 'in', self.env.company.ids)], limit=1).id
            else:
                profile = profile_obj.search(
                    [('is_valid_credential', '=', True), ('is_active', '=', True), '|', ('company_ids', '=', False),
                     ('company_ids', 'in', self.env.company.ids)], limit=1).id

            self.ebiz_profile_pending_id = profile
            self.start_date = self.ebiz_profile_pending_id._default_get_start()
            self.end_date = self.ebiz_profile_pending_id._default_get_end_date()
        list_of_pending.extend(
            self.fetch_pending_payments(self.start_date, self.end_date, self.ebiz_profile_pending_id))
        if 'from_pending_button' not in self.env.context:
            return list_of_pending
        else:
            self.update({
                'transaction_history_line_pending': list_of_pending,
            })

    def create_received_request_record(self):
        if self.start_date_received and self.end_date_received:
            if not self.start_date_received <= self.end_date_received:
                return message_wizard('From Date should be lower than the To date!', 'Invalid Date')
        list_of_received = [(6, 0, 0)]
        profile_obj = self.env['ebizcharge.instance.config']
        default_instance = profile_obj.search(
            [('is_valid_credential', '=', True), ('is_default', '=', True), ('is_active', '=', True), '|',
             ('company_ids', '=', False),
             ('company_ids', 'in', self._context.get('allowed_company_ids'))],
            limit=1)
        if not self.ebiz_profile_received_id:
            if default_instance:
                if not default_instance.company_ids:
                    profile = default_instance.id
                elif default_instance.company_ids and default_instance.company_ids.ids in self._context.get('allowed_company_ids'):
                    profile = default_instance.id
                else:
                    profile = profile_obj.search(
                        [('is_valid_credential', '=', True), ('is_active', '=', True), '|', ('company_ids', '=', False),
                         ('company_ids', 'in', self.env.company.ids)], limit=1).id
            else:
                profile = profile_obj.search(
                    [('is_valid_credential', '=', True), ('is_active', '=', True), '|', ('company_ids', '=', False),
                     ('company_ids', 'in', self.env.company.ids)], limit=1).id
            self.ebiz_profile_received_id = profile
            self.start_date_received = self.ebiz_profile_received_id._default_get_start()
            self.end_date_received = self.ebiz_profile_received_id._default_get_end_date()
        list_of_received.extend(
            self.fetch_received_payments(self.start_date_received, self.end_date_received,
                                         self.ebiz_profile_received_id))
        if 'from_received_button' not in self.env.context:
            return list_of_received
        else:
            self.update({
                'transaction_history_line_received': list_of_received,
            })

    @api.model
    def read(self, fields=None, load='_classic_read'):
        if self.ids and not self.is_reopened:
            self.create_default_records()
            self.is_reopened = True
        result = super(PaymentMethodUI, self).read(fields, load=load)
        return result

    def create_default_records(self):
        self.update({
            'transaction_history_line': self.create_send_request_record(),
            'transaction_history_line_pending': self.create_pending_request_record(),
            'transaction_history_line_received': self.create_received_request_record(),
        })

    def send_request_payment(self, *args, **kwargs):
        try:
            if len(kwargs['values']) == 0:
                raise UserError('Please select a record first!')
            customer_ids = []
            partner_obj = self.env['res.partner']
            recipients_obj = self.env['email.recipients']
            recipients_obj.search([]).unlink()
            odoo_customer = False
            for customer in kwargs['values']:
                recipient = recipients_obj.create({
                    'partner_id': customer['customer_id'],
                    'email': customer['email_id']
                })
                customer_ids.append(recipient.id)
                odoo_customer = partner_obj.search([('id', '=', customer['customer_id'])])
                if odoo_customer:
                    if odoo_customer.customer_rank > 0 and not odoo_customer.ebiz_internal_id:
                        odoo_customer.sync_to_ebiz()
            profile = False
            if odoo_customer and odoo_customer[0].ebiz_profile_id:
                profile = odoo_customer[0].ebiz_profile_id.id

            return {
                'type': 'ir.actions.act_window',
                'name': _('Request Payment Method'),
                'res_model': 'wizard.ebiz.request.payment.method.bulk',
                'target': 'new',
                'view_mode': 'form',
                'views': [[False, 'form']],
                'context': {
                    'default_partner_id': [[6, 0, customer_ids]],
                    'selection_check': 1,
                    'customers': customer_ids,
                    'default_ebiz_profile_id': profile,
                    'profile': profile,
                },
            }

        except Exception as e:
            raise UserError(e)

    def resend_email(self, *args, **kwargs):
        try:
            resp_lines = []
            success = 0
            failed = 0
            total_count = len(kwargs['values'])

            if len(kwargs['values']) == 0:
                raise UserError('Please select a record first!')
            else:
                ebiz_obj = self.env['ebiz.charge.api']
                partner_obj = self.env['res.partner']
                rpm_counter_obj = self.env['rpm.counter']
                pending_method_obj = self.env['payment.method.ui']
                for record in kwargs['values']:
                    resp_line = {}
                    resp_line['customer_name'] = resp_line['customer_id'] = record['customer_id']
                    resp_line['email_address'] = record['email_id']
                    partner = partner_obj.search([('id', '=', record['customer_id'])])
                    instance = None
                    if partner.ebiz_profile_id:
                        instance = partner.ebiz_profile_id

                    ebiz = ebiz_obj.get_ebiz_charge_obj(instance=instance)
                    form_url = ebiz.client.service.ResendEbizWebFormEmail(**{
                        'securityToken': ebiz._generate_security_json(),
                        'paymentInternalId': record['payment_internal_id'],
                    })
                    counter = rpm_counter_obj.search([('request_id', '=', record['payment_internal_id'])])
                    if counter:
                        counter[0].counter += 1
                    else:
                        counter = rpm_counter_obj.create({
                            'counter': 1,
                            'request_id': record['payment_internal_id'],
                        })
                    resp_line['status'] = 'Success'
                    success += 1

                    pending_methods = pending_method_obj.search([])
                    for method in pending_methods:
                        if 'id' in record:
                            if method.transaction_history_line_pending:
                                for pending in method.transaction_history_line_pending:
                                    if pending.id == record['id'] and counter:
                                        pending.update({
                                            'no_of_times_sent': counter[0].counter
                                        })
                    resp_lines.append([0, 0, resp_line])

            wizard = self.env['wizard.multi.payment.message'].create({'name': 'resend', 'lines_ids': resp_lines,
                                                                      'success_count': success, 'failed_count': failed,
                                                                      'total': total_count})

            return {'type': 'ir.actions.act_window',
                    'name': _('Request Payment Methods'),
                    'res_model': 'wizard.multi.payment.message',
                    'target': 'new',
                    'res_id': wizard.id,
                    'view_mode': 'form',
                    'views': [[False, 'form']],
                    'context':
                        self._context,
                    }

        except Exception as e:
            raise UserError(e)

    def search_pending_payments(self):
        try:
            if not self.start_date and not self.end_date:
                raise UserError('No Option Selected!')

            self.env['list.pending.payments.methods'].search([]).unlink()

            if self.start_date and self.end_date:
                if not self.start_date_received < self.end_date_received:
                    return message_wizard('From Date should be lower than the To date!', 'Invalid Date')
            if self.ebiz_profile_pending_id:
                instances = self.ebiz_profile_pending_id
            else:
                instances = self.env['ebizcharge.instance.config'].search(
                    [('is_valid_credential', '=', True), ('is_active', '=', True)])
            ebiz_obj = self.env['ebiz.charge.api']
            for instance in instances:
                ebiz = ebiz_obj.get_ebiz_charge_obj(instance=instance)
                params = {
                    'securityToken': ebiz._generate_security_json(),
                    'fromPaymentRequestDateTime': str(self.start_date),
                    'toPaymentRequestDateTime': str(self.end_date + timedelta(days=1)),
                    "filters": {
                        "SearchFilter": [{
                            'FieldName': 'InvoiceNumber',
                            'ComparisonOperator': 'eq',
                            'FieldValue': 'PM',
                        }]
                    },
                    "limit": 1000,
                    "start": 0,
                }
                payments = ebiz.client.service.SearchEbizWebFormPendingPayments(**params)
                payment_lines = []
                partner_obj = self.env['res.partner']
                rpm_counter_obj = self.env['rpm.counter']
                if payments:
                    for payment in payments:
                        if payment['CustomerId'].isnumeric():
                            is_customer = partner_obj.search([('id', '=', int(payment['CustomerId']))])
                            if is_customer:
                                counter = rpm_counter_obj.search([('request_id', '=', payment['PaymentInternalId'])])
                                payment_line = {
                                    "customer_name": int(payment['CustomerId']),
                                    "customer_id": payment['CustomerId'],
                                    "email_id": payment['CustomerEmailAddress'],
                                    "date_time": datetime.strptime(payment['PaymentRequestDateTime'], '%Y-%m-%dT%H:%M:%S'),
                                    "payment_internal_id": payment['PaymentInternalId'],
                                    "sync_transaction_id_pending": self.id,
                                    'no_of_times_sent': counter.counter if counter else 1,
                                }
                                payment_lines.append(payment_line)
                    self.env['list.pending.payments.methods'].create(payment_lines)

        except Exception as e:
            raise UserError(e)

    def search_received_payments(self):
        try:
            if not self.start_date_received and not self.end_date_received:
                raise UserError('No Option Selected!')

            self.env['list.received.payments.methods'].search([]).unlink()

            if not self.start_date_received < self.end_date_received:
                return message_wizard('From Date should be lower than the To date!', 'Invalid Date')
            if self.ebiz_profile_received_id:
                instances = self.ebiz_profile_received_id
            else:
                instances = self.env['ebizcharge.instance.config'].search(
                    [('is_valid_credential', '=', True), ('is_active', '=', True)])
            partner_obj = self.env['res.partner']
            rpm_counter_obj = self.env['rpm.counter']
            for instance in instances:
                ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
                params = {
                    'securityToken': ebiz._generate_security_json(),
                    'fromPaymentRequestDateTime': str(self.start_date_received),
                    'toPaymentRequestDateTime': str(self.end_date_received + timedelta(days=1)),
                    "filters": {
                        "SearchFilter": [{
                            'FieldName': 'InvoiceNumber',
                            'ComparisonOperator': 'eq',
                            'FieldValue': 'PM',
                        }]
                    },
                    "limit": 1000,
                    "start": 0,
                }
                payments = ebiz.client.service.SearchEbizWebFormReceivedPayments(**params)
                payment_lines = []
                if payments:
                    for payment in payments:
                        try:
                            if payment['CustomerId'].isnumeric():
                                is_customer = partner_obj.search([('id', '=', int(payment['CustomerId']))])
                                counter = rpm_counter_obj.search([('request_id', '=', payment['PaymentInternalId'])])
                                if counter:
                                    counter[0].unlink()
                                if is_customer:
                                    payment_line = {
                                        "customer_name": int(payment['CustomerId']),
                                        "customer_id": int(payment['CustomerId']),
                                        "email_id": payment['CustomerEmailAddress'],
                                        "date_time": datetime.strptime(payment['PaymentRequestDateTime'],
                                                                       '%Y-%m-%dT%H:%M:%S'),
                                        "payment_internal_id": payment['PaymentInternalId'],
                                        "payment_method": payment['PaymentMethod'] + ' ending in ' + payment['Last4'],
                                        "sync_transaction_id_received": self.id,
                                        "customer_token": is_customer.ebizcharge_customer_token,
                                    }
                                    payment_lines.append(payment_line)
                        except:
                            pass
                    self.env['list.received.payments.methods'].create(payment_lines)
        except Exception as e:
            raise UserError(e)

    def delete_invoice(self, *args, **kwargs):
        try:
            if len(kwargs['values']) == 0:
                raise UserError('Please select a record first!')
            else:
                text = f"Are you sure you want to remove {len(kwargs['values'])} request(s) from Pending Requests?"
                wizard = self.env['wizard.delete.payment.methods'].create({"record_id": self.id,
                                                                           "record_model": self._name,
                                                                           "text": text})
                action = self.env.ref('payment_ebizcharge_crm.wizard_delete_rpm_action').read()[0]
                action['res_id'] = wizard.id

                action['context'] = dict(
                    self.env.context,
                    kwargs_values=kwargs['values'],
                    pending_received='Pending Requests'
                )

                return action

        except Exception as e:
            raise UserError(e)

    def search_customers(self):
        try:
            if self.customer_selection:
                profile_obj = self.env['ebizcharge.instance.config']
                self.env["list.ebiz.customers"].search([]).unlink()
                list_of_customers = False
                if self.customer_selection == 'all_customers':
                    if self.ebiz_profile_id:
                        list_of_customers = self.env['res.partner'].search(
                            [('ebiz_internal_id', '!=', False), ('ebiz_profile_id', '=', self.ebiz_profile_id.id)])
                    else:
                        list_of_customers = self.env['res.partner'].search([('ebiz_internal_id', '!=', False)])
                elif self.customer_selection == 'no_save_card':
                    list_of_customers = []
                    if self.ebiz_profile_id:
                        instances = self.ebiz_profile_id
                    else:
                        instances = profile_obj.search([('is_valid_credential', '=', True)])
                    for instance in instances:
                        ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
                        params = {
                            'securityToken': ebiz._generate_security_json(),
                            'countOnly': False,
                            'start': 0,
                            'limit': 100000,
                        }
                        no_saved_card_ach = ebiz.client.service.GetPaymentMethodProfileCounts(**params)[
                            'PaymentMethodProfileCountsList']['PaymentMethodProfileCounts']

                        partner_obj = self.env['res.partner']
                        for method in no_saved_card_ach:
                            if method['CreditCardsCount'] == 0:
                                if self.ebiz_profile_id:
                                    local_customer = partner_obj.search(
                                        [('ebiz_profile_id', '=', self.ebiz_profile_id.id),
                                         ('ebiz_internal_id', '!=', False),
                                         ('ebiz_customer_id', '=', method['CustomerInformation']['CustomerId'])])
                                else:
                                    local_customer = partner_obj.search([('ebiz_internal_id', '!=', False),
                                                                         ('ebiz_customer_id', '=',
                                                                          int(method['CustomerInformation'][
                                                                                  'CustomerId']))])
                                if local_customer:
                                    list_of_customers.append(local_customer)

                elif self.customer_selection == 'no_save_back_ach':
                    list_of_customers = []
                    if self.ebiz_profile_id:
                        instances = self.ebiz_profile_id
                    else:
                        instances = profile_obj.search([('is_valid_credential', '=', True)])
                    for instance in instances:
                        ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
                        params = {
                            'securityToken': ebiz._generate_security_json(),
                            'countOnly': False,
                            'start': 0,
                            'limit': 100000,
                        }
                        no_saved_card_ach = ebiz.client.service.GetPaymentMethodProfileCounts(**params)[
                            'PaymentMethodProfileCountsList']['PaymentMethodProfileCounts']

                        for method in no_saved_card_ach:
                            if method['BankAccountsCount'] == 0:
                                try:
                                    if self.ebiz_profile_id:
                                        local_customer = self.env['res.partner'].search(
                                            [('ebiz_profile_id', '=', self.ebiz_profile_id.id),
                                             ('ebiz_internal_id', '!=', False), (
                                                 'ebiz_customer_id', '=', method['CustomerInformation']['CustomerId'])])
                                    else:
                                        local_customer = self.env['res.partner'].search(
                                            [('ebiz_internal_id', '!=', False), (
                                                'ebiz_customer_id', '=', method['CustomerInformation']['CustomerId'])])
                                except Exception as e:
                                    continue

                                if local_customer:
                                    list_of_customers.append(local_customer)

                elif self.customer_selection == 'no_payment_method':
                    list_of_customers = []
                    if self.ebiz_profile_id:
                        instances = self.ebiz_profile_id
                    else:
                        instances = profile_obj.search([('is_valid_credential', '=', True)])
                    for instance in instances:
                        ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
                        params = {
                            'securityToken': ebiz._generate_security_json(),
                            'countOnly': False,
                            'start': 0,
                            'limit': 100000,
                        }
                        no_saved_card_ach = ebiz.client.service.GetPaymentMethodProfileCounts(**params)[
                            'PaymentMethodProfileCountsList']['PaymentMethodProfileCounts']

                        for method in no_saved_card_ach:
                            if method['BankAccountsCount'] == 0 and method['CreditCardsCount'] == 0:
                                if self.ebiz_profile_id:
                                    local_customer = self.env['res.partner'].search(
                                        [('ebiz_profile_id', '=', self.ebiz_profile_id.id),
                                         ('ebiz_internal_id', '!=', False), (
                                             'ebiz_customer_id', '=', method['CustomerInformation']['CustomerId'])])
                                else:
                                    local_customer = self.env['res.partner'].search(
                                        [('ebiz_internal_id', '!=', False), (
                                            'ebiz_customer_id', '=', method['CustomerInformation']['CustomerId'])])
                                if local_customer:
                                    list_of_customers.append(local_customer)

                elif self.customer_selection == 'expired_card':
                    list_of_customers = []
                    filters_list = []

                    filters_list.append(
                        {'FieldName': 'ExpiredCreditCardsCount', 'ComparisonOperator': 'gt',
                         'FieldValue': 0})
                    if self.ebiz_profile_id:
                        instances = self.ebiz_profile_id
                    else:
                        instances = profile_obj.search([('is_valid_credential', '=', True)])
                    for instance in instances:
                        ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)

                        params = {
                            'securityToken': ebiz._generate_security_json(),
                            'filters': {"SearchFilter": filters_list},
                            'countOnly': False,
                            'start': 0,
                            'limit': 100000,
                            'sort': 'DateTime'
                        }
                        cards = ebiz.client.service.GetCardsExpirationList(**params)['CardExpirationCountsList']
                        if cards:
                            cards_lists = cards['CardExpirationCounts']

                            for card in cards_lists:
                                try:
                                    if self.ebiz_profile_id:
                                        local_customer = self.env['res.partner'].search(
                                            [('ebiz_internal_id', '!=', False),
                                             ('ebiz_profile_id', '=', self.ebiz_profile_id.id),
                                             ('ebiz_internal_id', '=',
                                              card['CustomerInformation']['CustomerInternalId'])])
                                    else:
                                        local_customer = self.env['res.partner'].search(
                                            [('ebiz_internal_id', '!=', False),
                                             ('ebiz_internal_id', '=',
                                              card['CustomerInformation']['CustomerInternalId'])])
                                except Exception as e:
                                    continue

                                if local_customer:
                                    list_of_customers.append(local_customer[0])

                elif self.customer_selection == 'card_expiring_soon':
                    if self.ebiz_profile_id:
                        context = {
                            'profiles': [self.ebiz_profile_id.id]
                        }
                    else:
                        instances = profile_obj.search([('is_valid_credential', '=', True)])
                        context = {
                            'profiles': instances.ids
                        }
                    return {'type': 'ir.actions.act_window',
                            'name': _('Please Select'),
                            'res_model': 'wizard.cards.expiring.soon',
                            'target': 'new',
                            'view_mode': 'form',
                            'view_type': 'form',
                            'context': context,
                            }

                list_of_dict = []
                if list_of_customers:
                    self.env["list.ebiz.customers"].search([]).unlink()
                    for customer in list_of_customers:
                        list_of_dict.append({
                            'customer_name': customer.id,
                            'customer_id': customer.id,
                            'email_id': customer.email,
                            'customer_phone': customer.phone,
                            'customer_city': customer.city,
                            'sync_transaction_id': self.id,
                        })
                    self.env['list.ebiz.customers'].create(list_of_dict)
            else:
                raise UserError('No option selected!')

        except Exception as e:
            raise UserError(e)

    def delete_invoice_added(self, *args, **kwargs):
        try:
            if len(kwargs['values']) == 0:
                raise UserError('Please select a record first!')
            else:
                text = f"Are you sure you want to remove {len(kwargs['values'])} payment method(s) from Added Payment Methods?"
                wizard = self.env['wizard.delete.payment.methods'].create({"record_id": self.id,
                                                                           "record_model": self._name,
                                                                           "text": text})
                action = self.env.ref('payment_ebizcharge_crm.wizard_delete_rpm_action').read()[0]
                action['res_id'] = wizard.id

                action['context'] = dict(
                    self.env.context,
                    kwargs_values=kwargs['values'],
                    pending_received='Added Payment Methods'
                )
                return action

        except Exception as e:
            raise UserError(e)

    def fetch_pending_payments(self, start, end, instance):
        if instance:
            instances = instance
        elif self.ebiz_profile_id:
            instances = self.ebiz_profile_id
        else:
            instances = self.env['ebizcharge.instance.config'].search([('is_valid_credential', '=', True)])
        payment_lines = []
        for instance in instances:
            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            params = {
                'securityToken': ebiz._generate_security_json(),
                'fromPaymentRequestDateTime': str(start),
                'toPaymentRequestDateTime': str(end + timedelta(days=1)),
                "filters": {
                    "SearchFilter": [{
                        'FieldName': 'InvoiceNumber',
                        'ComparisonOperator': 'eq',
                        'FieldValue': 'PM',
                    }]
                },
                "limit": 1000,
                "start": 0,
            }
            payments = ebiz.client.service.SearchEbizWebFormPendingPayments(**params)
            if payments:
                for payment in payments:
                    if payment['CustomerId'].isnumeric():
                        is_customer = self.env['res.partner'].search([('id', '=', int(payment['CustomerId']))])
                        if is_customer:
                            counter = self.env['rpm.counter'].search([('request_id', '=', payment['PaymentInternalId'])])
                            line = (0, 0, {
                                "customer_name": int(payment['CustomerId']),
                                "customer_id": payment['CustomerId'] if payment['CustomerId'].isnumeric() else False ,
                                "email_id": payment['CustomerEmailAddress'],
                                "date_time": datetime.strptime(payment['PaymentRequestDateTime'], '%Y-%m-%dT%H:%M:%S'),
                                "payment_internal_id": payment['PaymentInternalId'],
                                "sync_transaction_id_pending": self.id,
                                'no_of_times_sent': counter.counter if counter else 1,
                            })
                            payment_lines.append(line)

        return payment_lines

    def fetch_received_payments(self, start, end, instance):
        self.env['list.received.payments.methods'].search([]).unlink()
        if instance:
            instances = instance
        elif self.ebiz_profile_id:
            instances = self.ebiz_profile_id
        else:
            instances = self.env['ebizcharge.instance.config'].search([('is_valid_credential', '=', True)])
        payment_lines = []
        for instance in instances:
            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            params = {
                'securityToken': ebiz._generate_security_json(),
                'fromPaymentRequestDateTime': str(start),
                'toPaymentRequestDateTime': str(end + timedelta(days=1)),
                "filters": {
                    "SearchFilter": [{
                        'FieldName': 'InvoiceNumber',
                        'ComparisonOperator': 'eq',
                        'FieldValue': 'PM',
                    }]
                },
                "limit": 1000,
                "start": 0,
            }
            payments = ebiz.client.service.SearchEbizWebFormReceivedPayments(**params)
            if payments:
                for payment in payments:
                    try:
                        if payment['CustomerId'].isnumeric():
                            is_customer = self.env['res.partner'].search([('id', '=', int(payment['CustomerId']))])
                            if is_customer:
                                line = (0, 0, {
                                    "customer_name": int(payment['CustomerId']),
                                    "customer_id": payment['CustomerId'],
                                    "email_id": payment['CustomerEmailAddress'],
                                    "date_time": datetime.strptime(payment['PaymentRequestDateTime'], '%Y-%m-%dT%H:%M:%S'),
                                    "payment_internal_id": payment['PaymentInternalId'],
                                    "payment_method": payment['PaymentMethod'] + ' ending in ' + payment['Last4'],
                                    "sync_transaction_id_received": self.id,
                                    "customer_token": is_customer.ebizcharge_customer_token,
                                })
                                payment_lines.append(line)
                    except:
                        pass

        return payment_lines


class ListCustomers(models.TransientModel):
    _name = 'list.ebiz.customers'
    _description = "List Ebiz Customer"

    sync_date = fields.Datetime('Execution Date/Time', required=True, default=fields.Datetime.now)
    sync_transaction_id = fields.Many2one('payment.method.ui', string='Partner Reference', required=True,
                                          ondelete='cascade', index=True, copy=False)
    name = fields.Char(string='Number')
    customer_name = fields.Many2one('res.partner', string='Customer', domain="[('ebiz_customer_id', '!=', False)]")
    customer_id = fields.Char(string='Customer ID')
    email_id = fields.Char(string='Email')
    customer_phone = fields.Char('Phone')
    customer_city = fields.Char('City')
    status = fields.Char(string='Status')

    def view_payment_methods(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Payment Methods',
            'view_mode': 'tree',
            'res_model': 'payment.token',
            'domain': [('partner_id', '=', self.customer_name.id)],
            'context': "{'create': False}"
        }


class ListPendingMethods(models.Model):
    _name = 'list.pending.payments.methods'
    _description = "List Pending Payment Methods"

    sync_date = fields.Datetime('Execution Date/Time', required=True, default=fields.Datetime.now)
    sync_transaction_id_pending = fields.Many2one('payment.method.ui', string='Partner Reference', required=True,
                                                  ondelete='cascade', index=True, copy=False)
    name = fields.Char(string='Number')
    customer_name = fields.Many2one('res.partner', string='Customer', domain="[('ebiz_customer_id', '!=', False)]")
    customer_id = fields.Char(string='Customer ID')
    email_id = fields.Char(string='Email')
    date_time = fields.Datetime(string='Org. Date & Time Sent')
    payment_internal_id = fields.Char(string='Payment Internal Id')
    no_of_times_sent = fields.Integer("# of Times Sent")


class ListReceivedMethods(models.Model):
    _name = 'list.received.payments.methods'
    _description = "List Received Payment Methods"

    sync_date = fields.Datetime('Execution Date/Time', required=True, default=fields.Datetime.now)
    sync_transaction_id_received = fields.Many2one('payment.method.ui', string='Partner Reference', required=True,
                                                   ondelete='cascade', index=True, copy=False)

    customer_name = fields.Many2one('res.partner', string='Customer', domain="[('ebiz_customer_id', '!=', False)]")
    customer_id = fields.Char(string='Customer ID')
    email_id = fields.Char(string='Email')
    date_time = fields.Datetime(string='Date & Time Added')
    payment_internal_id = fields.Char(string='Payment Internal Id')
    payment_method = fields.Char('Payment Method')
    customer_token = fields.Char('Customer Token')

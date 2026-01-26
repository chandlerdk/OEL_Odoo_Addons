from odoo import fields, models, api, _
from odoo.exceptions import ValidationError, UserError
from datetime import datetime, timedelta
import logging
from ..models.ebiz_charge import message_wizard

_logger = logging.getLogger(__name__)


class DownloadEBizPayment(models.TransientModel):
    _name = 'ebiz.download.payments'
    _description = "EBiz Download Payments"

    def get_default_from_date(self):
        return self.env['ebizcharge.instance.config'].get_document_download_start_date()

    def get_default_to_date(self):
        today = datetime.now()
        end = today + timedelta(days=1)
        return end.date()

    def domain_users(self):
        domain = []
        if 'active_id' in self.env.context and self.env.context.get('active_id') is not None:
            rec = self.env['ebiz.download.payments'].browse([self.env.context.get('active_id')])
            domain.append(('partner_id.ebiz_profile_id', '=', rec.ebiz_profile_id.id))
        else:
            today = datetime.now()
            end = today + timedelta(days=1)

            start = self.env['ebizcharge.instance.config'].get_document_download_start_date()
            dt_start = datetime.combine(start, datetime.min.time())

            domain.append(('last_sync_date', '>=', dt_start))
            domain.append(('last_sync_date', '<=', end))
            default_instance = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_default', '=', True), ('is_active', '=', True)], limit=1)
            if default_instance:
                domain.append(('partner_id.ebiz_profile_id', '=', default_instance.id))
        return domain

    def get_default_company(self):
        return self._context.get('allowed_company_ids')

    def _default_instance_id(self):
        return self.env['ebizcharge.instance.config']._default_instance_id()

    from_date = fields.Date("From Date", required=True, default=get_default_from_date)
    is_adjustment_field = fields.Char(string='Adjustment')
    to_date = fields.Date("To Date", required=True, default=get_default_to_date)
    payment_lines = fields.One2many('ebiz.payment.lines', 'wiz_id')
    compute_counter = fields.Integer(compute="_compute_payment_line", store=True)
    name = fields.Char(string='Download Received Payments', default=lambda self: _('Download Received Payments'))
    transaction_log_lines = fields.Many2many('sync.logs', copy=True, domain=lambda self: self.domain_users())
    payment_category = fields.Selection([
                                   ('all', 'All'), 
                                   ('portal_mobile', 'Portal and Mobile'),
                                         ('email_pay', 'Payment Links'),
                                         ('fixed_amount', 'Fixed Amount Auto Payments')], string='Payment Category',
                                        default='portal_mobile')
    is_download_pressed = fields.Boolean()
    is_download_pressed_fixed_auto_amount = fields.Boolean()
    company_ids = fields.Many2many('res.company', compute='compute_company', default=get_default_company)
    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config', string='EBizCharge Profile',
                                      default=_default_instance_id)

    def _compute_display_name(self):
        self.display_name = 'Download Received Payments'

    def get_recurring_payments(self, start, end, ebiz):
        params = {
            'securityToken': ebiz._generate_security_json(),
            "fromDateTime": str(start),
            "toDateTime": str(end),
            "limit": 1000,
            "start": 0,
        }
        payments = ebiz.client.service.SearchRecurringPayments(**params)
        payment_lines = []

        if payments:
            for payment in payments:
                if payment['CustomerId'] != 'False' and payment['CustomerId'].isnumeric():
                    odoo_customer = self.env['res.partner'].browse(int(payment['CustomerId'])).exists()
                    if odoo_customer:
                        get_transaction = ebiz.client.service.GetTransactionDetails(
                            **{'securityToken': ebiz._generate_security_json(),
                               'transactionRefNum': payment['RefNum']})
                        payment_line = self.get_payment_line(payment, odoo_customer)
                        payment_line['type_id'] = "Fixed Amount Auto Payments"
                        payment_line['source'] = get_transaction['Source']
                        payment_lines.append((0, 0, payment_line))
        return payment_lines

    def get_payments(self, start, end, ebiz):
        params = {
            'securityToken': ebiz._generate_security_json(),
            "fromDateTime": str(start),
            "toDateTime": str(end),
            "limit": 1000,
            "start": 0,
        }
        payments = ebiz.client.service.GetPayments(**params)
        payment_lines = []

        if payments:
            for payment in payments:
                if payment['CustomerId'] != 'False' and payment['CustomerId'].isnumeric():
                    odoo_customer = self.env['res.partner'].browse(int(payment['CustomerId'])).exists()
                    if odoo_customer:
                        get_transaction = ebiz.client.service.GetTransactionDetails(
                            **{'securityToken': ebiz._generate_security_json(),
                               'transactionRefNum': payment['RefNum']})
                        payment_line = self.get_payment_line(payment, odoo_customer)
                        payment_line['type_id'] = 'Credit Note Payment' if payment[
                                                                               'TypeId'] == 'InvCredit' else self.get_payment_type(
                            payment['PaymentType'])
                        payment_line['source'] = get_transaction['Source']
                        payment_lines.append((0, 0, payment_line))
        return payment_lines

    def get_received_email_payments(self, start, end, ebiz):
        params = {
            'securityToken': ebiz._generate_security_json(),
            "fromPaymentRequestDateTime": str(start),
            "toPaymentRequestDateTime": str(end),
            "filters": {'SearchFilter': []},
            "limit": 1000,
            "start": 0,
        }
        payments = ebiz.client.service.SearchEbizWebFormReceivedPayments(**params)
        payment_lines = []

        if payments:
            for payment in payments:
                if payment['InvoiceNumber'] in ['PM', "Token"]: continue
                if payment['CustomerId'] != 'False' and payment['CustomerId'].isnumeric():
                    try:
                        odoo_customer = self.env['res.partner'].browse(int(payment['CustomerId'])).exists()
                    except:
                        continue
                    if odoo_customer:
                        if payment['InvoiceNumber']:
                            invoice = self.env['account.move'].search([('name', '=', payment['InvoiceNumber'])])
                            sale = self.env['sale.order'].search([('name', '=', payment['InvoiceNumber'])])
                            if invoice or sale:
                                payment_line = self.get_payment_line(payment, odoo_customer)
                                payment_line['type_id'] = 'Email Pay' if payment['TypeId'] == 'EmailForm' else payment[
                                    'TypeId']
                                payment_line['is_email_payment'] = True
                                payment_line['source'] = 'Email Pay' if payment[
                                                                            'PaymentSourceId'].strip() == 'Odoo CRM' else \
                                    payment['PaymentSourceId'] or "N/A"
                                payment_lines.append((0, 0, payment_line))
                        else:
                            payment_line = self.get_payment_line(payment, odoo_customer)
                            payment_line['type_id'] = 'Email Pay' if payment['TypeId'] == 'EmailForm' else payment[
                                'TypeId']
                            payment_line['is_email_payment'] = True
                            payment_line['source'] = 'Email Pay' if payment[
                                                                        'PaymentSourceId'].strip() == 'Odoo CRM' else \
                                payment['PaymentSourceId'] or "N/A",
                            payment_lines.append((0, 0, payment_line))
        return payment_lines

    def get_payment_line(self, payment, partner):
        currency_id = partner.property_product_pricelist.currency_id.id

        def ref_date(date):
            if not date:
                return date
            if '-' in date:
                rf_date = date.split('-')
            else:
                rf_date = date.split('/')
            return f"{rf_date[1]}/{rf_date[2]}/{rf_date[0]}"
        sale = self.env['sale.order'].search([('name', '=', payment['InvoiceNumber'])])
        is_save_link = False
        if sale and sale.invoice_status not in ('no','to invoice') and not sale.partner_id.ebiz_profile_id.apply_sale_pay_inv:
            is_save_link = True
        val = {
            "payment_type": payment['PaymentType'],
            "payment_internal_id": payment['PaymentInternalId'],
            "customer_id": str(payment['CustomerId']),
            "partner_id": int(payment['CustomerId']),
            "invoice_number": payment['InvoiceNumber'],
            "invoice_number_op": payment['InvoiceNumber'],
            "invoice_internal_id": payment['InvoiceInternalId'],
            "invoice_date": ref_date(payment['InvoiceDate']),
            "invoice_due_date": ref_date(payment['InvoiceDueDate']),
            "po_num": payment['PoNum'],
            "invoice_amount": float(payment['InvoiceAmount'] or "0"),
            "currency_id": currency_id,
            "amount_due": float(payment['AmountDue'] or "0"),
            "auth_code": payment['AuthCode'],
            "ref_num": payment['RefNum'],
            "is_save_link": is_save_link,
            "payment_method": f"{payment['PaymentMethod']} ending in {payment['Last4']}",
            "date_paid": ref_date(payment['DatePaid'].split('T')[0]),
            "paid_amount": float(payment['PaidAmount'] or "0"),
            "paid_amount_op": payment['PaidAmount'],
        }
        return val

    def get_payment_type(self, type):
        if type == 'RecurringFullBalanceInvoicePayment':
            return "Statement Auto Payment"
        elif type == "InvoicePayment":
            return "Invoice Payments"
        elif type == 'QuickPay':
            return "Quick Payment"
        elif type == 'InvCredit':
            return "Credit"
        return ""

    @api.depends('ebiz_profile_id')
    def compute_company(self):
        self.company_ids = self._context.get('allowed_company_ids')

    @api.model_create_multi
    def create(self, values):
        for val in values:
            if 'transaction_log_lines' in val:
                val['transaction_log_lines'] = None
        res = super(DownloadEBizPayment, self).create(values)
        return res

    @api.model
    def read(self, fields=None, load='_classic_read'):
        result = super(DownloadEBizPayment, self).read(fields, load=load)
        return result

    def fetch_again(self):
        self.is_download_pressed_fixed_auto_amount = False
        if not self.payment_category:
            raise ValidationError('Please select a Payment Category before refreshing the table.')
        if self.payment_category == 'fixed_amount':
            self.is_download_pressed_fixed_auto_amount = True
        self.payment_lines.unlink()
        if self.from_date and self.to_date:
            if not self.from_date < self.to_date:
                message = 'From Date should be lower than the To date!'
                return message_wizard(message, 'Invalid Date')
        if self.payment_category == 'all' and 'from_confirm_wizard' not in self.env.context:
            wizard = self.env['message.confirm.wizard'].create({"wizard_id": self.id, 
            "text": 'Downloading all payment types at once may take several minutes to load. Would you like to continue?',})

            action = self.env.ref('payment_ebizcharge_crm.action_message_confirm_wizard_form').read()[0]
            action['res_id'] = wizard.id
            return action
        self.compute_payment_lines()
        self.is_download_pressed = True
        odoo_logs = self.env['sync.logs'].search([]).filtered(
            lambda i: datetime.strptime(i.date_paid, '%m/%d/%Y').date() >= self.from_date and datetime.strptime(
                i.date_paid, '%m/%d/%Y').date() <= self.to_date)
        if odoo_logs:
            self.transaction_log_lines = odoo_logs.ids
        else:
            self.transaction_log_lines = False
        action = self.env.ref('payment_ebizcharge_crm.action_ebiz_download_payments_form').read()[0]
        action['res_id'] = self.id
        return action

    def _compute_payment_line(self):
        if not self.from_date < self.to_date:
            raise ValidationError('From Date should be lower than the to date')
        self.compute_payment_lines()

    def compute_payment_lines(self):
        if self.ebiz_profile_id:
            instances = self.ebiz_profile_id
        else:
            instances = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_active', '=', True),
                 '|', ('company_ids', 'in', self._context.get('allowed_company_ids')), ('company_ids', '=', False)])
        payments = [(5, 0, 0)]
        for instance in instances:
            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            if self.payment_category == 'all':
                payments += self.get_payments(self.from_date, self.to_date, ebiz)
                payments += self.get_received_email_payments(self.from_date, self.to_date, ebiz)
                payments += self.get_recurring_payments(self.from_date, self.to_date, ebiz)
            if self.payment_category == 'portal_mobile':
                payments += self.get_payments(self.from_date, self.to_date, ebiz)
            if self.payment_category == 'email_pay':
                payments += self.get_received_email_payments(self.from_date, self.to_date, ebiz)
            if self.payment_category == 'fixed_amount':
                payments += self.get_recurring_payments(self.from_date, self.to_date, ebiz)
        self.payment_lines = payments

    def js_mark_as_applied(self, *args, **kwargs):
        if len(kwargs['values']) == 0:
            raise ValidationError('Please select at least one payment to proceed with this action.')
        if any(bool(val['is_save_link']) for val in kwargs['values'] if bool(val['is_save_link'])):
            text = "One or more sales orders have been converted to an invoice. Do you want to apply the payment(s) as payment on account?"
            wizard = self.env['message.confirm.wizard'].create({"wizard_id": self.id, "text": text, "is_sale": True })
            action = self.env.ref('payment_ebizcharge_crm.action_message_confirm_wizard_form').read()[0]
            action['res_id'] = wizard.id
            action['context'] = dict(
                self.env.context,
                kwargs_values=kwargs['values'],
            )
            return action
	
        return self.mark_as_applied(kwargs['values'])

    def mark_as_applied(self, js_filter_records):
        selected = js_filter_records
        if not selected:
            raise ValidationError('Please select at least one payment to proceed with this action.')
        message_lines = []
        success = 0
        failed = 0
        total = len(selected)
        list_of_invoices = []
        try:
            for item in selected:
                message_record = {
                    'customer_name': item['partner_id'][1],
                    'customer_id': item['partner_id'][0],
                    'invoice_no': item['invoice_number'],
                    'status': 'Success'
                }
                invoice = self.env['account.move'].search([('name', '=', item['invoice_number_op'])])
                sale = self.env['sale.order'].search([('name', '=', item['invoice_number_op'])])
                credit = self.env['account.payment'].search([('name', '=', item['invoice_number_op'])])
                if invoice.partner_id.ebiz_profile_id:
                    partner = invoice.partner_id
                else:
                    partner = self.env['res.partner'].search([('id', '=', item['customer_id'])])
                if partner:
                    instance = partner.ebiz_profile_id
                    if not instance:
                        raise UserError(f'{partner.name} have not any Merchant account .Please select Merchant '
                                        f'account on customer profile.')
                    ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
                    list_of_invoices.append(self.create_log_lines(item))
                    resp = False
                    try:
                        if invoice:
                            if not item['is_email_payment']:
                                resp = ebiz.client.service.MarkPaymentAsApplied(**{
                                    'securityToken': ebiz._generate_security_json(),
                                    'paymentInternalId': item['payment_internal_id'],
                                    'invoiceNumber': item['invoice_number'],
                                })
                            else:
                                resp = ebiz.client.service.MarkEbizWebFormPaymentAsApplied(**{
                                    'securityToken': ebiz._generate_security_json(),
                                    'paymentInternalId': item['payment_internal_id'],
                                })
                            if resp['Status'] == 'Success':
                                invoice.ebiz_create_payment_line(item['paid_amount_op'])
                        elif sale:
                            if sale and item.get('payment_on_account'):
                                resp = ebiz.client.service.MarkEbizWebFormPaymentAsApplied(**{
                                    'securityToken': ebiz._generate_security_json(),
                                    'paymentInternalId': item['payment_internal_id'],
                                })
                                if resp and resp['Status'] == 'Success':
                                    self.create_payment_on_account(partner, item)
                            elif sale and item['type_id']=='PayLinkOnly':
                                resp = ebiz.client.service.MarkEbizWebFormPaymentAsApplied(**{
                                    'securityToken': ebiz._generate_security_json(),
                                    'paymentInternalId': item['payment_internal_id'],
                                })
                                if resp and resp['Status'] == 'Success':
                                    payment_acq = self.env['payment.provider'].search(
                                        [('company_id', '=',
                                          partner.company_id.id if partner.company_id else self.env.company.id),
                                         ('code', '=', 'ebizcharge')], limit=1)
                                    ebiz_method_tran = self.env['payment.method'].search(
                                        [('code', '=', 'ebizcharge')], limit=1)
                                    ebiz_method = self.env['account.payment.method.line'].search(
                                        [('journal_id', '=', payment_acq.journal_id.id),
                                         ('payment_method_id.code', '=', 'ebizcharge')], limit=1)
                                    payment = False
                                    transactions = ebiz.client.service.GetTransactionDetails(
                                        **{'securityToken': ebiz._generate_security_json(),
                                           'transactionRefNum': item['ref_num']})
                                    if transactions['TransactionType'] != 'Auth Only':
                                        payment = self.env['account.payment'].sudo().create({
                                            'journal_id': payment_acq.journal_id.id,
                                            'payment_method_id': ebiz_method.payment_method_id.id,
                                            'payment_method_line_id':ebiz_method.id,
                                            'partner_id': partner.id,
                                            'payment_reference': item['ref_num'],
                                            'amount': item['paid_amount'],
                                            'partner_type': 'customer',
                                            'payment_type': 'inbound',
                                            'ref': item['invoice_number_op'] if item['invoice_number_op'] else '',
                                        })

                                    ebiz_transaction = self.env['payment.transaction'].sudo().create({
                                        'provider_id': payment_acq.sudo().id,
                                        'payment_method_id': ebiz_method_tran.id,
                                        'provider_reference': item['ref_num'] ,
                                        'reference': item['invoice_number_op'] if item['invoice_number_op'] else '',
                                        'amount': item['paid_amount'],
                                        'currency_id': payment_acq.company_id.currency_id.id,
                                        'partner_id': partner.id,
                                        'token_id': False,
                                        'operation': 'offline',
                                        'sale_order_ids': [sale.id],
                                        'payment_id': payment.id if payment else False,
                                    })  # In sudo mode to allow writing on callback fields
                                    ebiz_transaction._set_authorized()
                                    sale.save_payment_link = False
                                    sale.request_amount = 0
                                    if transactions['TransactionType'].lower() not in ['auth only', 'authonly']:
                                        ebiz_transaction.transaction_type = 'deposit'
                                        ebiz_transaction._set_done()
                                        
                            elif sale:
                                resp = ebiz.client.service.MarkApplicationTransactionAsApplied(**{
                                    'securityToken': ebiz._generate_security_json(),
                                    'applicationTransactionInternalId': item['payment_internal_id'],
                                })

                                if resp and resp['Status'] == 'Success':
                                    self.create_payment_on_account(partner, item)
                        elif credit:
                            resp = ebiz.client.service.MarkPaymentAsApplied(**{
                                'securityToken': ebiz._generate_security_json(),
                                'paymentInternalId': item['payment_internal_id'],
                                'invoiceNumber': item['invoice_number_op'],
                            })
                            credit.action_draft()
                            credit.cancel()
                        elif partner and item['type_id'] in ['Quick Payment', 'Fixed Amount Auto Payments']:
                            payment_acq = self.env['payment.provider'].search(
                                [('company_id', '=',
                                  partner.company_id.id if partner.company_id else self.env.company.id),
                                 ('code', '=', 'ebizcharge')])
                            if payment_acq:
                                if item['type_id'] in ['Fixed Amount Auto Payments']:
                                    resp = ebiz.client.service.MarkRecurringPaymentAsApplied(**{
                                        'securityToken': ebiz._generate_security_json(),
                                        'paymentInternalId': item['payment_internal_id'],
                                        'invoiceNumber': item['invoice_number_op'] if item['invoice_number_op'] else '',
                                    })
                                else:
                                    resp = ebiz.client.service.MarkPaymentAsApplied(**{
                                        'securityToken': ebiz._generate_security_json(),
                                        'paymentInternalId': item['payment_internal_id'],
                                        'invoiceNumber': item['invoice_number_op'] if item['invoice_number_op'] else '',
                                    })
                                if resp['Status'] == 'Success':
                                    ebiz_method = self.env['account.payment.method.line'].search(
                                        [('journal_id', '=', payment_acq.journal_id.id),
                                         ('payment_method_id.code', '=', 'ebizcharge')], limit=1)
                                    payment = self.env['account.payment'].sudo().create({
                                        'journal_id': payment_acq.journal_id.id,
                                        'payment_method_id': ebiz_method.payment_method_id.id,
                                        'payment_method_line_id':ebiz_method.id,
                                        'partner_id': partner.id,
                                        'payment_reference': item['ref_num'],
                                        'amount': item['paid_amount'],
                                        'partner_type': 'customer',
                                        'payment_type': 'inbound',
                                        'ref': item['invoice_number_op'] if item['invoice_number_op'] else '',
                                    })
                                    payment.action_post()
                        if resp and resp['Status'] == 'Success':
                            success += 1
                            if self.payment_lines:
                                history_line = self.payment_lines.search(
                                    [('ref_num', '=', item['ref_num'])])
                                for l in history_line:
                                    self.payment_lines = [[2, l.id]]
                        else:
                            failed += 1
                            message_record['status'] = 'Failed'
                    except:
                        failed += 1
                        message_record['status'] = 'Failed'
                else:
                    failed += 1
                    message_record['status'] = 'Failed'

                message_lines.append([0, 0, message_record])

            odoo_logs = self.env['sync.logs'].create(list_of_invoices)
            for log in odoo_logs:
                self.write({
                    'transaction_log_lines': [[4, log.id]]
                })
            wizard = self.env['download.payment.message'].create({'name': 'Download', 'lines_ids': message_lines,
                                                                  'succeeded': success, 'failed': failed,
                                                                  'total': total})
            action = self.env.ref('payment_ebizcharge_crm.wizard_ebiz_download_message_action').read()[0]
            action['context'] = self._context
            action['res_id'] = wizard.id
            return action

        except Exception as e:
            _logger.exception(e)
            raise ValidationError(str(e))

    def create_payment_on_account(self, partner, item):
        payment_acq = self.env['payment.provider'].search(
            [('company_id', '=',
              partner.company_id.id if partner.company_id else self.env.company.id),
             ('code', '=', 'ebizcharge')])
        ebiz_method = self.env['account.payment.method.line'].search(
            [('journal_id', '=', payment_acq.journal_id.id),
             ('payment_method_id.code', '=', 'ebizcharge')], limit=1)
        payment = self.env['account.payment'].sudo().create({
            'journal_id': payment_acq.journal_id.id,
            'payment_method_id': ebiz_method.payment_method_id.id,
            'payment_method_line_id': ebiz_method.id,
            'partner_id': partner.id,
            'payment_reference': item['ref_num'],

            'amount': item['paid_amount'],
            'partner_type': 'customer',
            'payment_type': 'inbound',
            'ref': item['invoice_number_op'] if item['invoice_number_op'] else '',
        })
        payment.action_post()

    def action_pre_auth_or_deposit(self, item, order):
        token = self.env['payment.token'].search([('provider_ref', '=', item['provider_ref'])])
        acquirer = self.env['payment.provider'].search([('company_id', '=', order.company_id.id), ('code', '=', 'ebizcharge')])
        payment = self.with_context({'for_pre_auth': True}).sudo().create_payment(acquirer, item, order.partner_id)
        payment.payment_token_id = token.id
        transactions = payment.sudo().with_context({'default_order_id': order.id})._create_payment_transaction()
        transactions.sudo().write({
            'payment_id': payment.id,
            'sale_order_ids': [order.id],
            'invoice_ids': False,
            'transaction_type': 'pre_auth',
            'provider_reference': item['ref_num'],
            'ebiz_auth_code': item['auth_code'],
        })
        order.transaction_ids = [transactions.id]
        payment.payment_transaction_id = transactions.id
        payment.ref = transactions.reference
        transactions.sudo()._set_authorized()
        if item['payment_type'] == 'Sale':
            transactions.write({
                'transaction_type': 'deposit',
            })
            transactions.with_context({'from_email_link': True})._set_done()
            payment.sudo().action_post()
        order.request_amount = 0
        order.last_request_amount = 0
        order.ebiz_payment_link = 'applied'
        order.save_payment_link = False

    def create_log_lines(self, log):
        dict1 = {
            'type_id': log['type_id'],
            'invoice_number': log['invoice_number'],
            'partner_id': int(log['customer_id']),
            'customer_id': str(log['customer_id']),
            'date_paid': log['date_paid'],
            'invoice_amount': log['invoice_amount'],
            'paid_amount': log['paid_amount'],
            'amount_due': log['amount_due'],
            'payment_method': log['payment_method'],
            'auth_code': log['auth_code'],
            'ref_num': log['ref_num'],
            'currency_id': self.env.user.currency_id.id,
            'last_sync_date': datetime.now(),
        }
        return dict1

    @api.model
    def default_get(self, fields):
        res = super(DownloadEBizPayment, self).default_get(fields)
        if 'ebiz_profile_id' in res and res['ebiz_profile_id']:
            instance = self.env['ebizcharge.instance.config'].browse([res['ebiz_profile_id']])
            instance.action_update_profiles('ebiz.download.payments')
        res.update({
            'is_download_pressed': False,
        })
        return res

    def clear_logs(self, *args, **kwargs):
        if len(kwargs['values']) == 0:
            raise UserError('Please select a record first!')
        else:
            text = f"Are you sure you want to clear {len(kwargs['values'])} payment(s) from the Log?"
            wizard = self.env['wizard.delete.logs.download'].create({"record_id": self.id,
                                                                     "record_model": self._name,
                                                                     "text": text})
            action = self.env.ref('payment_ebizcharge_crm.wizard_delete_downloads_logs_action').read()[0]
            action['res_id'] = wizard.id
            action['context'] = dict(
                self.env.context,
                kwargs_values=kwargs['values'],
            )
            return action


class EBizPaymentLines(models.TransientModel):
    _name = 'ebiz.payment.lines'
    _description = "EBiz Payment Lines"

    wiz_id = fields.Many2one('ebiz.download.payments')
    check_box = fields.Boolean('Select')
    payment_internal_id = fields.Char('Payment Internal Id')
    partner_id = fields.Many2one('res.partner', 'Customer')
    customer_id = fields.Char('Customer ID')
    invoice_number = fields.Char('Invoice Number')
    invoice_number_op = fields.Char('Invoice Number #')
    invoice_internal_id = fields.Char('Invoice Internal Id')
    invoice_date = fields.Char('Invoice Date')
    invoice_due_date = fields.Char('Invoice Due Date')
    po_num = fields.Char('Po Num')
    so_num = fields.Char('So Num')
    invoice_amount = fields.Float('Invoice Total')
    amount_due = fields.Float('Balance Remaining')
    currency_id = fields.Many2one('res.currency')
    currency = fields.Char(string="Currency Name")
    auth_code = fields.Char('Auth Code')
    ref_num = fields.Char('Reference Number')
    payment_method = fields.Char('Payment Method')
    date_paid = fields.Char('Date Paid')
    paid_amount = fields.Float('Amount Paid')
    paid_amount_op = fields.Char('Amount Paid op')
    type_id = fields.Char('Type')
    payment_type = fields.Char('Payment Type')
    source = fields.Char('Source', default="Odoo")
    is_save_link = fields.Boolean(string="Save Link")
    is_email_payment = fields.Boolean('Is Email Pay', default=False)


class SyncLogs(models.Model):
    _name = 'sync.logs'
    _description = "Sync Logs"

    sync_date = fields.Datetime('Execution Date/Time', required=True, default=fields.Datetime.now)
    type_id = fields.Char('Type')
    currency_id = fields.Many2one('res.currency')
    invoice_number = fields.Char('Invoice Number')
    partner_id = fields.Many2one('res.partner', 'Customer')
    customer_id = fields.Char('Customer ID')
    date_paid = fields.Char('Date Paid')
    invoice_amount = fields.Float('Invoice Total')
    paid_amount = fields.Float('Amount Paid')
    amount_due = fields.Float('Balance Remaining')
    payment_method = fields.Char('Payment Method')
    auth_code = fields.Char('Auth Code')
    ref_num = fields.Char('Reference Number')
    last_sync_date = fields.Datetime(string="Import Date & Time")


class BatchProcessMessage(models.TransientModel):
    _name = "download.payment.message"
    _description = "Download Payment Message"

    name = fields.Char("Name")
    failed = fields.Integer("Failed")
    succeeded = fields.Integer("Succeeded")
    total = fields.Integer("Total")
    lines_ids = fields.One2many('download.payment.message.line', 'message_id')


class BatchProcessMessageLines(models.TransientModel):
    _name = "download.payment.message.line"
    _description = "Download Payment Message Line"

    customer_id = fields.Char('Customer ID')
    customer_name = fields.Char('Customer')
    invoice_no = fields.Char('Number')
    status = fields.Char('Status')
    message_id = fields.Many2one('download.payment.message')

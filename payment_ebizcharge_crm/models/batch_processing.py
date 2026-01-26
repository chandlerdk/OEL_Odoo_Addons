# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError, MissingError
import logging
from .ebiz_charge import message_wizard

_logger = logging.getLogger(__name__)


class BatchProcessing(models.Model):
    _name = 'batch.processing'
    _description = "Batch Processing"

    def domain_users(self):
        return [('create_uid', '=', self.env.user.id)]

    @api.model
    def get_card_type_selection(self):
        icons = self.env['payment.method'].search([]).read(['name'])
        icons_dict = {}
        for icon in icons:
            if not icon['name'][0] in icons_dict:
                icons_dict[icon['name'][0]] = icon['name']
        sel = list(icons_dict.items())
        return sel

    def get_default_company(self):
        companies = self.env['ebizcharge.instance.config'].search(
            [('is_active', '=', True), '|', ('company_ids', '=', False), (
                'company_ids', 'in', self._context.get('allowed_company_ids'))]).mapped(
            'company_ids').ids
        return companies

    def _default_location_id(self):
        return self.env['ebizcharge.instance.config']._default_instance_id()

    name = fields.Char(string='Batch Processing', default="Batch Processing")
    start_date = fields.Date(string='From Date')
    end_date = fields.Date(string='To Date')
    partner_id = fields.Many2one('res.partner', string='Select Customer',
                                 domain="[('ebiz_internal_id', '!=', False), ('ebiz_profile_id', '=', "
                                        "ebiz_profile_id)]")
    currency_id = fields.Many2one('res.currency')
    transaction_history_line = fields.One2many('sync.batch.processing', 'sync_transaction_id', copy=True)
    transaction_log_lines = fields.Many2many('sync.batch.processed', copy=True,
                                             domain=lambda self: self.domain_users())
    add_filter = fields.Boolean(string='Filters')
    send_receipt = fields.Boolean(string='Send receipt to customer')
    is_surcharge = fields.Boolean(string='sur')
    surcharge_terms = fields.Char(string="Surcharge Terms")
    company_ids = fields.Many2many('res.company', compute='compute_company', default=get_default_company)
    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config', string='EBizCharge Profile',
                                      default=_default_location_id)

    @api.depends('ebiz_profile_id')
    def compute_company(self):
        self.company_ids = self._context.get('allowed_company_ids')

    @api.onchange('send_receipt')
    def send_receipt_method(self):
        for i in self:
            for line in i.transaction_history_line:
                line.send_receipt = i.send_receipt

    def create_default_records(self):
        if self.start_date and self.end_date:
            if not self.start_date <= self.end_date:
                return message_wizard('From Date should be lower than the To date!', 'Invalid Date')
        profile_obj = self.env['ebizcharge.instance.config']
        profile = int(profile_obj.get_upload_instance(active_model='batch.processing', active_id=self))
        if profile:
            self.ebiz_profile_id = profile
            self.start_date = self.ebiz_profile_id._default_get_start()
            self.end_date = self.ebiz_profile_id._default_get_end_date()

        self.surcharge_terms = self.ebiz_profile_id.batch_terms
        filters = [('payment_state', '!=', 'paid'),
                   ('amount_residual', '>', 0),
                   ('ebiz_invoice_status', 'in', ('delete', 'default','partially_received')),
                   ('state', '=', 'posted'),
                   ('move_type', '=', 'out_invoice'),
                   ('date', '<=', self.end_date),
                   ('date', '>=', self.start_date), ('partner_id.ebiz_profile_id', '=', self.ebiz_profile_id.id)]
        if self.partner_id:
            filters.append(('partner_id', '=', self.partner_id.id))
        invoices = self.get_list_of_invoices(filters)
        list_of_invoices = [(5, 0, 0)]
        for invoice in invoices:
            line = (0, 0, invoice)
            list_of_invoices.append(line)
        self.update({
            'transaction_history_line': list_of_invoices,
            'is_surcharge': self.ebiz_profile_id.is_surcharge_enabled,
        })
        logs_filters = [('customer_name.ebiz_profile_id', '=', self.ebiz_profile_id.id)]
        if self.partner_id:
            logs_filters.append(('customer_name', '=', self.partner_id.id))
        logs = self.env['sync.batch.processed'].search(logs_filters).filtered(
            lambda i: i.date_paid.date() >= self.start_date and i.date_paid.date() <= self.end_date)
        list_of_logs = [(5, 0, 0)]
        temp_logs = []
        for log in logs:
            if log['name'] not in temp_logs:
                line = (0, 0, {
                    'name': log['name'],
                    'customer_name': int(log['customer_id']),
                    'customer_id': str(log['customer_id']),
                    'date_paid': log['date_paid'],
                    'currency_id': self.env.user.currency_id.id,
                    'amount_paid': log['amount_paid'],
                    'transaction_status': log['transaction_status'],
                    'email': log['email'],
                    'payment_method': log['payment_method'],
                    'auth_code': log['auth_code'],
                    'transaction_ref': log['transaction_ref'],
                })
                list_of_logs.append(line)
                temp_logs.append(log['name'])
        self.update({
            'transaction_log_lines': list_of_logs,
        })

    @api.model
    def read(self, fields, load='_classic_read'):
        self.create_default_records()
        resp = super(BatchProcessing, self).read(fields, load)
        return resp

    def js_flush_customer(self, ecom_side=None):
        rec = self.env['batch.processing'].search([])
        if rec:
            # rec.ebiz_profile_id = False
            rec.partner_id = False

    def search_transaction(self):
        batch_obj = self.env["sync.batch.processing"]
        try:
            if not self.start_date and not self.end_date and not self.partner_id:
                raise UserError('No Option Selected!')

            if not self.ebiz_profile_id:
                raise UserError('Please select an EBizCharge Merchant Account before refreshing the table.')

            if self.start_date and self.end_date:
                if not self.start_date <= self.end_date:
                    batch_obj.search([]).unlink()
                    return message_wizard('From Date should be lower than the To date!', 'Invalid Date')

            filters = [('payment_state', '!=', 'paid'),
                       ('amount_residual', '>', 0),
                       ('state', '=', 'posted'),
                       ('move_type', '=', 'out_invoice')]
            logs_filter = []

            if self.end_date:
                filters.append(('date', '<=', self.end_date))

            if self.start_date:
                filters.append(('date', '>=', self.start_date))

            if self.partner_id:
                filters.append(('partner_id', '=', self.partner_id.id))
                logs_filter.append(('customer_name', '=', self.partner_id.id))

            if self.ebiz_profile_id:
                filters.append(('partner_id.ebiz_profile_id', '=', self.ebiz_profile_id.id))
                logs_filter.append(('customer_name.ebiz_profile_id', '=', self.ebiz_profile_id.id))

            list_of_invoices = self.get_list_of_invoices(filters)
            odoo_logs = self.env['sync.batch.processed'].search(logs_filter).filtered(
                lambda i: i.date_paid.date() >= self.start_date and i.date_paid.date() <= self.end_date)
            self.update({
                'transaction_log_lines': [[6, 0, odoo_logs.ids]]
            })

            if list_of_invoices:
                list_of_invoices = list(map(lambda x: dict(x, **{'sync_transaction_id': self.id}), list_of_invoices))
                batch_obj.search([]).unlink()
                batch_obj.create(list_of_invoices)
            else:
                batch_obj.search([]).unlink()
        except Exception as e:
            _logger.exception(e)
            raise UserError(e)

    def get_list_of_invoices(self, filters):
        invoices = self.env['account.move'].search(filters)
        list_of_invoices = []
        if invoices:
            for invoice in invoices:
                default_credit_card = None
                partner = invoice.partner_id
                payment_methods = partner.ebiz_ach_tokens + partner.ebiz_credit_card_ids
                for token in payment_methods:
                    if token.is_default:
                        default_credit_card = token
                c_type = ''
                if default_credit_card:
                    card_types = self.get_card_type_selection()
                    card_types = {x[0]: x[1] for x in card_types}
                    if default_credit_card.card_type and default_credit_card.card_type != 'Unknown':
                        c_type = card_types[
                            'D' if default_credit_card.card_type == 'DS' else default_credit_card.card_type]
                    dict1 = {
                        'name': invoice['name'],
                        'customer_name': partner.id,
                        'email': partner.email,
                        'customer_id': str(partner.id),
                        'invoice_id': invoice.id,
                        'invoice_date': invoice.date,
                        'invoice_date_due': invoice.invoice_date_due,
                        'currency_id': invoice.currency_id.id,
                        'sales_person': self.env.user.id,
                        'amount': invoice.amount_total,
                        'amount_residual': invoice.amount_residual,
                        'payment_method': f'{c_type if c_type else "Account"} ending in {default_credit_card.payment_details[3:]}',
                        'default_card_id': default_credit_card.id,
                        'generated_link': invoice.save_payment_link,
                    }
                    list_of_invoices.append(dict1)
        return list_of_invoices

    def create_log_lines(self, invoices, current_record, send_receipt):
        selected_invoice = invoices
        list_of_invoices = []
        for invoice in selected_invoice:
            odoo_invoice = self.env['account.move'].browse(int(invoice['invoice_id'])).exists()
            transaction_id = odoo_invoice.transaction_ids[0]
            trans_status = 'Declined'
            if str(transaction_id.state) == 'done':
                trans_status = 'Success'
            dict1 = {
                "name": invoice['name'],
                "customer_name": odoo_invoice.partner_id.id,
                "customer_id": odoo_invoice.partner_id.id,
                "date_paid": transaction_id.last_state_change,
                "currency_id": invoice['currency_id'][0],
                "amount_paid": invoice['amount'],
                "transaction_status": trans_status,
                "payment_method": invoice['payment_method'],
                "auth_code": transaction_id.ebiz_auth_code,
                "transaction_ref": transaction_id.provider_reference,
                'email': invoice['email'] if send_receipt else "NA",
            }
            list_of_invoices.append(dict1)

        odoo_logs = self.env['sync.batch.processed'].create(list_of_invoices)
        for log in odoo_logs:
            current_record.write({
                'transaction_log_lines': [[4, log.id]]
            })


    def process_invoices(self, *args, **kwargs):
        """
            Niaz Implementation:
            Email the receipt to customer, if email receipts templates not there in odoo, it will fetch.
            return: wizard to select the receipt template
        """
        try:
            if 'values' in kwargs and len(kwargs['values']) == 0:
                raise UserError('Please select a record first!')

            if 'values' in kwargs and any(bool(val['generated_link']) for val in kwargs['values'] if bool(val['generated_link'])) and 'for_batch_processing' not in self.env.context:
                text = f"One or more documents selected have pending payment links. Processing payments for these documents will invalidate the existing links. Do you want to continue?"
                wizard = self.env['wizard.receive.email.payment.link'].create({"text": text})
                action = self.env.ref('payment_ebizcharge_crm.wizard_received_email_pay_payment_link').read()[0]
                action['res_id'] = wizard.id
                action['context'] = dict(
                    batch_processing=True,
                    kwargs=kwargs,
                )
                return action
            if 'values' in kwargs and 'for_batch_processing' not in self.env.context:
                lines = kwargs['values']
                send_receipt = kwargs['send_receipt']
            else:
                lines = self.env.context['kwargs']['values']
                send_receipt = self.env.context['kwargs']['send_receipt']

            success = 0
            total_count = len(lines)
            if not self:
                odooRecord = self.env['batch.processing'].create({
                    'start_date': self._default_get_start(),
                    'end_date': self._default_get_end_date(),
                })
                self = odooRecord

            message_lines = []
            for record in lines:
                search_invoice = self.env['account.move'].browse(int(record['invoice_id']))
                response = search_invoice.sync_to_ebiz()
                x = search_invoice.ebiz_batch_procssing_reg(record['default_card_id'], send_receipt)
                success += 1 if search_invoice.transaction_ids[0].state == 'done' else 0

                message_lines.append([0, 0, {'customer_id': record['customer_id'],
                                             "customer_name": record['customer_name'][1],
                                             'invoice_no': record['name'],
                                             'status': 'Success' if search_invoice.transaction_ids[
                                                                        0].state == 'done' else "Failed"}])
                if self.transaction_history_line:
                    history_line = self.transaction_history_line.search([('invoice_id', '=', record['invoice_id'])])
                    self.transaction_history_line = [[2, history_line.id]]

            self.create_log_lines(lines, self, send_receipt)
            wizard = self.env['batch.process.message'].create({'name': "Batch Process", 'lines_ids': message_lines,
                                                               'success_count': success, 'total': total_count})
            return {
                'type': 'ir.actions.act_window',
                'name': _('Batch Process Result'),
                'res_model': 'batch.process.message',
                'res_id': wizard.id,
                'target': 'new',
                'view_mode': 'form',
                'views': [[False, 'form']],
                'context': self._context
            }

        except MissingError as b:
            self.search_transaction()
            return {'type': 'ir.actions.act_window',
                    'name': _('Record Updated!!!'),
                    'res_model': 'message.wizard',
                    'target': 'new',
                    'view_mode': 'form',
                    'views': [[False, 'form']],
                    'context': {
                        'message': 'There was a change in the record, Invoices refreshed! Please try now',
                    },
                    }

        except Exception as e:
            _logger.exception(e)
            raise UserError(e)

    # def process_invoices(self, *args, **kwargs):
    #     """
    #         Niaz Implementation:
    #         Email the receipt to customer, if email receipts templates not there in odoo, it will fetch.
    #         return: wizard to select the receipt template
    #     """
    #     try:
    #         if 'values' in kwargs and len(kwargs['values']) == 0:
    #             raise UserError('Please select a record first!')

    #         success = 0
    #         total_count = len(kwargs['values'])

    #         message_lines = []
    #         for record in kwargs['values']:
    #             search_invoice = self.env['account.move'].browse(int(record['invoice_id']))
    #             search_invoice.sync_to_ebiz()
    #             search_invoice.ebiz_batch_procssing_reg(record['default_card_id'], kwargs['send_receipt'])
    #             success += 1 if search_invoice.transaction_ids[0].state == 'done' else 0
    #             message_lines.append([0, 0, {'customer_id': record['customer_id'],
    #                                          "customer_name": record['customer_name'][1],
    #                                          'invoice_no': record['name'],
    #                                          'status': 'Success' if search_invoice.transaction_ids[
    #                                                                     0].state == 'done' else "Failed"}])
    #             if self.transaction_history_line:
    #                 history_line = self.transaction_history_line.search([('invoice_id', '=', record['invoice_id'])])
    #                 self.transaction_history_line = [[2, history_line.id]]

    #         self.create_log_lines(kwargs['values'], self, kwargs['send_receipt'])
    #         wizard = self.env['batch.process.message'].create({'name': "Batch Process", 'lines_ids': message_lines,
    #                                                            'success_count': success, 'total': total_count})
    #         return {
    #             'type': 'ir.actions.act_window',
    #             'name': _('Batch Process Result'),
    #             'res_model': 'batch.process.message',
    #             'res_id': wizard.id,
    #             'target': 'new',
    #             'view_mode': 'form',
    #             'views': [[False, 'form']],
    #             'context': self._context
    #         }

    #     except MissingError as b:
    #         self.search_transaction()
    #         return {'type': 'ir.actions.act_window',
    #                 'name': _('Record Updated!!!'),
    #                 'res_model': 'message.wizard',
    #                 'target': 'new',
    #                 'view_mode': 'form',
    #                 'views': [[False, 'form']],
    #                 'context': {
    #                     'message': 'There was a change in the record, Invoices refreshed! Please try now',
    #                 },
    #                 }

    #     except Exception as e:
    #         _logger.exception(e)
    #         raise UserError(e)

    def clear_logs(self, *args, **kwargs):
        if len(kwargs['values']) == 0:
            raise UserError('Please select a record first!')
        list_of_records = []
        for record in kwargs['values']:
            filter_record = self.env['sync.batch.processed'].search(
                [('name', '=', record['name']), ('transaction_ref', '=', record['transaction_ref'])])
            if filter_record:
                list_of_records.append(filter_record.ids)
        text = f"Are you sure you want to clear {len(kwargs['values'])} invoice(s) from the Log?"
        wizard = self.env['wizard.delete.upload.logs'].create({"record_id": self.id,
                                                               "record_model": 'invoice',
                                                               "text": text})
        action = self.env.ref('payment_ebizcharge_crm.wizard_delete_upload_logs').read()[0]
        action['res_id'] = wizard.id
        action['context'] = dict(
            list_of_records=list_of_records,
            model='sync.batch.processed',
        )
        return action


class ListSyncBatch(models.Model):
    _name = 'sync.batch.processing'
    _order = 'date_time asc'
    _description = "Sync Batch Processing"

    sync_date = fields.Datetime(string='Execution Date/Time', required=True, default=fields.Datetime.now)
    sync_transaction_id = fields.Many2one('batch.processing', string='Partner Reference', required=True,
                                          ondelete='cascade', index=True, copy=False)
    name = fields.Char(string='Number')
    customer_name = fields.Many2one('res.partner', string='Customer')
    customer_id = fields.Char(string='Customer ID')
    invoice_id = fields.Char(string='Invoice ID')
    account_holder = fields.Char(string='Account Holder')
    date_time = fields.Datetime(string='Date Time')
    currency_id = fields.Many2one('res.currency', string='Company Currency')
    amount = fields.Float(string='Invoice Total')
    amount_residual = fields.Float(string='Balance')
    tax = fields.Char(string='Tax Excluded')
    card_no = fields.Char(string='Card Number')
    status = fields.Char(string='Status')
    email = fields.Char(string='Email')
    invoice_date = fields.Date(string='Invoice Date')
    invoice_date_due = fields.Date(string='Due Date')
    sales_person = fields.Many2one('res.users', string='Sales Person')
    payment_method = fields.Char(string='Payment Method')
    default_card_id = fields.Integer(string='Default Credit Card ID')
    send_receipt = fields.Boolean(string='Send receipt to customer')
    generated_link = fields.Char(string='Generated Link')

    
    def view_payment_methods(self, *args, **kwargs):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Payment Methods',
            'res_model': 'res.partner',
            'res_id': kwargs['values'],
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'new',
            'flags': {'mode': 'readonly'},
            'context': {'create': False},
        }


class SyncBatchProcessed(models.Model):
    _name = 'sync.batch.processed'
    _order = 'date_paid desc'
    _description = "Sync Batch Processed"

    sync_date = fields.Datetime(string='Execution Date/Time', required=True, default=fields.Datetime.now)
    name = fields.Char(string='Invoice Number')
    customer_name = fields.Many2one('res.partner', string='Customer')
    customer_id = fields.Char(string='Customer ID')
    date_paid = fields.Datetime(string='Date & Time Paid')
    currency_id = fields.Many2one('res.currency', string='Company Currency')
    amount_paid = fields.Float(string='Amount Paid')
    transaction_status = fields.Char(string='Transaction Status')
    email = fields.Char(string='Receipt Sent To (Email)')
    payment_method = fields.Char(string='Payment Method')
    auth_code = fields.Char(string='Auth Code')
    transaction_ref = fields.Char(string='Reference Number')


class SyncBatchLog(models.TransientModel):
    _name = 'sync.batch.log'
    _description = "Sync Batch Log"

    sync_date = fields.Datetime('Execution Date/Time', required=True, default=fields.Datetime.now)
    name = fields.Char(string='Invoice Number')
    customer_name = fields.Many2one('res.partner', string='Customer')
    customer_id = fields.Char(string='Customer ID')
    date_paid = fields.Datetime(string='Date & Time Paid')
    currency_id = fields.Many2one('res.currency', string='Company Currency')
    amount_paid = fields.Float(string='Amount Paid')
    transaction_status = fields.Char(string='Transaction Status')
    email = fields.Char(string='Receipt Sent To (Email)', related='customer_name.email')
    payment_method = fields.Char(string='Payment Method')
    auth_code = fields.Char(string='Auth Code')
    transaction_ref = fields.Char(string='Reference Number')


class BatchProcessMessage(models.TransientModel):
    _name = "batch.process.message"
    _description = "Batch Process Message"

    name = fields.Char(string="Name")
    success_count = fields.Integer(string="Success Count")
    total = fields.Integer(string="Total")
    lines_ids = fields.One2many('batch.processing.message.line', 'message_id')


class BatchProcessMessageLines(models.TransientModel):
    _name = "batch.processing.message.line"
    _description = "Batch Processing Message Line"

    customer_id = fields.Char(string='Customer ID')
    customer_name = fields.Char(string='Customer Name')
    invoice_no = fields.Char(string='Number')
    status = fields.Char(string='Status')
    message_id = fields.Many2one('batch.process.message')

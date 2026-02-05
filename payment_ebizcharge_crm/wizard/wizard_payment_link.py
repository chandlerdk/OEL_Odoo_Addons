# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError
from odoo.tools import float_compare
from werkzeug import urls

from odoo.addons.payment import utils as payment_utils


class PaymentLinkWizardInh(models.TransientModel):
    _inherit = 'payment.link.wizard'
    _description = "Generate Payment Link"

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        res_id = self.env.context.get('active_id')
        res_model = self.env.context.get('active_model')
        if res_id and res_model:
            res.update({'res_model': res_model, 'res_id': res_id})
            res.update(
                self.env[res_model].browse(res_id)._get_default_payment_link_values()
            )
            if res_model == 'account.move':
                self.env[res_model].browse(res_id).update({
                    'odoo_payment_link': True
                })
        return res


    @api.depends('amount', 'currency_id', 'partner_id', 'company_id')
    def _compute_link(self):
        for payment_link in self:
            related_document = self.env[payment_link.res_model].browse(payment_link.res_id)
            base_url = related_document.get_base_url()  # Don't generate links for the wrong website
            url_params = {
                'amount': self.amount,
                'access_token': self._get_access_token(),
                **self._get_additional_link_values(),
            }
            payment_link.link = f'{base_url}/payment/pay?{urls.url_encode(url_params)}'
            if payment_link.link:
                if payment_link.res_model in  ('account.move','sale.order'):
                    doc = self.env[payment_link.res_model].search([('id','=',payment_link.res_id)], limit=1)
                    if doc:
                        doc.update({
                            'odoo_payment_link_doc': payment_link.link
                        })
                        doc._log_pay_link()


class EBizPaymentLinkWizard(models.TransientModel):
    _name = "ebiz.payment.link.wizard"
    _description = "Generate Payment Link"

    @api.model
    def default_get(self, fields):
        res = super(EBizPaymentLinkWizard, self).default_get(fields)
        res_id = self._context.get('active_id')
        res_model = self._context.get('active_model')
        res.update({'res_id': res_id, 'res_model': res_model})
        amount_field = 'amount_residual' if res_model == 'account.move' else 'amount_total'
        if res_id and res_model == 'account.move':
            record = self.env[res_model].browse(res_id)
            res.update({
                'description': record.payment_reference,
                'amount': record[amount_field],
                'currency_id': record.currency_id.id,
                'partner_id': record.partner_id.id,
                'amount_max': record[amount_field],
            })
        if res_id and res_model == 'sale.order':
            record = self.env[res_model].browse(res_id)
            res.update({
                'description': record.name,
                'amount': record[amount_field],
                'currency_id': record.currency_id.id,
                'partner_id': record.partner_id.id,
                'amount_max': record[amount_field],
            })
        return res

    res_model = fields.Char('Related Document Model', required=True)
    res_id = fields.Integer('Related Document ID', required=True)
    amount = fields.Monetary(currency_field='currency_id', required=True)
    amount_max = fields.Monetary(currency_field='currency_id')
    currency_id = fields.Many2one('res.currency')
    partner_id = fields.Many2one('res.partner')
    partner_email = fields.Char(related='partner_id.email')
    link = fields.Char(string='Payment Link')
    description = fields.Char('Payment Ref')
    link_check_box = fields.Boolean('Link Check Box', default=False)
    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config')

    def _default_template(self):
        if 'default_ebiz_profile_id' in self.env.context:
            instances = self.env['ebizcharge.instance.config'].search(
                [('id', '=', self.env.context['default_ebiz_profile_id'])])
            self.env['email.templates'].search(
                [('instance_id', '=', self.env.context['default_ebiz_profile_id'])]).unlink()
        else:
            instances = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_active', '=', True)])
            self.env['email.templates'].search([]).unlink()
        for instance in instances:
            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            templates = ebiz.client.service.GetEmailTemplates(**{
                'securityToken': ebiz._generate_security_json(),
            })
            if templates:
                for template in templates:
                    odoo_temp = self.env['email.templates'].search(
                        [('template_id', '=', template['TemplateInternalId']), ('instance_id', '=', instance.id)])
                    if not odoo_temp:
                        if template['TemplateTypeId'] != 'TransactionReceiptMerchant' or template[
                            'TemplateTypeId'] != 'TransactionReceiptCustomer':
                            self.env['email.templates'].create({
                                'name': template['TemplateName'],
                                'template_id': template['TemplateInternalId'],
                                'template_subject': template['TemplateSubject'],
                                'template_description': template['TemplateDescription'],
                                'template_type_id': template['TemplateTypeId'],
                                'instance_id': instance.id,
                            })

        tem_check = self.env['email.templates'].search([('template_type_id', '=', 'WebFormEmail'), (
        'instance_id', '=', self.env.context.get('default_ebiz_profile_id'))])

        if tem_check:
            return tem_check[0].id
        else:
            return None

    select_template = fields.Many2one('email.templates', string='Select Template', default=_default_template)
    is_sale_order = fields.Boolean(string='Sale')
    transaction_type = fields.Selection([('pre_auth', 'Pre-Authorize'),
                                         ('deposit', 'Deposit')], string='Transaction Type', default='pre_auth',
                                        index=True)

    @api.onchange('amount', 'description')
    def _onchange_amount(self):
        # if float_compare(self.amount_max, self.amount, precision_rounding=self.currency_id.rounding or 0.01) == -1:
        #     raise ValidationError(_("Please set an amount smaller than %s.") % (self.amount_max))
        if self.amount <= 0:
            raise ValidationError(_("The value of the payment amount must be positive."))

    def generate_link(self):
        try:
            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=self.ebiz_profile_id)

            res_id = self._context.get('active_id')
            res_model = self._context.get('active_model')
            record = self.env[res_model].browse(res_id)
            if not self.select_template:
                if 'default_ebiz_profile_id' in self.env.context:
                    instances = self.env['ebizcharge.instance.config'].search(
                        [('id', '=', self.env.context['default_ebiz_profile_id'])])
                    self.env['email.templates'].search(
                        [('instance_id', '=', self.env.context['default_ebiz_profile_id'])]).unlink()
                else:
                    instances = self.env['ebizcharge.instance.config'].search(
                        [('is_valid_credential', '=', True), ('is_active', '=', True)])
                    self.env['email.templates'].search([]).unlink()
                for instance in instances:
                    ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
                    templates = ebiz.client.service.GetEmailTemplates(**{
                        'securityToken': ebiz._generate_security_json(),
                    })
                    if templates:
                        for template in templates:
                            odoo_temp = self.env['email.templates'].search(
                                [('template_id', '=', template['TemplateInternalId']), ('instance_id', '=', instance.id)])
                            if not odoo_temp:
                                if template['TemplateTypeId'] != 'TransactionReceiptMerchant' or template[
                                    'TemplateTypeId'] != 'TransactionReceiptCustomer':
                                    self.env['email.templates'].create({
                                        'name': template['TemplateName'],
                                        'template_id': template['TemplateInternalId'],
                                        'template_subject': template['TemplateSubject'],
                                        'template_description': template['TemplateDescription'],
                                        'template_type_id': template['TemplateTypeId'],
                                        'instance_id': instance.id,
                                    })
                template_type_id = 'WebFormEmail' if not self.is_sale_order else 'SalesOrderWebFormEmail'
                tem_check = self.env['email.templates'].search([('template_type_id', '=', template_type_id), (
                    'instance_id', '=', self.env.context.get('default_ebiz_profile_id'))])

                if tem_check:
                    self.select_template = tem_check[0].id
            if not self.select_template:
                raise UserError(
                    'Configuration required. Please set an email template inside the Admin Portal to generate payment link.')
            if self.is_sale_order and self.transaction_type == 'pre_auth' and self.amount < record.ebiz_amount_residual:
                raise UserError('Amount cannot be less than the original document amount for Pre-Auth.')

            fname = record.partner_id.name.split(' ')
            lname = ''
            for name in range(1, len(fname)):
                lname += fname[name]
            address = ''
            if record.partner_id.street:
                address += record.partner_id.street
            if record.partner_id.street2:
                address += ' ' + record.partner_id.street2
            try:
                lines = record.order_line
            except AttributeError:
                lines = record.invoice_line_ids
            get_merchant_data = False
            get_allow_credit_card_pay = False
            if record.partner_id.ebiz_profile_id:
                get_merchant_data = record.partner_id.ebiz_profile_id.merchant_data
                get_allow_credit_card_pay = record.partner_id.ebiz_profile_id.allow_credit_card_pay
            payment_method = 'CC'
            if get_merchant_data and get_allow_credit_card_pay:
                payment_method = 'CC,ACH'
            elif get_merchant_data:
                payment_method = 'ACH'
            elif get_allow_credit_card_pay:
                payment_method = 'CC'

            if 'from_bulk' in self.env.context:
                record.request_amount = self.env.context['requested_amount']
                record.last_request_amount = self.env.context['requested_amount']
            else:
                record.request_amount += self.amount
                record.last_request_amount = self.amount
            self.amount = record.last_request_amount if 'from_bulk' in self.env.context else self.amount

            if res_model == 'account.move':
                record.ebiz_payment_link = 'pending'
                if round(self.amount, 2) > round(record.amount_residual, 2):
                    raise UserError("Requested Amount cannot be greater than Invoice Amount.")
            ePaymentForm = {
                'FormType': 'PayLinkOnly',
                'FromEmail': 'support@ebizcharge.com',
                'FromName': 'EBizCharge',
                'EmailSubject': self.select_template.template_subject,
                'EmailAddress': self.partner_email if self.partner_email else ' ',
                'EmailTemplateID': self.select_template.template_id,
                'EmailTemplateName': self.select_template.name,
                'ShowSavedPaymentMethods': True,
                'CustFullName': record.partner_id.name,
                'TotalAmount': record.amount_total,
                'PayByType': payment_method,
                'AmountDue': self.amount,
                'ShippingAmount': self.amount,
                'CustomerId': record.partner_id.ebiz_customer_id or record.partner_id.id,
                #'ShowViewInvoiceLink': True if not self.is_sale_order else False,
                'SendEmailToCustomer': False,
                'TaxAmount': record.amount_tax,
                'SoftwareId': 'ODOOPayLinkOnly',
                #'InvoiceInternalId': record.ebiz_internal_id,
                'Description': 'Invoice' if not self.is_sale_order else 'SalesOrder',
                'DocumentTypeId': 'Invoice' if not self.is_sale_order else 'SalesOrder',
                'InvoiceNumber': str(record.id) if str(record.name) == '/' else str(record.name),
                'BillingAddress': {
                    "FirstName": fname[0],
                    "LastName": lname,
                    "CompanyName": record.partner_id.company_name if record.partner_id.company_name else '',
                    "Address1": address,
                    "City": record.partner_id.city if record.partner_id.city else '',
                    "State": record.partner_id.state_id.code or 'CA',
                    "ZipCode": record.partner_id.zip or '',
                    "Country": record.partner_id.country_id.code or 'US',
                },
                "LineItems": self._transaction_lines(lines),
            }
            if not self.is_sale_order:
                ePaymentForm['ShowViewInvoiceLink'] =  True
                ePaymentForm['InvoiceInternalId'] =  record.ebiz_internal_id
                ePaymentForm['PoNum'] = record.ref if record.ref else record.name
            if self.amount < 0 or self.amount == 0:
                raise UserError('Amount cannot be Zero/Negative.')
            if record.partner_id.ebiz_customer_id:
                ePaymentForm['CustomerId'] = record.partner_id.ebiz_customer_id
            if self.is_sale_order:
                command = 'Sale'
                if self.transaction_type == 'pre_auth':
                    command = 'AuthOnly'
                    ePaymentForm['PayByType'] = 'CC'
                record.transaction_type = self.transaction_type
                ePaymentForm['ProcessingCommand'] = command
                ePaymentForm['ShowViewSalesOrderLink'] = True
                ePaymentForm['SalesOrderInternalId'] = record.ebiz_internal_id
                ePaymentForm['PoNum'] = record.client_order_ref if record.client_order_ref else record.name
            if res_model == 'sale.order':
                ePaymentForm[
                    'Date'] = record.date_order if record.date_order else record.date_order if record.date_order else ''
            if res_model == 'account.move':
                ePaymentForm[
                    'Date'] = record.invoice_date if record.invoice_date else record.invoice_date_due if record.invoice_date_due else ''

            if record.save_payment_link:
                ebiz.client.service.DeleteEbizWebFormPayment(**{
                    'securityToken': ebiz._generate_security_json(),
                    'paymentInternalId': record.payment_internal_id,
                })
                if record and record.save_payment_link and not record.is_email_request:
                    message_log = 'EBizCharge Payment Link invalidated: ' + str(record.save_payment_link)
                    record.message_post(body=message_log)
                record.save_payment_link = False

            form_url = ebiz.client.service.GetEbizWebFormURL(**{
                'securityToken': ebiz._generate_security_json(),
                'ePaymentForm': ePaymentForm
            })
            if res_model in ('account.move', 'sale.order'):
                if record.is_email_request:
                    message_log = 'Email Pay Request sent to: ' + str(
                        record.email_for_pending) + '  has been invalidated'
                    record.message_post(body=message_log)
                elif record.save_payment_link:
                    message_log = 'EBizCharge Payment Link invalidated: ' + str(form_url)
                    record.message_post(body=message_log)

                record.save_payment_link = form_url
                record.is_email_request = False
                record.ebiz_invoice_status = 'delete'
                if record.save_payment_link:
                    message_log = 'New EBizCharge Payment Link has been generated: ' + str(form_url)
                    record.message_post(body=message_log)

            if self.link_check_box:
                record.save_payment_link = form_url
                record.payment_internal_id = form_url.split('=')[1]
            else:
                record.save_payment_link = form_url
                record.payment_internal_id = form_url.split('=')[1]
                return {'type': 'ir.actions.act_window',
                        'name': _('Copy Payment Link'),
                        'res_model': 'ebiz.payment.link.copy',
                        'target': 'new',
                        'view_mode': 'form',
                        'view_type': 'form',
                        'context': {
                            'default_link': form_url,
                        }}
        except Exception as e:
            raise ValidationError(e)


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


class EBizPaymentLink(models.TransientModel):
    _name = "ebiz.payment.link.copy"
    _description = "Copy Payment Link"

    link = fields.Char(string='Payment Link')
    copy_link_lines = fields.One2many('ebiz.payment.link.copy.line', 'wizard_id', string='Copy Lines')



class EBizPaymentLink(models.TransientModel):
    _name = "ebiz.payment.link.copy.line"
    _description = "Copy Payment Link Lines"

    link = fields.Char(string='Payment Link')
    wizard_id = fields.Many2one('ebiz.payment.link.copy', string='Copy line')
    invoice_id = fields.Many2one('account.move', string='Invoices')
    number = fields.Char(string='Number')



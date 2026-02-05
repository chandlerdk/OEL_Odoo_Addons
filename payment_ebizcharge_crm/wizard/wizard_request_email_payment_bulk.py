from odoo import fields, models, _, api
from datetime import datetime
import logging

_logger = logging.getLogger(__name__)
from odoo.exceptions import UserError, ValidationError


class EmailPaymentWizard(models.TransientModel):
    _name = 'ebiz.request.payment.bulk'
    _description = "EBiz Request Payment Bulk"

    payment_lines = fields.One2many('ebiz.payment.lines.bulk', 'wizard_id')

    def _default_template(self):
        if 'profile' in self.env.context:
            instances = self.env['ebizcharge.instance.config'].search(
                [('id', '=', self.env.context['profile'])])
            self.env['email.templates'].search(
                [('instance_id', '=', self.env.context['profile'])]).unlink()
        else:
            instances = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_active', '=', True)])
            self.env['email.templates'].search([]).unlink()

        for instance in instances:
            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            templates = ebiz.client.service.GetEmailTemplates(**{
                'securityToken': ebiz._generate_security_json()
            })
            if templates:
                for template in templates:
                    odoo_temp = self.env['email.templates'].search(
                        [('template_id', '=', template['TemplateInternalId']), ('instance_id', '=', instance.id)])
                    if not odoo_temp:
                        if template['TemplateTypeId'] != 'TransactionReceiptMerchant' and template[
                            'TemplateTypeId'] != 'TransactionReceiptCustomer':
                            self.env['email.templates'].create({
                                'name': template['TemplateName'],
                                'template_id': template['TemplateInternalId'],
                                'template_subject': template['TemplateSubject'],
                                'template_description': template['TemplateDescription'],
                                'template_type_id': template['TemplateTypeId'],
                                'instance_id': instance.id,
                            })
        partner = self.env['res.partner'].browse([self._context['partner']])
        tem_check = self.env['email.templates'].search([('template_type_id', '=', 'WebFormEmail'), ('instance_id', '=', partner.ebiz_profile_id.id)])
        if tem_check:
            return tem_check[0].id
        else:
            return None

    select_template = fields.Many2one('email.templates', string='Select Template', default=_default_template)
    email_subject = fields.Char(string='Subject', related='select_template.template_subject', readonly=False)
    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config', string='EBizCharge Merchant Account')

    def send_email(self):
        try:
            resp_lines = []
            success = 0
            failed = 0
            total_count = len(self.payment_lines)
            if not self.payment_lines:
                raise UserError('Please select a record first!')

            for record in self.payment_lines:
                invoice_id = self.env['account.move'].search([('id', '=', record.invoice_id)])
                instance = None
                if record.customer_name.ebiz_profile_id:
                    instance = record.customer_name.ebiz_profile_id

                ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
                resp_line = {}
                resp_line['customer_name'] = resp_line['customer_id'] = record.customer_name.id
                resp_line['number'] = record.invoice_id

                if record.email_id and '@' in record.email_id and '.' in record.email_id:
                    if invoice_id.state != 'posted':
                        invoice_id.action_post()

                    if not invoice_id.ebiz_internal_id:
                        invoice_id.sync_to_ebiz()

                    if invoice_id.amount_residual < record.amount_due:
                        raise UserError('Amount cannot be greater than amount due!')

                    fname = invoice_id.partner_id.name.split(' ')
                    lname = ''
                    for name in range(1, len(fname)):
                        lname += fname[name]

                    address = ''
                    if invoice_id.partner_id.street:
                        address += invoice_id.partner_id.street
                    if invoice_id.partner_id.street2:
                        address += ' ' + invoice_id.partner_id.street2

                    try:
                        lines = invoice_id
                    except AttributeError:
                        lines = invoice_id
                    get_merchant_data = False
                    get_allow_credit_card_pay = False
                    if invoice_id.partner_id.ebiz_profile_id:
                        get_merchant_data = invoice_id.partner_id.ebiz_profile_id.merchant_data
                        get_allow_credit_card_pay = invoice_id.partner_id.ebiz_profile_id.allow_credit_card_pay
                    payment_method = 'cc'
                    if get_merchant_data and get_allow_credit_card_pay:
                        payment_method = 'CC,ACH'
                    elif get_merchant_data:
                        payment_method = 'ACH'
                    # added due to version12 commit
                    elif get_allow_credit_card_pay:
                        payment_method = 'CC'

                    ePaymentForm = {
                        'FormType': 'EmailForm',
                        'FromEmail': 'support@ebizcharge.com',
                        'FromName': 'EBizCharge',
                        'EmailSubject': self.email_subject,
                        'EmailAddress': record.email_id,
                        'EmailTemplateID': self.select_template.template_id,
                        'EmailTemplateName': self.select_template.name,
                        'ShowSavedPaymentMethods': True,
                        'CustFullName': invoice_id.partner_id.name,
                        'TotalAmount': invoice_id.amount_total,
                        'AmountDue': record.amount_due,
                        'DocumentTypeId': 'Invoice',
                        'ShippingAmount': record.amount_due,
                        'PayByType': payment_method,
                        'CustomerId': invoice_id.partner_id.id,
                        'ShowViewInvoiceLink': True,
                        'SendEmailToCustomer': True,
                        'TaxAmount': invoice_id.amount_tax if invoice_id.amount_total==record.amount_due else 0,
                        'SoftwareId': 'Odoo CRM',
                        'Date': str(invoice_id.invoice_date) if invoice_id.invoice_date else '',
                        'InvoiceNumber': str(invoice_id.id) if str(invoice_id.name) == '/' else str(invoice_id.name),
                        'PoNum': invoice_id.ref if invoice_id.ref else invoice_id.name,
                        'BillingAddress': {
                            "FirstName": fname[0],
                            "LastName": lname,
                            "CompanyName": invoice_id.partner_id.company_name if invoice_id.partner_id.company_name else '',
                            "Address1": address,
                            "City": invoice_id.partner_id.city if invoice_id.partner_id.city else '',
                            "State": invoice_id.partner_id.state_id.code if invoice_id.partner_id.state_id.code else 'CA',
                            "ZipCode": invoice_id.partner_id.zip if invoice_id.partner_id.zip else '',
                            "Country": invoice_id.partner_id.country_id.code if invoice_id.partner_id.country_id.code else 'US',
                        },
                        "LineItems": self._transaction_lines(lines, amt_due=record.amount_due),
                    }

                    if invoice_id.partner_id.ebiz_customer_id:
                        ePaymentForm['CustomerId'] = invoice_id.partner_id.ebiz_customer_id

                    ePaymentForm[
                        'Date'] = invoice_id.invoice_date if invoice_id.invoice_date else invoice_id.invoice_date_due if invoice_id.invoice_date_due else ''
                    ePaymentForm['InvoiceInternalId'] = invoice_id.ebiz_internal_id
                    ePaymentForm['Description'] = 'Invoice'

                    form_url = ebiz.client.service.GetEbizWebFormURL(**{
                        'securityToken': ebiz._generate_security_json(),
                        'ePaymentForm': ePaymentForm
                    })

                    invoice_id.write({
                        'payment_internal_id': form_url.split('=')[1],
                        'ebiz_invoice_status': 'pending',
                        'date_time_sent_for_email': datetime.now(),
                        'email_for_pending': record.email_id,
                        'email_requested_amount': record.amount_due,
                        'email_received_payments': False,
                        'save_payment_link': form_url,
                        'no_of_times_sent': 1,
                    })

                    resp_line['status'] = 'Success'
                    success += 1
                    email_invoices_obj = self.env['payment.request.bulk.email'].search([])
                    if email_invoices_obj:
                        list_of_pending = []
                        partner = record.customer_name
                        odoo_invoice = self.env['account.move'].search([('id', '=', int(record.invoice_id))])
                        date_check = False
                        if odoo_invoice.date_time_sent_for_email:
                            date_check = 'due in 3 days' if (datetime.now() - odoo_invoice.date_time_sent_for_email).days <= 3 \
                                else '3 days overdue'
                        dict2 = (0, 0, {
                            'name': record['name'],
                            'customer_name': partner.id,
                            'customer_id': partner.id,
                            'invoice_id': record.invoice_id,
                            'invoice_date': odoo_invoice.date,
                            'email_id': record.email_id if record.email_id else partner.email,
                            'sales_person': self.env.user.id,
                            'amount': odoo_invoice.amount_total,
                            "currency_id": record.currency_id.id,
                            'amount_due': odoo_invoice.amount_residual_signed,
                            'tax': odoo_invoice.amount_untaxed_signed,
                            'date_and_time_Sent': odoo_invoice.date_time_sent_for_email or None,
                            'over_due_status': date_check if date_check else None,
                            'invoice_due_date': odoo_invoice.invoice_date_due,
                            'sync_transaction_id_pending': self.id,
                            'ebiz_status': 'Pending' if odoo_invoice.ebiz_invoice_status == 'pending' else odoo_invoice.ebiz_invoice_status,
                            'email_requested_amount': odoo_invoice.email_requested_amount,
                            'no_of_times_sent': odoo_invoice.no_of_times_sent,
                        })
                        list_of_pending.append(dict2)
                        for emailInvoice in email_invoices_obj:
                            if emailInvoice.transaction_history_line:
                                for line in emailInvoice.transaction_history_line:
                                    if line.invoice_id == record.invoice_id:
                                        emailInvoice.transaction_history_line = [[2, line.id]]
                            emailInvoice.write({
                                'transaction_history_line_pending': list_of_pending
                            })

                elif not record.email_id:
                    resp_line['status'] = 'Failed (No Email Address)'
                    failed += 1
                else:
                    resp_line['status'] = 'Failed (Wrong Email Address)'
                    failed += 1

                resp_lines.append([0, 0, resp_line])

            else:
                wizard = self.env['wizard.email.pay.message'].create({'name': 'email_pay', 'lines_ids': resp_lines,
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
                        'context':
                            self._context,
                        }

        except Exception as e:
            raise ValidationError(e)



    def _transaction_line(self, line):
        if line.price_subtotal != 0:
            qty = line.product_uom_qty if hasattr(line, 'product_uom_qty') else line.quantity
            taxable = False
            tax = 0
            if line._name == 'account.move.line':
                taxable = True if line.tax_ids else False
                base_line = line._convert_to_tax_base_line_dict()
                to_update_vals, tax_values_list = line.env['account.tax']._compute_taxes_for_single_line(base_line)
                tax = sum([x['tax_amount'] for x in tax_values_list if 'tax_amount' in x])
            elif line._name == 'sale.order.line':
                taxable = True if line.tax_id else False
                tax = line.price_tax
            return {
                'SKU': line.product_id.id,
                'ProductName': line.product_id.name,
                'Description': line.name,
                'UnitPrice': line.price_unit,
                'Taxable': taxable,
                'TaxAmount': tax if taxable else 0,
                'Qty': str(qty),
                'DiscountRate': line.discount,
            }

    def _transaction_lines(self, lines, amt_due=None):
        item_list = []
        trans_amount = amt_due
        
        if trans_amount==lines.amount_total:
            order_lines = lines.order_line if lines._name=='sale.order' else lines.invoice_line_ids
            
            for line in order_lines:
                item_list.append(self._transaction_line(line))
        else:
            description = ''
            if lines._name == "account.move":
                description = 'Inv# ' + str(lines.name)
            if lines._name == "sale.order":
                description = 'Order# ' + str(lines.name)
   
            item_list.append({
            'SKU': lines.name,
            'ProductName': description,
            'Description': description,
            'UnitPrice': trans_amount,
            'Taxable': 0,
            'TaxAmount': 0,
            'Qty': 1,
            'DiscountRate': 0,
        })
        return {'TransactionLineItem': item_list}


class EBizPaymentLines(models.TransientModel):
    _name = 'ebiz.payment.lines.bulk'
    _description = "EBiz Payment Lines Bulk"

    wizard_id = fields.Many2one('ebiz.request.payment.bulk')

    def _default_template(self):
        instance = None
        if self.customer_name.ebiz_profile_id:
            instance = self.customer_name.ebiz_profile_id.id

        tem_check = self.env['email.templates'].search(
            [('template_type_id', '=', 'WebFormEmail'), ('instance_id', '=', instance)])
        if tem_check:
            return tem_check[0].id
        else:
            return None

    name = fields.Char(string='Number')
    customer_name = fields.Many2one('res.partner', string='Customer')
    amount_due = fields.Float(string='Amount Due')
    check_box = fields.Boolean('Select')
    email_id = fields.Char(string='Email ID')
    invoice_id = fields.Char('Invoice ID')
    currency_id = fields.Many2one('res.currency', string='Company Currency')
    select_template = fields.Many2one('email.templates', string='Select Template')
    email_subject = fields.Char(string='Subject', related='select_template.template_subject', readonly=False)
    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config')

    @api.model_create_multi
    def create(self, vals_list):
        res = super().create(vals_list)
        for rec, vals in zip(res, vals_list):
            if 'ebiz_profile_id' in vals:
                tem_check = self.env['email.templates'].search(
                    [('template_type_id', '=', 'WebFormEmail'), ('instance_id', '=', vals['ebiz_profile_id'])])
                if tem_check:
                    rec.select_template = tem_check[0].id
        return res

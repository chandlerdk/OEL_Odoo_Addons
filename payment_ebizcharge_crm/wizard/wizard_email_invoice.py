from odoo import models, api, fields
from odoo.exceptions import UserError, ValidationError
from datetime import datetime
from zeep import Client
from ..models.ebiz_charge import message_wizard


class EmailInvoice(models.TransientModel):
    _name = 'email.invoice'
    _description = "Email Invoice"

    partner_ids = fields.Many2many('res.partner', string='Customer')
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

        tem_check = self.env['email.templates'].search([('template_type_id', '=', 'WebFormEmail'), (
            'instance_id', '=', self.env.context.get('default_ebiz_profile_id'))])
        if tem_check:
            return tem_check[0].id
        else:
            return None

    select_template = fields.Many2one('email.templates', string='Select Template', default=_default_template)
    email_subject = fields.Char(string='Subject', related='select_template.template_subject', readonly=False)
    record_id = fields.Char(string='Record ID')
    model_name = fields.Char(string='Model Name')
    email_customer = fields.Char('')
    amount = fields.Monetary(string='Amount')
    currency_id = fields.Many2one("res.currency", string="Currency", readonly=True, required=True)

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

    def _transaction_lines(self, lines):
        item_list = []
        trans_amount = self.amount
        
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

    def send_email(self):
        try:
            instance = None
            if self.partner_ids.ebiz_profile_id:
                instance = self.partner_ids.ebiz_profile_id

            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            sale_order = self.env['account.move'].search([('id', '=', self.record_id)])

            if self.env.context.get('active_model') == 'sale.order':
                if sale_order.invoice_ids:
                    if sale_order.invoice_ids.amount_residual < self.amount:
                        raise UserError('Amount cannot be greater than amount due!')
                else:
                    if sale_order.amount_total < self.amount:
                        raise UserError('Amount cannot be greater than amount due!')
            else:
                if sale_order.amount_residual < self.amount:
                    raise UserError('Amount cannot be greater than amount due!')

            if '@' not in self.email_customer or '.' not in self.email_customer:
                raise UserError('You might have entered the wrong Email Address!')

            fname = sale_order.partner_id.name.split(' ')
            lname = ''
            for name in range(1, len(fname)):
                lname += fname[name]

            address = ''
            if sale_order.partner_id.street:
                address += sale_order.partner_id.street
            if sale_order.partner_id.street2:
                address += ' ' + sale_order.partner_id.street2
            try:
                lines = sale_order
            except AttributeError:
                lines = sale_order
            get_merchant_data = False
            get_allow_credit_card_pay = False
            if sale_order.partner_id.ebiz_profile_id:
                get_merchant_data = sale_order.partner_id.ebiz_profile_id.merchant_data
                get_allow_credit_card_pay = sale_order.partner_id.ebiz_profile_id.allow_credit_card_pay
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
                'EmailAddress': self.email_customer,
                'EmailTemplateID': self.select_template.template_id,
                'EmailTemplateName': self.select_template.name,
                'ShowSavedPaymentMethods': True,
                'CustFullName': sale_order.partner_id.name,
                'TotalAmount': sale_order.amount_total,
                'AmountDue': self.amount,
                'CustomerId': sale_order.partner_id.ebiz_customer_id or sale_order.partner_id.id,
                'SendEmailToCustomer': True,
                'TaxAmount': sale_order.amount_tax if self.amount==sale_order.amount_total else 0,
                'PayByType': payment_method,
                'SoftwareId': 'Odoo CRM',
                'InvoiceNumber': str(sale_order.id) if str(sale_order.name) == '/' else str(sale_order.name),
                'BillingAddress': {
                    "FirstName": fname[0],
                    "LastName": lname,
                    "CompanyName": sale_order.partner_id.company_name if sale_order.partner_id.company_name else '',
                    "Address1": address,
                    "City": sale_order.partner_id.city if sale_order.partner_id.city else '',
                    "State": sale_order.partner_id.state_id.code or 'CA',
                    "ZipCode": sale_order.partner_id.zip or '',
                    "Country": sale_order.partner_id.country_id.code or 'US',
                },
                "LineItems": self._transaction_lines(lines),
            }
            if sale_order.partner_id.ebiz_customer_id:
                ePaymentForm['CustomerId'] = sale_order.partner_id.ebiz_customer_id

            if self.env.context.get('active_model') == 'sale.order':
                ePaymentForm['Date'] = sale_order.date_order.date()
                ePaymentForm['SalesOrderInternalId'] = sale_order.ebiz_internal_id
                ePaymentForm['DocumentTypeId'] = 'SalesOrder'
                ePaymentForm['ShowViewSalesOrderLink'] = True
                ePaymentForm['Description'] = 'SalesOrder'
                ePaymentForm['PoNum'] = sale_order.client_order_ref if sale_order.client_order_ref else sale_order.name
            else:
                ePaymentForm[
                    'Date'] = sale_order.invoice_date if sale_order.invoice_date else sale_order.invoice_date_due if sale_order.invoice_date_due else ''
                ePaymentForm['InvoiceInternalId'] = sale_order.ebiz_internal_id
                ePaymentForm['Description'] = 'Invoice'
                ePaymentForm['ShowViewInvoiceLink'] = True
                ePaymentForm['DocumentTypeId'] = 'Invoice'
                ePaymentForm['PoNum'] = sale_order.ref if sale_order.ref else sale_order.name

            form_url = ebiz.client.service.GetEbizWebFormURL(**{
                'securityToken': ebiz._generate_security_json(),
                'ePaymentForm': ePaymentForm
            })

            if self.env.context.get('active_model') == 'sale.order':
                sale_order.action_confirm()
                sale_order.write({
                    'ebiz_invoice_status': 'pending',
                    'payment_internal_id': form_url.split('=')[1],
                })
            else:
                sale_order.write({
                    'payment_internal_id': form_url.split('=')[1],
                    'ebiz_invoice_status': 'pending',
                    'date_time_sent_for_email': datetime.now(),
                    'email_for_pending': self.email_customer,
                    'email_received_payments': False,
                    'is_email_request': True,
                    'email_requested_amount': self.amount,
                    'save_payment_link': form_url,
                    'no_of_times_sent': 1,
                })
                if sale_order:
                    message_log = 'New Email Pay Request has been sent to: '+str(self.email_customer)
                    sale_order.message_post(body=message_log)

            return message_wizard('Email pay request has been sent successfully!')

        except Exception as e:
            raise ValidationError(e)


class EmailInvoiceMultiple(models.TransientModel):
    _name = 'multiple.email.invoice'
    _description = "Multiple Email Invoice"

    partner_ids = fields.Many2many('res.partner', string='Customer')
    invoice_ids = fields.Many2many('account.move', string='Invoice')
    select_template = fields.Many2one('email.templates', string='Select Template')
    email_subject = fields.Char(string='Subject')
    record_id = fields.Char(string='Record ID')
    model_name = fields.Char(string='Model Name')
    email_customer = fields.Char('', related='partner_ids.email', readonly=True)
    amount = fields.Monetary(string='Amount')
    currency_id = fields.Many2one("res.currency", string="Currency", readonly=True, required=True)



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

    def _transaction_lines(self, lines):
        item_list = []
        trans_amount = self.amount
        
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

    def send_email(self):
        try:
            instance = None
            if self.partner_ids.ebiz_profile_id:
                instance = self.partner_ids.ebiz_profile_id
            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            sale_order = self.env[self.env.context.get('active_model')].browse(self.env.context.get('active_id'))
            if not sale_order.partner_id.email:
                raise UserError(f'"{sale_order.partner_id.name}" does not contain Email Address!')

            fname = sale_order.partner_id.name.split(' ')
            lname = ''
            for name in range(1, len(fname)):
                lname += fname[name]

            address = ''
            if sale_order.partner_id.street:
                address += sale_order.partner_id.street
            if sale_order.partner_id.street2:
                address += ' ' + sale_order.partner_id.street2
            try:
                lines = sale_order
            except AttributeError:
                lines = sale_order

            ePaymentForm = {
                'FormType': 'EmailForm',
                'FromEmail': 'support@ebizcharge.com',
                'FromName': 'EBizCharge',
                'EmailSubject': self.select_template.template_subject,
                'EmailAddress': sale_order.partner_id.email,
                'EmailTemplateID': self.select_template.template_id,
                'EmailTemplateName': self.select_template.name,
                'ShowSavedPaymentMethods': True,
                'CustFullName': sale_order.partner_id.name,
                'TotalAmount': sale_order.amount_total,
                'AmountDue': self.amount,
                'CustomerId': sale_order.partner_id.ebiz_customer_id,
                'SendEmailToCustomer': True,
                'TaxAmount': sale_order.amount_tax if self.amount==sale_order.amount_total else 0,
                'InvoiceNumber': str(sale_order.id),
                'BillingAddress': {
                    "FirstName": fname[0],
                    "LastName": lname,
                    "CompanyName": sale_order.partner_id.company_name if sale_order.partner_id.company_name else '',
                    "Address1": address,
                    "City": sale_order.partner_id.city if sale_order.partner_id.city else '',
                    "State": sale_order.partner_id.state_id.code if sale_order.partner_id.state_id.code else 'CA',
                    "ZipCode": sale_order.partner_id.zip if sale_order.partner_id.zip else '',
                    "Country": sale_order.partner_id.country_id.code if sale_order.partner_id.country_id.code else 'US',
                },
                "LineItems": self._transaction_lines(lines),
            }

            if sale_order.partner_id.ebiz_customer_id:
                ePaymentForm['CustomerId'] = sale_order.partner_id.ebiz_customer_id

            if self.env.context.get('active_model') == 'sale.order':
                ePaymentForm['Date'] = sale_order.date_order.date()
                ePaymentForm['ShowViewSalesOrderLink'] = True
                ePaymentForm['DocumentTypeId'] = 'SalesOrder'
                ePaymentForm['Description'] = 'SalesOrder'
                ePaymentForm['SalesOrderInternalId'] = sale_order.ebiz_internal_id
                ePaymentForm['PoNum'] = sale_order.client_order_ref if sale_order.client_order_ref else sale_order.name
            else:
                ePaymentForm[
                    'Date'] = sale_order.invoice_date if sale_order.invoice_date else sale_order.invoice_date_due if sale_order.invoice_date_due else ''
                ePaymentForm['ShowViewInvoiceLink'] = True
                ePaymentForm['DocumentTypeId'] = 'Invoice'
                ePaymentForm['Description'] = 'Invoice'
                ePaymentForm['InvoiceInternalId'] = sale_order.ebiz_internal_id
                ePaymentForm['PoNum'] = sale_order.ref if sale_order.ref else sale_order.name

            form_url = ebiz.client.service.GetEbizWebFormURL(**{
                'securityToken': ebiz._generate_security_json(),
                'ePaymentForm': ePaymentForm
            })

            if self.env.context.get('active_model') == 'sale.order':
                sale_order.action_confirm()
                sale_order.write({
                    'ebiz_invoice_status': 'pending',
                    'payment_internal_id': form_url.split('=')[1],
                })
            else:
                sale_order.write({
                    'payment_internal_id': form_url.split('=')[1],
                    'is_email_request': True,
                    'ebiz_invoice_status': 'pending',
                })

            return message_wizard('Email has been sent successfully!')

        except Exception as e:
            raise ValidationError(e)


class EmailInvoiceMultiplePayments(models.TransientModel):
    _name = 'multiple.email.invoice.payments'
    _description = "Multiple Email Invoice Payments"

    partner_ids = fields.Many2many('res.partner', string='Customer')
    invoice_ids = fields.Many2many('account.move', string='Invoice')
    select_template = fields.Many2one('email.templates', string='Select Template')
    email_subject = fields.Char(string='Subject')
    record_id = fields.Char(string='Record ID')
    model_name = fields.Char(string='Model Name')
    email_customer = fields.Char('', related='partner_ids.email', readonly=True)
    amount = fields.Monetary(string='Amount')
    currency_id = fields.Many2one("res.currency", string="Currency", readonly=True, required=True)

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

    def _transaction_lines(self, lines):
        item_list = []
        trans_amount = self.amount
        
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

    def send_email(self):
        try:
            sale_order = self.env[self.env.context.get('active_model')].browse(self.env.context.get('active_id'))
            instance = None
            if sale_order.partner_id.ebiz_profile_id:
                instance = sale_order.partner_id.ebiz_profile_id

            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            if not sale_order.partner_id.email:
                raise UserError(f'"{sale_order.partner_id.name}" does not contain Email Address!')

            fname = sale_order.partner_id.name.split(' ')
            lname = ''
            for name in range(1, len(fname)):
                lname += fname[name]

            address = ''
            if sale_order.partner_id.street:
                address += sale_order.partner_id.street
            if sale_order.partner_id.street2:
                address += ' ' + sale_order.partner_id.street2

            try:
                lines = sale_order
            except AttributeError:
                lines = sale_order

            ePaymentForm = {
                'FormType': 'EmailForm',
                'FromEmail': 'support@ebizcharge.com',
                'FromName': 'EBizCharge',
                'EmailSubject': self.select_template.template_subject,
                'EmailAddress': sale_order.partner_id.email,
                'EmailTemplateID': self.select_template.template_id,
                'EmailTemplateName': self.select_template.name,
                'ShowSavedPaymentMethods': True,
                'CustFullName': sale_order.partner_id.name,
                'TotalAmount': sale_order.amount_total,
                'AmountDue': self.amount,
                'CustomerId': sale_order.partner_id.ebiz_customer_id,
                'SendEmailToCustomer': True,
                'TaxAmount': sale_order.amount_tax if self.amount==sale_order.amount_total else 0,
                'InvoiceNumber': str(sale_order.id),
                'BillingAddress': {
                    "FirstName": fname[0],
                    "LastName": lname,
                    "CompanyName": sale_order.partner_id.company_name if sale_order.partner_id.company_name else '',
                    "Address1": address,
                    "City": sale_order.partner_id.city if sale_order.partner_id.city else '',
                    "State": sale_order.partner_id.state_id.code if sale_order.partner_id.state_id.code else 'CA',
                    "ZipCode": sale_order.partner_id.zip if sale_order.partner_id.zip else '',
                    "Country": sale_order.partner_id.country_id.code if sale_order.partner_id.country_id.code else 'US',
                },
                "LineItems": self._transaction_lines(lines),
            }

            if sale_order.partner_id.ebiz_customer_id:
                ePaymentForm['CustomerId'] = sale_order.partner_id.ebiz_customer_id

            if self.env.context.get('active_model') == 'sale.order':
                ePaymentForm['Date'] = sale_order.date_order.date()
                ePaymentForm['ShowViewSalesOrderLink'] = True
                ePaymentForm['DocumentTypeId'] = 'SalesOrder'
                ePaymentForm['Description'] = 'SalesOrder'
                ePaymentForm['SalesOrderInternalId'] = sale_order.ebiz_internal_id
                ePaymentForm['PoNum'] = sale_order.client_order_ref if sale_order.client_order_ref else sale_order.name
            else:
                ePaymentForm[
                    'Date'] = sale_order.invoice_date if sale_order.invoice_date else sale_order.invoice_date_due if sale_order.invoice_date_due else ''
                ePaymentForm['ShowViewInvoiceLink'] = True
                ePaymentForm['DocumentTypeId'] = 'Invoice'
                ePaymentForm['Description'] = 'Invoice'
                ePaymentForm['InvoiceInternalId'] = sale_order.ebiz_internal_id
                ePaymentForm['PoNum'] = sale_order.ref if sale_order.ref else sale_order.name

            form_url = ebiz.client.service.GetEbizWebFormURL(**{
                'securityToken': ebiz._generate_security_json(),
                'ePaymentForm': ePaymentForm
            })

            if self.env.context.get('active_model') == 'sale.order':
                sale_order.action_confirm()
                sale_order.write({
                    'ebiz_invoice_status': 'pending',
                    'payment_internal_id': form_url.split('=')[1],
                })
            else:
                sale_order.write({
                    'payment_internal_id': form_url.split('=')[1],
                    'is_email_request': True,
                    'ebiz_invoice_status': 'pending',
                })

            return message_wizard('Email has been sent successfully!')

        except Exception as e:
            raise ValidationError(e)

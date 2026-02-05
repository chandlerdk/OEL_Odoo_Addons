# -*- coding: utf-8 -*-
from datetime import datetime
from zeep import Client
from odoo.exceptions import ValidationError


def message_wizard(message, title="Success"):
    context = dict()
    context['message'] = message
    return {
        'name': title,
        'view_type': 'form',
        'view_mode': 'form',
        'views': [[False, 'form']],
        'res_model': 'message.wizard',
        'view_id': False,
        'type': 'ir.actions.act_window',
        'target': 'new',
        'context': context
    }





class EBizChargeAPI:
    """
    EBizCharge API Middleware
    """

    def __init__(self, security_key, user_id, password):
        """
        Initialize EBizCharge Object
        """
        self.url = 'https://soapapi1.ebizcharge.net/v2/wsdl/ebizsoap1.wsdl'
        self.client = Client(wsdl=self.url)
        self.security_key = security_key
        self.user_id = user_id
        self.password = password

    def _generate_security_json(self):
        security_json = {
            'SecurityId': self.security_key,
            'UserId': self.user_id,
            'Password': self.password
        }
        return security_json

    def _prepare_partner_contact_info(self, partner):
        values = {}
        if partner.phone:
            values['Phone'] = partner.phone
        if partner.mobile:
            values['CellPhone'] = partner.mobile
        if partner.email:
            values['Email'] = partner.email
        return values

    def _add_customer_params(self, partner):
        addr = partner.address_get(['delivery', 'invoice'])
        name_array = partner.name.split(' ')
        first_name = name_array[0]
        if len(name_array) >= 2:
            last_name = " ".join(name_array[1:])
        else:
            last_name = ""
        customer = {
            "SoftwareId": "Odoo CRM",
            "CustomerId": partner.id,
            "FirstName": first_name,
            "LastName": last_name,
            "CompanyName": partner.parent_id.name if partner.parent_id else partner.name or "",
            'BillingAddress': self._get_customer_address(partner.browse(addr['invoice'])),
            'ShippingAddress': self._get_customer_address(partner.browse(addr['delivery']))
        }
        if partner.company_id:
            division = str(partner.company_id.id)
            customer['DivisionId'] = division
        values = self._prepare_partner_contact_info(partner)
        customer.update(values)
        sync_object = {
            'securityToken': self._generate_security_json(),
            'customer': customer
        }
        return sync_object

    def add_customer(self, partner):
        customer_params = self._add_customer_params(partner)
        res = self.client.service.AddCustomer(**customer_params)
        if res['ErrorCode'] == 2:
            get_resp = self.get_customer(partner.id)
            partner.write({
                'ebiz_internal_id': get_resp['CustomerInternalId'],
                'ebiz_customer_id': get_resp['CustomerId'],
                "ebizcharge_customer_token": get_resp['CustomerToken']
            })
            res = self.update_customer(partner)
        return res

    def _update_customer_params(self, partner):
        customer_params = self._add_customer_params(partner)
        customer_params['customerId'] = partner.id
        return customer_params

    def _get_customer_params(self, partner_id):
        return {
            'securityToken': self._generate_security_json(),
            'customerId': partner_id
        }

    def update_customer(self, partner):
        customer_params = self._update_customer_params(partner)
        return self.client.service.UpdateCustomer(**customer_params)

    def get_customer(self, partner_id):
        customer_params = self._get_customer_params(partner_id)
        res = self.client.service.GetCustomer(**customer_params)
        return res

    def get_customer_token(self, partner_id):
        try:
            customer_params = {
                'securityToken': self._generate_security_json(),
                'CustomerId': partner_id
            }
            res = self.client.service.GetCustomerToken(**customer_params)
            return res
        except Exception as e:
            raise ValidationError(e)

    def _so_line_params(self, line, item_no):
        item = {
            "ItemId": line.product_id.id,
            "Name": line.product_id.name,
            "Description": line.product_id.description,
            "UnitPrice": line.price_unit,
            "Qty": line.product_uom_qty,
            "Taxable": True if line.tax_id else False,
            "TaxRate": 0,
            "GrossPrice": 0,
            "WarrantyDiscount": 0,
            "SalesDiscount": line.discount,
            "UnitOfMeasure": line.product_uom.name,
            "TotalLineAmount": line.price_subtotal,
            "TotalLineTax": line.price_tax,
            "ItemLineNumber": item_no,
        }
        return item

    def _so_lines_params(self, lines):
        lines_list = []
        for i, line in enumerate(lines):
            if line.price_subtotal != 0:
                lines_list.append(self._so_line_params(line, i + 1))
        return lines_list

    def _add_so_params(self, order):
        array_of_items = self.client.get_type('ns0:ArrayOfItem')
        division = ''
        if order.company_id:
            division = str(order.company_id.id)
        software = 'ODOO CRM'
        web_sale = order.env['ir.module.module'].sudo().search(
            [('name', '=', 'website_sale'), ('state', 'in', ['installed', 'to upgrade', 'to remove'])])
        if web_sale and order.website_id:
            software = 'ODOO WEB'
        order = {
            "CustomerId": order.partner_id.id,
            "SubCustomerId": "",
            "SalesOrderNumber": order.name,
            "Date": str(order.date_order.date()),
            "Amount": order.amount_total,
            "DueDate": str(order.date_order.date()),
            "AmountDue": order.amount_total,
            "TypeId": "Invoice",
            "Software": software,
            "DivisionId": division,
            "NotifyCustomer": False,
            "EmailTemplateID": "",
            "URL": "",
            "TotalTaxAmount": order.amount_tax,
            "UniqueId": "",
            "Description": "Sale Order" if order.state in ['done', 'sale'] else "Quotation",
            "CustomerMessage": "",
            "Memo": "",
            "ShipVia": "",
            "SalesRepId": order.user_id.id,
            "TermsId": "",
            "IsToBeEmailed": 0,
            "IsToBePrinted": 0,
            "Items": array_of_items(self._so_lines_params(order.order_line))
        }
        return order

    def _add_order_params(self, order):
        sync_object = {
            'securityToken': self._generate_security_json(),
            'salesOrder': self._add_so_params(order)
        }
        return sync_object

    def sync_sale_order(self, order):
        so_params = self._add_order_params(order)
        res = self.client.service.AddSalesOrder(**so_params)
        return res

    def _update_sale_order_params(self, order):
        so_params = self._add_order_params(order)
        so_params.update({
            'customerId': order.partner_id.id,
            'salesOrderNumber': order.name,
            'salesOrderInternalId': order.ebiz_internal_id
        })
        return so_params

    def update_sale_order(self, order):
        so_params = self._update_sale_order_params(order)
        res = self.client.service.UpdateSalesOrder(**so_params)
        return res

    def _payment_profile_credit_card(self, profile):
        if type(profile) != dict:
            credit_card = {
                "AccountHolderName": profile.account_holder_name,
                "MethodType": "CreditCard",
                "CardExpiration": "%s-%s" % (profile.card_exp_year, profile.card_exp_month),
                "AvsStreet": profile.avs_street,
                "AvsZip": profile.avs_zip,
                "CardCode": profile.card_code,
                "Created": profile.create_date.strftime('%Y-%m-%dT%H:%M:%S'),
                "Modified": profile.write_date.strftime('%Y-%m-%dT%H:%M:%S')
            }
            if 'xxx' not in profile.card_number:
                credit_card['CardNumber'] = profile.card_number
            if profile.ebizcharge_profile:
                credit_card['MethodID'] = profile.ebizcharge_profile
            return credit_card
        else:
            credit_card = {
                "AccountHolderName": profile['account_holder_name'],
                "MethodType": "CreditCard",
                "CardExpiration": "%s-%s" % (profile['card_exp_year'], profile['card_exp_month']),
                "AvsStreet": profile['avs_street'],
                "AvsZip": profile['avs_zip'],
                "CardCode": profile['card_code'],
                "Created": datetime.today().strftime('%Y-%m-%dT%H:%M:%S'),
                "Modified": datetime.today().strftime('%Y-%m-%dT%H:%M:%S')
            }
            if 'xxx' not in profile['card_number']:
                credit_card['CardNumber'] = profile['card_number']
            return credit_card

    def _payment_profile_bank(self, profile):
        if type(profile) != dict:
            bank_profile = {
                "AccountHolderName": profile.account_holder_name,
                "MethodType": "ACH",
                "AccountType": profile.account_type,
                "Routing": profile.routing,
                "Created": profile.create_date.strftime('%Y-%m-%dT%H:%M:%S'),
                "Modified": profile.write_date.strftime('%Y-%m-%dT%H:%M:%S')
            }
            if 'xxx' not in profile.account_number:
                bank_profile['Account'] = profile.account_number
            if profile.ebizcharge_profile:
                bank_profile['MethodID'] = int(profile.ebizcharge_profile)
            return bank_profile
        else:
            bank_profile = {
                "AccountHolderName": profile['account_holder_name'],
                "MethodType": "ACH",
                "AccountType": profile['account_type'],
                "Routing": profile['routing'],
                "Created": datetime.today().strftime('%Y-%m-%dT%H:%M:%S'),
                "Modified": datetime.today().strftime('%Y-%m-%dT%H:%M:%S')
            }
            if 'xxx' not in profile['account_number']:
                bank_profile['Account'] = profile['account_number']

            return bank_profile

    def _generate_payment_profile(self, profile, p_type='credit'):
        return {
            'securityToken': self._generate_security_json(),
            'customerInternalId': profile.partner_id.ebiz_internal_id if type(profile) != dict else profile.get(
                'ebiz_internal_id'),
            "paymentMethodProfile": self._payment_profile_credit_card(
                profile) if p_type == "credit" else self._payment_profile_bank(profile)
        }

    def add_customer_payment_profile(self, profile, p_type='credit'):
        sync_params = self._generate_payment_profile(profile, p_type)
        customer_profile = self.client.service.AddCustomerPaymentMethodProfile(**sync_params)
        return customer_profile

    def update_customer_payment_profile(self, profile, p_type='credit'):
        sync_params = self._generate_payment_profile(profile, p_type)
        del sync_params['customerInternalId']
        sync_params['customerToken'] = profile.partner_id.ebizcharge_customer_token
        return self.client.service.UpdateCustomerPaymentMethodProfile(**sync_params)

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
            "SalesDiscount": line.discount,
            "UnitOfMeasure": line.product_id.uom_id.name,
            "TotalLineAmount": line.price_total,
            "TotalLineTax": line.price_total - line.price_subtotal,
            "ItemLineNumber": item_no
        }
        return item

    def _invoice_lines_params(self, invoice_lines):
        lines_list = []
        for i, line in enumerate(invoice_lines):
            if line.price_subtotal != 0:
                lines_list.append(self._invoice_line_params(line, i + 1))
        array_of_items = self.client.get_type('ns0:ArrayOfItem')
        return array_of_items(lines_list)

    def _invoice_params(self, invoice):
        division = ''
        if invoice.company_id:
            division = str(invoice.company_id.id)
        software = 'ODOO CRM'
        web_sale = invoice.env['ir.module.module'].sudo().search(
            [('name', '=', 'website_sale'), ('state', 'in', ['installed', 'to upgrade', 'to remove'])])
        if web_sale and invoice.website_id:
            software = 'ODOO WEB'
        invoice_obj = {
            "CustomerId": invoice.partner_id.id,
            "InvoiceNumber": invoice.name,
            "InvoiceDate": str(invoice.invoice_date) if invoice.invoice_date else '',
            "InvoiceAmount": invoice.amount_total_signed,
            "InvoiceDueDate": str(invoice.invoice_date_due) if invoice.invoice_date_due else '',
            "AmountDue": invoice.amount_residual_signed,
            "Software": software,
            "DivisionId": division,
            "NotifyCustomer": False,
            "TotalTaxAmount": invoice.amount_tax_signed,
            "InvoiceUniqueId": invoice.id,
            "InvoiceMemo": "",
            "InvoiceSalesRepId": invoice.user_id.id,
            "PoNum": invoice.ref or "",
            "InvoiceIsToBeEmailed": 0,
            "InvoiceIsToBePrinted": 0,
            "Items": self._invoice_lines_params(invoice.invoice_line_ids),
            'ShippingAddress': self._get_customer_address(
                invoice.partner_shipping_id) if invoice.partner_shipping_id else '',
        }
        if invoice.move_type == 'out_refund':
            if invoice.amount_residual_signed == 0:
                invoice_obj['InvoiceAmount'] = -invoice.amount_total_signed
        return invoice_obj

    def _prepare_partner_address_info(self, partner):
        values = {}
        if partner.street:
            values['Address1'] = partner.street
        if partner.street2:
            values['Address2'] = partner.street2
        if partner.city:
            values['City'] = partner.city
        if partner.state_id:
            values['State'] = partner.state_id.code
        if partner.zip:
            values['ZipCode'] = partner.zip
        return values

    def _get_customer_address(self, partner):
        name_list = partner.name.split(' ') if partner.name else False
        first_name = name_list[0] if name_list else ''
        if name_list and len(name_list) >= 2:
            last_name = " ".join(name_list[1:])
        else:
            last_name = ""
        address = {
            "FirstName": first_name,
            "LastName": last_name,
            "CompanyName": partner.name if partner.company_type == "company" else partner.parent_id.name or "",
            "Country": partner.country_id.code or "US"
        }
        values = self._prepare_partner_address_info(partner)
        address.update(values)
        return address

    def _invoice_sync_object(self, invoice):
        sync_object = {
            'securityToken': self._generate_security_json(),
            'invoice': self._invoice_params(invoice)
        }
        return sync_object

    def sync_invoice(self, order):
        inv_params = self._invoice_sync_object(order)
        res = self.client.service.AddInvoice(**inv_params)
        return res

    def _update_invoice_params(self, invoice):
        invoice_params = self._invoice_sync_object(invoice)
        invoice_params.update({
            'customerId': invoice.partner_id.id,
            'invoiceNumber': invoice.name,
            'invoiceInternalId': invoice.ebiz_internal_id
        })
        return invoice_params

    def update_invoice(self, invoice):
        invoice_params = self._update_invoice_params(invoice)
        res = self.client.service.UpdateInvoice(**invoice_params)
        return res

    def _get_transaction_details(self, sale, command=None):
        order_id = ''
        invoice_id = ''
        
        po = ''
        trans_amount = 0
        if sale._name == "sale.order":
            order_id = sale.name
            invoice_id = sale.invoice_ids[0].name if sale.invoice_ids else sale.name
            po = sale.client_order_ref or sale.name
        if sale._name == "account.move":
            order_id = sale.invoice_origin or sale.name
            invoice_id = sale.name
            po = sale.ref or sale.name
            trans_amount = sale.amount_residual

        trans_ids = sale.transaction_ids.filtered(lambda x: x.state == 'draft')
        if trans_ids:
            trans_amount = trans_ids[0].amount
        tax_amount = 0
        if sale.amount_tax > 0:
            #tax_perc = (sale.amount_tax / sale.amount_untaxed) * 100
            #tax_amount = (trans_amount / 100) * tax_perc
            if trans_amount == sale.amount_total:
                tax_amount = sale.amount_tax

        subtotal_auth = 0
        if command==None and command not in ('capture','Capture'):
            subtotal_auth = round(trans_amount - tax_amount, 2)        
        return {
            'OrderID': order_id,
            'Invoice': invoice_id or "",
            'PONum': po,
            'Description': 'Partial Payment' if sale.amount_total!=trans_amount else ' ',
            'Amount': trans_amount,
            'Tax': round(tax_amount, 2),
            'Shipping': 0,
            'Discount': 0,
            'Subtotal': subtotal_auth,
            'AllowPartialAuth': False,
            'Tip': 0,
            'NonTax': True,
            'Duty': 0,
        }


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

    def _transaction_lines(self, lines, command=None, captured_amount=None):
        item_list = []
        trans_ids = lines.transaction_ids.filtered(lambda x: x.state not in ('cancel','error'))
        trans_amount = 0
        if trans_ids:
            trans_amount = trans_ids[0].amount 

        if captured_amount!=None and command=='Capture':
            trans_amount = captured_amount

        if command=='Capture' and trans_amount==lines.amount_total:
            order_lines = lines.order_line if lines._name=='sale.order' else lines.invoice_line_ids            
            for line in order_lines:
                item_list.append(self._transaction_line(line))
        elif trans_amount==lines.amount_total:  
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
        return {'LineItem': item_list}


    def _get_credit_card_transaction(self, profile, card=None):
        return {
            'InternalCardAuth': False,
            'CardPresent': False,
            'CardNumber': card if card else profile.card_number,
            'CardExpiration': "%s%s" % (profile.card_exp_month, profile.card_exp_year[2:]),
            'CardCode': profile.card_code,
            'AvsStreet': profile.avs_street,
            'AvsZip': profile.avs_zip
        }

    def get_transaction_object(self, order, credit_card, profile, command):
        address_obj = self._get_customer_address_for_transaction(order.partner_id)
        software = 'ODOO CRM'
        web_sale = order.env['ir.module.module'].sudo().search(
            [('name', '=', 'website_sale'), ('state', 'in', ['installed', 'to upgrade', 'to remove'])])
        if web_sale and order.website_id:
            software = 'ODOO WEB'
        obj = {
            "IgnoreDuplicate": False,
            "IsRecurring": False,
            "Details": self._get_transaction_details(order, command=command),
            "Software": software,
            "ClientIP": '',
            "Command": command,
            "CustReceipt": False,
            "LineItems": self._transaction_lines(order),
            "CustomerID": order.partner_id.id,
            "CreditCardData": credit_card,
            "AccountHolder": profile['account_holder_name'],
            'BillingAddress': address_obj,
            'ShippingAddress': address_obj
        }
        return obj

    def _get_customer_address_for_transaction(self, partner):
        name_array = partner.name.split(' ')
        first_name = name_array[0]
        if len(name_array) >= 2:
            last_name = " ".join(name_array[1:])
        else:
            last_name = ""
        address = {
            "FirstName": first_name,
            "LastName": last_name,
            "Company": partner.name if partner.company_type == "company" else partner.parent_id.name or "",
            "Street": partner.street or "",
            "Street2": partner.street2 or "",
            "City": partner.city or "",
            "State": partner.state_id.code or "",
            "Zip": partner.zip or "",
            "Country": partner.country_id.code or "US",
        }
        return address

    def run_transaction(self, order, profile, command='sale', token_ebiz=None):
        try:
            credit_card = []
            if token_ebiz==None:
                credit_card = self._get_credit_card_transaction(profile)
                credit_card['CardNumber'] = card
            elif token_ebiz:
                exp_date = token_ebiz["expiry"].split('/')
                card_exp_year =  str(2000 + int(exp_date[1]))
                card_exp_month =  str(int(exp_date[0]))
                credit_card = {
                    'InternalCardAuth': False,
                    'CardPresent': False,
                    'CardNumber': token_ebiz['cardNumber'],
                    'CardExpiration': "%s%s" % (card_exp_month, card_exp_year[2:]),
                    'CardCode': token_ebiz['cardCode'],
                    'AvsStreet': token_ebiz['street'],
                    'AvsZip': token_ebiz['zip']
                }
            transaction_params = self.get_transaction_object(order, credit_card, profile, command)
            params = {
                'securityToken': self._generate_security_json(),
                'tran': transaction_params
            }
            return self.client.service.runTransaction(**params)
        except Exception as e:
            raise ValidationError(e)

    def get_customer_transaction_object(self, order, profile, command):
        software = 'ODOO CRM'
        web_sale = order.env['ir.module.module'].sudo().search(
            [('name', '=', 'website_sale'), ('state', 'in', ['installed', 'to upgrade', 'to remove'])])
        if web_sale and order.website_id:
            software = 'ODOO WEB'
        if order.transaction_ids and order.transaction_ids[0].security_code:
            trans_object = {
                "isRecurring": False,
                "IgnoreDuplicate": False,
                "Details": self._get_transaction_details(order, command=command),
                "Software": software,
                "MerchReceipt": True,
                "CustReceipt": False,
                "CustReceiptName": '',
                "CustReceiptEmail": '',
                "ClientIP": '',
                "CardCode": order.transaction_ids[0].security_code,
                "Command": command if profile.token_type == "credit" else "Check",
            }
            for i in order.transaction_ids:
                i.write({
                    'security_code': False
                })
        else:
            trans_object = {
                "isRecurring": False,
                "IgnoreDuplicate": False,
                "Details": self._get_transaction_details(order, command=command),
                "Software": software,
                "MerchReceipt": True,
                "CustReceipt": False,
                "CustReceiptName": '',
                "CustReceiptEmail": '',
                "ClientIP": '',
                "Command": command if profile.token_type == "credit" else "Check",
            }
            if profile.card_code:
                trans_object['CardCode'] = profile.card_code
            profile.card_code = False

        if order._name == 'account.move':
            trans_object['LineItems'] = self._transaction_lines(order)
            trans_object['CustReceipt'] = order.transaction_ids[0].payment_id.ebiz_send_receipt
            trans_object['CustReceiptEmail'] = order.transaction_ids[0].payment_id.ebiz_receipt_emails
            if order.move_type == 'out_refund':
                if profile.token_type == 'credit':
                    trans_object['Command'] = 'Credit'
                else:
                    trans_object['Command'] = 'CheckCredit'
        else:
            trans_object['LineItems'] = self._transaction_lines(order)
            if order._name == 'sale.order':
                trans_object['CustReceipt'] = order.transaction_ids[0].payment_id.ebiz_send_receipt
                trans_object['CustReceiptEmail'] = order.transaction_ids[0].payment_id.ebiz_receipt_emails

        return trans_object

    def run_customer_transaction(self, order, profile, command='sale', current_user=None):
        try:
            customer_token = ''
            if profile.partner_id.ebiz_profile_id and profile.partner_id.ebizcharge_customer_token:
                customer_token = profile.partner_id.ebizcharge_customer_token  
            elif order.partner_id.ebiz_profile_id and order.partner_id.ebizcharge_customer_token:
                customer_token = order.partner_id.ebizcharge_customer_token   
            elif current_user:
                customer_token = current_user.ebizcharge_customer_token            
            params = {
                "securityToken": self._generate_security_json(),
                "custNum": customer_token,
                "paymentMethodID": profile.ebizcharge_profile,
                "tran": self.get_customer_transaction_object(order, profile, command),
            }
            resp = self.client.service.runCustomerTransaction(**params)
            return resp
        except Exception as e:
            raise ValidationError(e)

    def run_full_amount_transaction(self, order, profile, command, card, token_ebiz=None):
        try:
            credit_card = []
            if token_ebiz==None:
                credit_card = self._get_credit_card_transaction(profile)
                credit_card['CardNumber'] = card
            elif token_ebiz:
                exp_date = token_ebiz["expiry"].split('/')
                card_exp_year =  str(2000 + int(exp_date[1]))
                card_exp_month =  str(int(exp_date[0]))
                credit_card = {
                    'InternalCardAuth': False,
                    'CardPresent': False,
                    'CardNumber': token_ebiz['cardNumber'],
                    'CardExpiration': "%s%s" % (card_exp_month, card_exp_year[2:]),
                    'CardCode': token_ebiz['cardCode'],
                    'AvsStreet': token_ebiz['street'],
                    'AvsZip': token_ebiz['zip']
                }
            transaction_params = self.get_transaction_object_run_transaction(order, credit_card, profile, command)
            params = {
                "securityToken": self._generate_security_json(),
                "tran": transaction_params,
            }
            return self.client.service.runTransaction(**params)
        except Exception as e:
            raise ValidationError(e)

    def get_transaction_object_run_transaction(self, order, credit_card, profile, command):
        order.partner_id.address_get()
        address_obj = self._get_customer_address_for_transaction(order.partner_id)
        software = 'ODOO CRM'
        web_sale = order.env['ir.module.module'].sudo().search(
            [('name', '=', 'website_sale'), ('state', 'in', ['installed', 'to upgrade', 'to remove'])])
        if web_sale and order.website_id:
            software = 'ODOO WEB'
        obj = {
            "IgnoreDuplicate": False,
            "IsRecurring": False,
            "Details": self._get_transaction_details(order, command=command),
            "Software": software,
            "ClientIP": '',
            "Command": command,
            "CustReceipt": False,
            "LineItems": self._transaction_lines(order),
            "CustomerID": order.partner_id.id,
            "CreditCardData": credit_card,
            "AccountHolder": profile['account_holder_name'],
            'BillingAddress': address_obj,
            'ShippingAddress': address_obj
        }
        return obj

    def execute_transaction(self, ref_num, kwargs, invoice=False, sale=False, ebiz_transaction_amt=False,
                            transaction_histry_amt=False, transaction_histry_tax=False, emv_trans=None):
        try:
            reference_number = 'Product'
            transaction_params = {}
            if invoice:
                captured_amount = invoice.amount_residual
                reference_number = invoice.name
                software = 'ODOO CRM'
                web_sale = invoice.env['ir.module.module'].sudo().search(
                    [('name', '=', 'website_sale'), ('state', 'in', ['installed', 'to upgrade', 'to remove'])])
                if web_sale and invoice.website_id:
                    software = 'ODOO WEB'
                transaction_params = {
                    'Command': kwargs['command'],
                    'RefNum': ref_num,
                    'IsRecurring': False,
                    'IgnoreDuplicate': False,
                    'CustReceipt': True,
                    'Software': software,
                    "LineItems": self._transaction_lines(invoice, command=kwargs['command'], captured_amount=captured_amount),
                    'Details': {
                        'Invoice': invoice.name,
                        'PONum': invoice.ref if invoice.ref else invoice.name,
                        'Description': "Customer Credit",
                        'Amount': transaction_histry_amt if transaction_histry_amt!=False else captured_amount,
                        'Tax': transaction_histry_tax if transaction_histry_tax!=False else invoice.amount_tax,
                        'Shipping': 0,
                        'Discount': 0,
                        'Subtotal': transaction_histry_amt if transaction_histry_amt!=False  else invoice.amount_residual,
                        'AllowPartialAuth': False,
                        'Tip': 0,
                        'NonTax': True,
                        'Duty': 0
                    },
                }
            elif sale:
                captured_amount = sale.amount_total
                software = 'ODOOCRM'
                transaction_params = {
                    'Command': kwargs['command'],
                    'RefNum': ref_num,
                    'IsRecurring': False,
                    'IgnoreDuplicate': False,
                    'CustReceipt': True,
                    'Software': software,
                    "LineItems": self._transaction_lines(sale, command=kwargs['command']),
                    'Details': {
                        'Invoice': sale.name,
                        'PONum': sale.client_order_ref if sale.client_order_ref else sale.name,
                        'Description': "Customer Credit",
                        'Amount': transaction_histry_amt if transaction_histry_amt!=False else captured_amount,
                        'Tax':  transaction_histry_tax if transaction_histry_tax!=False else sale.amount_tax,
                        'Shipping': 0,
                        'Discount': 0,
                        'Subtotal': transaction_histry_amt if transaction_histry_amt!=False else sale.amount_total,
                        'AllowPartialAuth': False,
                        'Tip': 0,
                        'NonTax': True,
                        'Duty': 0
                    },
                }

            elif transaction_histry_amt and invoice==False and sale==False:
                transaction_params = {
                    'Command': kwargs['command'],
                    'RefNum': ref_num,
                    'IsRecurring': False,
                    'IgnoreDuplicate': False,
                    'CustReceipt': True,
                    'Software': 'ODOO CRM',
                    'Details': {
                        'Description': "Customer Credit",
                        'Amount': transaction_histry_amt,
                        'Tax': 0,
                        'Shipping': 0,
                        'Discount': 0,
                        'AllowPartialAuth': False,
                        'Subtotal': transaction_histry_amt,
                        'Tip': 0,
                        'NonTax': True,
                        'Duty': 0
                    },
                }
            else:
                #noon
                if emv_trans!=None:
                    transaction_params = {
                        'Command': kwargs['command'],
                        'Details': self._get_emv_transaction_details(emv_trans),
                        'RefNum': ref_num,
                        'IsRecurring': False,
                        'IgnoreDuplicate': False,
                        'CustReceipt': True,
                        "CustomerID": emv_trans.partner_id.id,
                    }
                else:
                     transaction_params = {
                        'Command': kwargs['command'],
                        'RefNum': ref_num,
                        'IsRecurring': False,
                        'IgnoreDuplicate': False,
                        'CustReceipt': True,
                    }  
 
            if transaction_histry_amt!=False:
                tax_amt = 0
                if transaction_histry_tax!=False:
                    tax_amt = transaction_histry_tax    
                #transaction_params['LineItems'] = {'LineItem': [{
                  #  'SKU': reference_number,
                 #   'ProductName': reference_number,
                 #   'Description': reference_number,
                 #   'UnitPrice': float(transaction_histry_amt)-float(tax_amt),
                 #   'Taxable': 1 if transaction_histry_tax!=False else 0,
                 #   'TaxAmount': transaction_histry_tax if transaction_histry_tax!=False else 0,
                 #   'Qty': 1,
                #    'DiscountRate': 0,
                #}]}
            params = {
                'securityToken': self._generate_security_json(),
                'tran': transaction_params
            }
            return self.client.service.runTransaction(**params)
        except Exception as e:
            raise ValidationError(e)


    def _get_emv_transaction_details(self, trans_id):
        return {
            'OrderID': "",
            'Invoice': trans_id['reference'],
            'PONum': "",
            'Description': 'Transaction Captured from ODOO',
            'Amount': trans_id['amount'],
            'Tax': 0,
            'Shipping': 0,
            'Discount': 0,
            'Subtotal': trans_id['amount'],
            'AllowPartialAuth': False,
            'Tip': 0,
            'NonTax': True,
            'Duty': 0
        }


    def void_transaction(self, trans, invoice=None):
        ref_num = trans.provider_reference
        kwargs = {'command': 'Void'}
        return self.execute_transaction(ref_num, kwargs)

    def capture_transaction(self, trans, invoice=None, sale=None, ebiz_transaction_amt=None , emv_trans=None):
        ref_num = trans.provider_reference
        kwargs = {'command': 'Capture'}
        return self.execute_transaction(ref_num, kwargs, invoice, sale, ebiz_transaction_amt, emv_trans=emv_trans)

    def return_transaction(self, **kwargs):
        ref_num = kwargs['ref_num']
        kwargs['command'] = "credit"
        return self.execute_transaction(ref_num, kwargs)

    def run_credit_transaction(self, invoice, profile, ref, current_user=None):
        try:
            command = "Credit" if profile.token_type == 'credit' else "CheckCredit"
            trans_obj = self.get_customer_transaction_object(invoice, profile, command)
            customer_token = ''
            if profile.partner_id.ebiz_profile_id and profile.partner_id.ebizcharge_customer_token:
                customer_token = profile.partner_id.ebizcharge_customer_token
            elif invoice.partner_id.ebiz_profile_id and invoice.partner_id.ebizcharge_customer_token:
                customer_token = invoice.partner_id.ebizcharge_customer_token
            elif current_user:
                customer_token = current_user.ebizcharge_customer_token
            params = {
                "securityToken": self._generate_security_json(),
                "custNum": customer_token ,
                "paymentMethodID": profile.ebizcharge_profile,
                "tran": trans_obj
            }
            return self.client.service.runCustomerTransaction(**params)
        except Exception as e:
            raise ValidationError(e)


    def run_transaction_without_invoice(self, trans_id):
        try:
            payment_token = trans_id.token_id
            command = 'Sale' if payment_token.token_type == 'credit' else 'Check'
            params = {
                "securityToken": self._generate_security_json(),
                "custNum": trans_id.partner_id.ebizcharge_customer_token,
                "paymentMethodID": payment_token.ebizcharge_profile,
                "tran": {
                    "isRecurring": False,
                    "IgnoreDuplicate": False,
                    "Software": 'ODOO CRM',
                    "MerchReceipt": True,
                    "CustReceiptName": '',
                    "CustReceiptEmail": '',
                    "CustReceipt": False,
                    "ClientIP": '',
                    "CardCode": payment_token.card_code,
                    "Command": command,
                    "Details": {
                        'OrderID': "",
                        'Invoice': trans_id.reference,
                        'PONum': "",
                        'Description': "Customer Credit",
                        'Amount': trans_id.amount,
                        'Tax': 0,
                        'Shipping': 0,
                        'Discount': 0,
                        'Subtotal': trans_id.amount,
                        'AllowPartialAuth': False,
                        'Tip': 0,
                        'NonTax': True,
                        'Duty': 0
                    },
                },
            }
            return self.client.service.runCustomerTransaction(**params)
        except Exception as e:
            raise ValidationError(e)

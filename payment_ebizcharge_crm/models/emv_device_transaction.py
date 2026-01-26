# -*- coding: utf-8 -*-
import hashlib

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime, timedelta
import logging
from .ebiz_charge import message_wizard
import json
import requests
import base64


class InvEmvDeviceTransaction(models.Model):
    _name = 'emv.device.transaction'
    _description = "EMV Device Transaction"
    _rec_name = 'invoice'

    def _default_template(self):
        payment_acq = self.env['payment.provider'].search(
            [('company_id', '=', self.env.company.id), ('code', '=', 'ebizcharge')])
        if payment_acq:
            return payment_acq.journal_id.id
        else:
            return None

    journal_id = fields.Many2one('account.journal', string='Journal')
    payment_token_id = fields.Many2one('payment.token', string="Payment Token ID")
    emv_device_ids = fields.One2many('line.emv.device.transaction', 'emv_id')

    partner_id = fields.Many2one('res.partner', string="Partner Id", )
    devicekey = fields.Char(string="EMV Device Key")
    invoice_id = fields.Many2one("account.move", string='Invoice')
    pin = fields.Char(string="pin")

    email_sent = fields.Boolean(string='Email Sent')

    seed = fields.Char(string="Seed")
    command = fields.Char(string="Command")
    amount = fields.Float(string="Amount")
    payment_date = fields.Date(string="Payment Date")
    invoice = fields.Char(string="Invoice")
    block_offline = fields.Char(string="Block Offline")
    ignore_duplicate = fields.Char(string="Ignore Duplicate")
    save_card = fields.Char(string="Save Card")
    manual_key = fields.Char(string="Manual Key")
    prompt_tip = fields.Char(string="Prompt tip")
    ponum = fields.Char(string="Ponum")
    orderid = fields.Char(string="Order Id")
    sale_id = fields.Many2one('sale.order', string='Order')
    description = fields.Char(string="Description")
    billing_address = fields.Text(string="Billing Address")
    shipping_address = fields.Text(string="Shipping Address")
    receipt_ref_num = fields.Char(string='Receipt RefNum')

    # address fields
    street = fields.Char()
    street2 = fields.Char()
    zip = fields.Char(change_default=True)
    city = fields.Char()
    company_name = fields.Char('Company Name')

    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('sent', 'Sent'),
            ('approve', 'Approved'),
            ('cancel', 'Cancelled'),
        ],
        string='Status',
        required=True,
        readonly=True,
        copy=False,
        tracking=True,
        default='draft',
    )

    def action_draft(self):
        self.write({'state': 'sent'})

    def action_post(self):
        url = "https://secure.ebizcharge.com/api/v2/paymentengine/payrequests"
        headers = {
            "Content-Type": "application/json"
        }
        for transaction in self:
            line_list = []
            for line in transaction.emv_device_ids:
                line_list.append({
                    "name": line.name,
                    "description": line.description,
                    "cost": line.price_unit,
                    "list_price": line.price_unit,
                    "qty": line.qty,
                    "sku": line.sku,
                    "commoditycode": line.commoditycode,
                    "discountamount": line.discountamount,
                    "discountrate": line.discountrate,
                    "taxable": line.taxable,
                    "taxamount": line.taxamount,
                    "taxclass": line.taxclass,
                    "category": line.category,
                    "manufacturer": line.manufacturer,
                })
            amount_tax = 0
            custid = 0
            email = ""
            if transaction.invoice_id:
                amount_tax = transaction.invoice_id.amount_tax
                custid = transaction.invoice_id.partner_id.id
                email =  transaction.invoice_id.partner_id.email
            elif transaction.sale_id:
                amount_tax = transaction.sale_id.amount_tax
                custid = transaction.sale_id.partner_id.id
                email =  transaction.sale_id.partner_id.email
            data = {
                "devicekey": transaction.devicekey,
                "command": transaction.command,
                "amount": transaction.amount,
                "software": "ODOO CRM",
                "customerid": custid,
                "email" : email if transaction.email_sent else '',
                "amount_detail": {"subtotal": (transaction.amount-amount_tax), "tax": amount_tax},
                "timeout": "150",
                "block_offline": transaction.block_offline,
                "ignore_duplicate": transaction.block_offline,
                "save_card": transaction.save_card,
                "manual_key": transaction.manual_key,
                "prompt_tip": transaction.prompt_tip,
                "invoice": transaction.invoice,
                "ponum": transaction.ponum,
                "orderid": transaction.orderid,
                "description": transaction.description,
                "billing_address": {
                    "company": transaction.partner_id.company_name,
                    "street": str(transaction.partner_id.street) +' '+ str(transaction.partner_id.street2),
                    "postalcode": transaction.partner_id.zip, },
                "shipping_address": {
                    "company": transaction.partner_id.company_name,
                    "street": str(transaction.partner_id.street) +' '+ str(transaction.partner_id.street2),
                    "postalcode": transaction.partner_id.zip, },
                "lineitems": line_list, }
            api_key = transaction.partner_id.ebiz_profile_id.source_key
            seed = ''
            pin = transaction.partner_id.ebiz_profile_id.pin
            auth_info = self.generate_auth_info(api_key, seed, pin)
            headers.update({
                "Authorization": auth_info,
            })

            response = requests.post(url, headers=headers, json=data)
            data_list = response.content
            final_list = json.loads(data_list)

            if 'key' in final_list and final_list['key']:
                print(final_list['key'])
                url_get_transaction_info = url + '/' + str(final_list['key'])
                get_info = requests.get(url_get_transaction_info, headers=headers)
                self.receipt_ref_num = final_list['key']
            self.state = 'sent'
            
            
            
            
    def action_create_payment(self, pay, trans_ref):
        companyid = pay.sale_id.company_id.id if pay.sale_id else pay.invoice_id.company_id.id
        prvoider = self.env['payment.provider'].search(
            [('company_id', '=', companyid), ('code', '=', 'ebizcharge')], limit=1)
        ebiz_method = self.env['account.payment.method.line'].search(
            [('journal_id', '=', prvoider.journal_id.id),
             ('payment_method_id.code', '=', 'ebizcharge')], limit=1)
        payment_record = {
            'journal_id': pay.journal_id.id,
            'payment_method_id': ebiz_method.payment_method_id.id,
            'payment_method_line_id':ebiz_method.id,
            'payment_token_id': pay.payment_token_id.id,
            'amount': abs(pay.amount),
            'partner_id': pay.partner_id.id,
            'partner_type': 'customer',
            'payment_type': 'inbound',
        }
        if pay.invoice_id and pay.invoice_id.move_type == 'out_refund':
            payment_record['payment_type'] = 'outbound'
        elif pay.invoice_id and pay.invoice_id.move_type == 'out_invoice':
            payment_record['payment_type'] = 'inbound'
        payment = self.env['account.payment'].sudo().create(payment_record)
        if pay.sale_id:
            transaction_vals = {
                'provider_id': prvoider.id,
                'payment_method_id': ebiz_method.payment_method_id.id,
                'amount': pay.amount,
                'currency_id': pay.sale_id.currency_id.id,
                'partner_id': pay.partner_id.id,
                'token_id': False,
                # 'operation': 'offline',
                'emv_transaction': True,
                'provider_reference': trans_ref,
                'payment_id': payment.id,
                'sale_order_ids': [pay.sale_id.id],
            }
            trans_vals = self.env['payment.transaction'].create(transaction_vals)
            trans_vals._set_authorized()
            if pay.command!='AuthOnly':
                trans_vals._set_done()
                payment.action_post()
        else:
            payment.action_post()       
        domain = [
            ('parent_state', '=', 'posted'),
            ('account_type', 'in', ('asset_receivable', 'liability_payable')),
            ('reconciled', '=', False)]
        payment_lines = payment.line_ids.filtered_domain(domain)
        lines_domain = [('debit', '>', 0)]
        if pay.invoice_id and pay.invoice_id.move_type == 'out_refund':
            lines_domain = [('credit', '>', 0)]
        lines = pay.invoice_id.line_ids.filtered_domain(lines_domain)
        for account in payment_lines.account_id:
            (payment_lines + lines).filtered_domain(
                [('account_id', '=', account.id), ('reconciled', '=', False)]).reconcile()
        return payment

    def action_check(self, trans=None):

        url = "https://secure.ebizcharge.com/api/v2/paymentengine/payrequests"
        headers = {
            "Content-Type": "application/json"
        }
        transaction_sent = self.env['emv.device.transaction'].search([('state','=','sent')])
        if trans!=None:
            transaction_sent = self.env['emv.device.transaction'].search([('state', '=', 'sent'),('id','=',trans)])
        for trans_snt in transaction_sent:
            api_key = trans_snt.partner_id.ebiz_profile_id.source_key
            seed = ''
            pin = trans_snt.partner_id.ebiz_profile_id.pin
            auth_info = trans_snt.generate_auth_info(api_key, seed, pin)
            headers.update({
                "Authorization": auth_info,
            })
            url_get_transaction_info = url + '/' + str(trans_snt.receipt_ref_num)
            get_info = requests.get(url_get_transaction_info, headers=headers)
            data_list = get_info.content
            final_list = json.loads(data_list)
            message = ""
            if final_list.get('transaction') and final_list.get('transaction').get('result_code'):
                if final_list.get('transaction').get('result_code') == 'A':
                    trans_ref = final_list['transaction']['refnum']
                    trans_snt.action_create_payment(trans_snt, trans_ref)
                    trans_snt.state = 'approve'
                    if trans_snt.invoice_id:
                        trans_snt.invoice_id.sync_to_ebiz()
                    message = 'EMV Device ' + str(final_list['status'])
                else:
                    if final_list.get('transaction').get('result'):
                        message = 'EMV Device Transaction ' + str(final_list.get('transaction').get('result'))
                        trans_snt.state = 'cancel'
            elif 'status' in final_list and final_list['status']=='sent to device':
                message= 'Transaction '+str(final_list['status'])
            else:
                if 'status' in final_list:
                    message= 'EMV Device '+str(final_list['status'])
                    trans_snt.state = 'cancel'
            if trans_snt.invoice_id:
                trans_snt.invoice_id.log_status_emv = message
                trans_snt.invoice_id.emv_transaction_id = trans_snt.id
            if trans_snt.sale_id:
                trans_snt.sale_id.log_status_emv = message
                trans_snt.sale_id.emv_transaction_id = trans_snt.id
            # raise UserError(str(final_list))


    def action_cancel(self):
        self.write({'state': 'cancel'})

    def generate_hash(self, device_key, seed, pin):
        hash_input = str(device_key) + str(seed) + str(pin)
        hash_value = hashlib.sha256(hash_input.encode()).hexdigest()
        return hash_value

    def generate_auth_info(self, device_key, seed, pin):
        hash_value = self.generate_hash(device_key, seed, pin)
        auth_info = str(device_key) + ':s2/' + str(seed) + '/' + str(hash_value)
        encoded_auth_info = base64.b64encode(auth_info.encode()).decode()
        # print(str('/') + ' ' + encoded_auth_info)
        return 'Basic ' + encoded_auth_info


class InvEmvDeviceTransaction(models.Model):
    _name = 'line.emv.device.transaction'
    _description = "EMV Device Transaction"

    name = fields.Char(string="Name")
    emv_id = fields.Many2one('emv.device.transaction')
    description = fields.Char(string="Description")
    qty = fields.Integer(string="Qty")
    cost = fields.Float(string="cost")
    list_price = fields.Float(string="List Price")
    sku = fields.Char(string="sku")
    taxclass = fields.Char(string="Tax class")
    category = fields.Char(string="Category")
    enable_partialauth = fields.Char(string="enable_partialauth")
    manufacturer = fields.Char(string="Manufacturer")
    commoditycode = fields.Float(string="Commodity Code")
    discountamount = fields.Float(string="Discount amount")
    discountrate = fields.Float(string="Discount Rate")
    taxable = fields.Float(string="Taxable")
    taxamount = fields.Float(string="Tax amount")
    price_unit = fields.Float(string="Price Unit")
    price_subtotal = fields.Float(string="Price Subtotal")


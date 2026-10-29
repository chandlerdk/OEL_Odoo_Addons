# -*- encoding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (http://www.bistasolutions.com)
#
##############################################################################

import time

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.fields import Command


class PurchaseAdvancePaymentInv(models.TransientModel):
    _name = "purchase.advance.payment.inv"
    _description = "Bista Purchase Advance Payment Invoice"

    @api.model
    def _count(self):
        return len(self.env.context.get('active_ids', []))

    @api.model
    def _default_product_id(self):
        product_id = self.env['ir.config_parameter'].sudo().get_int(
            'purchase.default_pre_deposit_product_id')
        return self.env['product.product'].browse(product_id).exists()

    @api.model
    def _default_deposit_account_id(self):
        return self._default_product_id().property_account_expense_id

    @api.model
    def _default_deposit_taxes_id(self):
        return self._default_product_id().supplier_taxes_id

    def _get_active_purchase_order(self):
        if self.env.context.get('active_model') == 'purchase.order' and self.env.context.get('active_id'):
            return self.env['purchase.order'].browse(self.env.context['active_id'])
        return self.env['purchase.order']

    @api.model
    def _default_has_pre_payment(self):
        return bool(self._get_active_purchase_order().order_line.filtered('is_prepayment'))

    @api.model
    def _default_currency_id(self):
        return self._get_active_purchase_order().currency_id

    advance_payment_method = fields.Selection([
        ('delivered', 'Regular Bill'),
        ('percentage', 'Pre payment (percentage)'),
        ('fixed', 'Pre payment (fixed amount)')
    ], string='Create Bill', default='delivered', required=True,
        help="A standard bill is issued with all the order lines ready "
             "for invoicing, according to their invoicing policy "
             "(based on ordered or delivered quantity).")
    deduct_pre_payments = fields.Boolean('Deduct pre payments', default=True)
    has_pre_payments = fields.Boolean(
        'Has pre payments', default=_default_has_pre_payment, readonly=True)
    product_id = fields.Many2one('product.product', string='Pre Payment Product',
                                 domain=[('type', '=', 'service')],
                                 default=_default_product_id)
    count = fields.Integer(default=_count, string='Order Count')
    amount = fields.Float('Pre Payment Amount', digits='Account',
                          help="The percentage of amount to be invoiced in advance, "
                               "taxes excluded.")
    currency_id = fields.Many2one(
        'res.currency', string='Currency', default=_default_currency_id)
    fixed_amount = fields.Monetary(
        'Pre Payment Amount(Fixed)',
        help="The fixed amount to be invoiced in advance, taxes excluded.")
    deposit_account_id = fields.Many2one("account.account", string="Expense Account",
                                         help="Account used for deposits",
                                         default=_default_deposit_account_id)
    deposit_taxes_id = fields.Many2many(
        "account.tax", string="Vendor Taxes",
        help="Taxes used for deposits",
        default=_default_deposit_taxes_id)

    @api.onchange('advance_payment_method')
    def onchange_advance_payment_method(self):
        if self.advance_payment_method == 'percentage':
            self.amount = self.default_get(['amount']).get('amount')

    def _prepare_invoice_values(self, order, name, amount, po_line):
        return {
            'ref': '',
            'move_type': 'in_invoice',
            'invoice_origin': order.name,
            'invoice_user_id': order.user_id.id,
            'partner_id': order.partner_id.id,
            'fiscal_position_id': order.fiscal_position_id.id or order.partner_id.property_account_position_id.id,
            'partner_shipping_id': order.dest_address_id.id
                                   or order.picking_type_id.warehouse_id.partner_id.id
                                   or order.partner_id.id,
            'currency_id': order.currency_id.id,
            'payment_reference': order.name,
            'invoice_payment_term_id': order.payment_term_id.id,
            'partner_bank_id': order.partner_id.commercial_partner_id.bank_ids[:1].id,
            'company_id': order.company_id.id,
            'invoice_line_ids': [Command.create({
                'name': name,
                'price_unit': amount,
                'quantity': 1.0,
                'product_id': self.product_id.id,
                'product_uom_id': po_line.uom_id.id,
                'tax_ids': [Command.set(po_line.tax_ids.ids)],
                'purchase_line_ids': [Command.set(po_line.ids)],
                'purchase_line_id': po_line.id,
            })],
        }

    def _get_advance_details(self, order):
        if self.advance_payment_method == 'percentage':
            amount = order.amount_untaxed * self.amount / 100
            name = _("Pre payment of %s%%", self.amount)
        else:
            amount = self.fixed_amount
            name = _('Pre Payment')
        return amount, name

    def _create_invoice(self, order, po_line, amount):
        if (self.advance_payment_method == 'percentage' and self.amount <= 0.00) or \
                (self.advance_payment_method == 'fixed' and self.fixed_amount <= 0.00):
            raise UserError(_('The value of the pre payment amount must be positive.'))

        amount, name = self._get_advance_details(order)
        invoice_vals = self._prepare_invoice_values(order, name, amount, po_line)
        invoice = self.env['account.move'].sudo().with_company(order.company_id).create(
            invoice_vals).with_user(self.env.uid)
        invoice.message_post_with_source(
            'mail.message_origin_link',
            render_values={'self': invoice, 'origin': order},
            subtype_xmlid='mail.mt_note',
        )
        return invoice

    def _prepare_po_line(self, order, tax_ids, amount):
        return {
            'name': _('Pre Payment: %s', time.strftime('%m %Y')),
            'price_unit': amount,
            'product_qty': 0.0,
            'order_id': order.id,
            'uom_id': self.product_id.uom_id.id,
            'product_id': self.product_id.id,
            'tax_ids': [Command.set(tax_ids)],
            'is_prepayment': True,
            'date_planned': fields.Datetime.now(),
        }

    def create_invoices(self):
        purchase_orders = self.env['purchase.order'].browse(self.env.context.get('active_ids', []))
        create_invoice_ids = []
        if self.advance_payment_method == 'delivered':
            invoice = purchase_orders.with_context(
                advance_payment_method=self.advance_payment_method,
            )._create_invoices(final=self.deduct_pre_payments)
            create_invoice_ids += invoice.ids
        else:
            if not self.product_id:
                vals = self._prepare_deposit_product()
                product_tmpl = self.env['product.template'].create(vals)
                self.product_id = product_tmpl.product_variant_ids
                self.env['ir.config_parameter'].sudo().set_int(
                    'purchase.default_pre_deposit_product_id', self.product_id.id)

            purchase_line_obj = self.env['purchase.order.line']
            for order in purchase_orders:
                amount, name = self._get_advance_details(order)

                if 'invoice_policy' in self.product_id._fields and self.product_id.invoice_policy != 'order':
                    raise UserError(_("The product used to invoice a pre payment "
                                      "should have an invoice policy set to "
                                      "'Ordered quantities'. Please update your "
                                      "deposit product to be able to create a "
                                      "deposit invoice."))
                if self.product_id.type != 'service':
                    raise UserError(_("The product used to invoice a pre payment"
                                      " should be of type 'Service'. Please "
                                      "use another product or update this product."))
                taxes = self.product_id.supplier_taxes_id.filtered(
                    lambda r: not order.company_id or r.company_id == order.company_id) or self.deposit_taxes_id
                if order.fiscal_position_id and taxes:
                    tax_ids = order.fiscal_position_id.map_tax(taxes).ids
                else:
                    tax_ids = taxes.ids
                po_line_values = self._prepare_po_line(order, tax_ids, amount)
                po_line = purchase_line_obj.create(po_line_values)
                invoice = self._create_invoice(order, po_line, amount)
                create_invoice_ids += invoice.ids
        if self.env.context.get('open_invoices'):
            return purchase_orders.action_view_invoice_advance_payment()
        return {'type': 'ir.actions.act_window_close', 'create_invoice_ids': create_invoice_ids}

    def _prepare_deposit_product(self):
        vals = {
            'name': 'Pre payment',
            'type': 'service',
            'purchase_method': 'purchase',
            'property_account_expense_id': self.deposit_account_id.id,
            'supplier_taxes_id': [Command.set(self.deposit_taxes_id.ids)],
            'company_id': False,
            'sale_ok': False,
            'purchase_ok': False,
        }
        if 'invoice_policy' in self.env['product.template']._fields:
            vals['invoice_policy'] = 'order'
        return vals

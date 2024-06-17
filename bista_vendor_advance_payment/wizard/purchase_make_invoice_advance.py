# -*- encoding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (http://www.bistasolutions.com)
#
##############################################################################

import time
from datetime import datetime
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PurchaseAdvancePaymentInv(models.TransientModel):
    _name = "purchase.advance.payment.inv"
    _description = "Bista Purchase Advance Payment Invoice"

    @api.model
    def _count(self):
        return len(self._context.get('active_ids', []))

    @api.model
    def _default_product_id(self):
        product_id = self.env['ir.config_parameter'].sudo().get_param(
            'purchase.default_pre_deposit_product_id')
        return self.env['product.product'].browse(int(product_id)).exists()

    @api.model
    def _default_deposit_account_id(self):
        return self._default_product_id().property_account_income_id

    @api.model
    def _default_deposit_taxes_id(self):
        return self._default_product_id().supplier_taxes_id

    @api.model
    def _default_has_pre_payment(self):
        if self._context.get('active_model') == 'purchase.order' and \
                self._context.get('active_id', False):
            purchase_order = self.env['purchase.order'].browse(
                self._context.get('active_id'))
            return purchase_order.order_line.filtered(
                lambda purchase_order_line: purchase_order_line.is_prepayment
            )
        return False

    @api.model
    def _default_currency_id(self):
        if self._context.get('active_model') == 'purchase.order' and \
                self._context.get('active_id', False):
            purchase_order = self.env['purchase.order'].browse(
                self._context.get('active_id'))
            return purchase_order.currency_id

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
    deposit_account_id = fields.Many2one("account.account", string="Income Account",
                                         domain=[('deprecated', '=', False)],
                                         help="Account used for deposits",
                                         default=_default_deposit_account_id)
    deposit_taxes_id = fields.Many2many(
        "account.tax", string="Vendor Taxes",
        help="Taxes used for deposits",
        default=_default_deposit_taxes_id)

    @api.onchange('advance_payment_method')
    def onchange_advance_payment_method(self):
        if self.advance_payment_method == 'percentage':
            amount = self.default_get(['amount']).get('amount')
            return {'value': {'amount': amount}}
        return {}

    def _prepare_invoice_values(self, order, name, amount, po_line):
        invoice_vals = {
            'ref': '',
            'move_type': 'in_invoice',
            'invoice_origin': order.name,
            'invoice_user_id': order.user_id.id,
            # 'narration': order.internal_communication,
            'partner_id': order.partner_id.id,
            'fiscal_position_id': order.fiscal_position_id.id or order.partner_id.property_account_position_id.id,
            'partner_shipping_id': order.dest_address_id.id or order.picking_type_id.warehouse_id.partner_id.id or order.partner_id.id,
            'currency_id': order.currency_id.id,
            'payment_reference': order.name,
            'invoice_payment_term_id': order.payment_term_id.id,
            'partner_bank_id': order.company_id.partner_id.bank_ids[:1].id,
            # 'document_ids': [(6, 0, order.document_ids.ids)],
            # 'project_id': order.project_id.id,
            # 'team_id': order.team_id.id,
            # 'campaign_id': order.campaign_id.id,
            # 'medium_id': order.medium_id.id,
            # 'source_id': order.source_id.id,
            'invoice_line_ids': [(0, 0, {
                'name': name,
                'price_unit': amount,
                'quantity': 1.0,
                'product_id': self.product_id.id,
                'product_uom_id': po_line.product_uom.id,
                'tax_ids': [(6, 0, po_line.taxes_id.ids)],
                'purchase_line_ids': [(6, 0, [po_line.id])],
                'purchase_line_id': po_line.id,
                # Commented below fields as analytic_account_id and analytic_tag_ids is no longer available in v16
                # 'analytic_tag_ids': [(6, 0, analytic_tag_ids.ids)],
                # 'analytic_account_id': po_line.account_analytic_id.id or False,
            })],
        }
        return invoice_vals

    def _get_advance_details(self, order):
        if self.advance_payment_method == 'percentage':
            amount = order.amount_untaxed * self.amount / 100
            name = _("Pre payment of %s%%") % (self.amount)
        else:
            amount = self.fixed_amount
            name = _('Pre Payment')

        return amount, name

    def _create_invoice(self, order, po_line, amount):
        if (self.advance_payment_method == 'percentage' and self.amount <= 0.00) or \
                (self.advance_payment_method == 'fixed' and self.fixed_amount <= 0.00):
            raise UserError(
                _('The value of the pre payment amount must be positive.'))

        amount, name = self._get_advance_details(order)

        invoice_vals = self._prepare_invoice_values(
            order, name, amount, po_line)

        if order.fiscal_position_id:
            invoice_vals['fiscal_position_id'] = order.fiscal_position_id.id
        invoice = self.env['account.move'].sudo().create(
            invoice_vals).with_user(self.env.uid)
        invoice.message_post_with_source('mail.message_origin_link',
                                       render_values={'self': invoice,
                                               'origin': order},
                                       subtype_xmlid='mail.mt_note')
        return invoice

    def _prepare_po_line(self, order, tax_ids, amount):
        po_values = {
            'name': _('Pre Payment: %s') % (time.strftime('%m %Y'),),
            'price_unit': amount,
            'product_qty': 0.0,
            'order_id': order.id,
            'product_uom': self.product_id.uom_id.id,
            'product_id': self.product_id.id,
            'taxes_id': [(6, 0, tax_ids)],
            'is_prepayment': True,
            'date_planned': datetime.now(),
        }
        return po_values

    def create_invoices(self):
        purchase_orders = self.env['purchase.order'].browse(
            self._context.get('active_ids', []))
        create_invoice_ids = []
        if self.advance_payment_method == 'delivered':
            self.env.context = dict(self.env.context)
            self.env.context.update({'advance_payment_method': self.advance_payment_method})
            invoice = purchase_orders._create_invoices(final=self.deduct_pre_payments)
            ##To Return invoice ids
            if invoice:
                create_invoice_ids.append(invoice.id)
        else:
            # Create deposit product if necessary
            if not self.product_id:
                vals = self._prepare_deposit_product()
                product_tmpl_id = self.env['product.template'].create(vals)
                self.product_id = product_tmpl_id.product_variant_ids
                self.env['ir.config_parameter'].sudo().set_param(
                    'purchase.default_pre_deposit_product_id', self.product_id.id)

            purchase_line_obj = self.env['purchase.order.line']
            for order in purchase_orders:
                amount, name = self._get_advance_details(order)

                if self.product_id.invoice_policy != 'order':
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
                    tax_ids = order.fiscal_position_id.map_tax(
                        taxes, self.product_id, order.partner_shipping_id).ids
                else:
                    tax_ids = taxes.ids
                po_line_values = self._prepare_po_line(order, tax_ids, amount)
                po_line = purchase_line_obj.create(po_line_values)
                invoice = self._create_invoice(order, po_line, amount)
                if invoice:
                    create_invoice_ids.append(invoice.id)
        if self._context.get('open_invoices', False):
            invoice = purchase_orders.action_view_invoice_advance_payment()
            ##To Return invoice ids & purchase order ids
            if invoice.get('id'):
                create_invoice_ids.append(invoice.get('id'))
            return invoice
        return {'type': 'ir.actions.act_window_close', 'purchase_orders': purchase_orders,
                "create_invoice_ids": create_invoice_ids}

    def _prepare_deposit_product(self):
        # ir_model_data = self.env['ir.model.data']
        return {
            'name': 'Pre payment',
            'type': 'service',
            'invoice_policy': 'order',
            'property_account_expense_id': self.deposit_account_id.id,
            'taxes_id': [(6, 0, self.deposit_taxes_id.ids)],
            'company_id': False,
            'sale_ok': False,
            'purchase_ok': False,
        }

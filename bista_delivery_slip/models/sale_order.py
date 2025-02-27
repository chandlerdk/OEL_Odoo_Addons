# -*- coding: utf-8 -*-


from odoo import models, fields, api

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    discount_total = fields.Monetary(
        string="Discount Applied",
        compute="_compute_discount_total",
        currency_field='currency_id'
    )

    @api.depends('order_line.discount', 'order_line.price_unit', 'order_line.product_uom_qty')
    def _compute_discount_total(self):
        for order in self:
            total_discount = sum(
                line.price_unit * line.product_uom_qty * (line.discount / 100)
                for line in order.order_line if line.discount
            )
            order.discount_total = total_discount

class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    discount_total = fields.Monetary(
        string="Discount Applied",
        currency_field='currency_id'
    )
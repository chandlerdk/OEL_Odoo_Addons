# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, _

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _prepare_delivery_line_vals(self, carrier, price_unit):
        """ Prepare values for creating the delivery line in the repair order. """
        values = {
            'order_id': self.id,
            'product_id': carrier.product_id.id,
            'price_unit': price_unit,
            'product_uom_qty': 1.0,
        }
        return values

    def _create_delivery_line(self, carrier, price_unit):
        values = self._prepare_delivery_line_vals(carrier, price_unit)
        return self.env['sale.order.line'].sudo().create(values)

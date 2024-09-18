# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
from odoo import models


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _get_new_picking_values(self):
        res = super(StockMove, self)._get_new_picking_values()
        sale_id = self.mapped('sale_line_id').mapped('order_id')[:1]
        if sale_id:
            shipstation_delivery = self.env['delivery.carrier'].search([('delivery_type', '=', 'shipstation')]).mapped(
                'product_id')
            shipstation_service = sale_id.order_line.filtered(
                lambda l: l.is_delivery and l.product_id.id in shipstation_delivery.ids)[:1].name
            res.update({'shipstation_service': shipstation_service, 'carrier_id': ''})
        return res


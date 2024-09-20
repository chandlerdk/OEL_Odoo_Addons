# -*- coding: utf-8 -*-

from odoo import api, Command, fields, models


class StockMoveLine(models.Model):
    _inherit = 'purchase.order.line'

    def _prepare_stock_move_vals(self, picking, price_unit, product_uom_qty, product_uom):
        move_vals = super()._prepare_stock_move_vals(picking, price_unit, product_uom_qty, product_uom)
        if picking.picking_type_id.code == 'outgoing':
            if self.order_id.picking_type_id.delivery_generate:
                move_vals.update({
                    'location_id': self.order_id.picking_type_id.delivery_generate_id.default_location_src_id.id,
                    'location_dest_id': self.order_id.picking_type_id.delivery_generate_id.default_location_dest_id.id,
                    'picking_type_id': self.order_id.picking_type_id.delivery_generate_id.id
                })
        if picking.picking_type_id.code == 'incoming':
            if self.order_id.picking_type_id.delivery_generate:
                move_vals.update({
                    'location_id': self.order_id.picking_type_id.default_location_src_id.id,
                    'location_dest_id': self.order_id.picking_type_id.default_location_dest_id.id,
                    'picking_type_id': self.order_id.picking_type_id.id
                })
        return move_vals
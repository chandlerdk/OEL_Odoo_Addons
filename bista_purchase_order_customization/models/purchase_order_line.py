# -*- coding: utf-8 -*-

from odoo import models


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    def _prepare_stock_move_vals(self, picking, price_unit, product_uom_qty, product_uom):
        move_vals = super()._prepare_stock_move_vals(picking, price_unit, product_uom_qty, product_uom)
        po_picking_type = self.order_id.picking_type_id
        if not po_picking_type.delivery_generate:
            return move_vals
        if self.env.context.get('delivery_generate_moves'):
            delivery_type = po_picking_type.delivery_generate_id
            move_vals.update({
                'location_id': delivery_type.default_location_src_id.id,
                'location_dest_id': delivery_type.default_location_dest_id.id,
                'forecasted_location_id': delivery_type.default_location_dest_id.id,
                'picking_type_id': delivery_type.id,
                'move_dest_ids': False,
            })
        else:
            move_vals.update({
                'location_id': po_picking_type.default_location_src_id.id,
                'location_dest_id': po_picking_type.default_location_dest_id.id,
                'picking_type_id': po_picking_type.id,
            })
        return move_vals

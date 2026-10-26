# -*- coding: utf-8 -*-
from odoo import api, Command, fields, models


class StockMove(models.Model):
    _inherit = 'stock.move'

    so_quantity = fields.Float(string="Sale order Quantity")
    remaining_qty = fields.Float(string="Remaining Quantity")


    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('product_uom_qty'):
                vals.update({
                    'so_quantity': vals.get('product_uom_qty')
                })
            # if vals['product_uom_qty']:
            #     vals['so_quantity'] =vals['product_uom_qty']
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('remaining_qty'):
            vals.update({
                'so_quantity':vals.get('remaining_qty')
            })
        return super().write(vals)

    def _is_phantom_kit_component(self):
        """True when this move is a component of a kit. Manufacturing may be uninstalled."""
        self.ensure_one()
        product = self.sale_line_id.product_id
        if not product or 'bom_ids' not in product._fields:
            return False
        return any(bom.type == 'phantom' for bom in product.bom_ids)


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def write(self, vals):
        res = super(SaleOrderLine, self).write(vals)
        if 'product_uom_qty' in vals or 'qty_delivered' in vals:
            for line in self:
                stock_moves = self.env['stock.move'].search([
                    ('sale_line_id', '=', line.id),
                    ('state', 'not in', ['done', 'cancel'])
                ])
                for move in stock_moves:
                    move.remaining_qty = line.product_uom_qty - line.qty_delivered
        return res

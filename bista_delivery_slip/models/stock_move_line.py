# -*- coding: utf-8 -*-
from odoo import api, Command, fields, models


class StockMove(models.Model):
    _inherit = 'stock.move'

    so_quantity = fields.Float(string="Sale order Quantity")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals['product_uom_qty']:
                vals['so_quantity'] =vals['product_uom_qty']
        return super().create(vals_list)

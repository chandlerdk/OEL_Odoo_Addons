# -*- coding: utf-8 -*-
from itertools import count

from odoo import models, fields, api
from datetime import datetime, time



class NegativeForecast(models.Model):
    _name = 'back.order.report'
    _description = 'Back Order Report'

    product_id = fields.Many2one('product.template', string="Product")
    partner_id = fields.Many2one('res.partner',string='Customer')
    sale_id = fields.Many2one('sale.order',string='Sale Order')
    qty = fields.Float(string="Quantity")
    sale_price =fields.Float('Sale Price')
    total_amount = fields.Float('Amount Total')

    def back_order_report_generation(self):
        outgoing_type_id = self.env['stock.picking.type'].search([('code', '=', 'outgoing')], limit=1)
        moves = self.env['stock.move'].search(
            [('state','=','assigned'),('picking_type_id', '=', outgoing_type_id.id)]
        )

        for move in moves:
            sale_id = self.env['sale.order'].search([('name','=',move.origin)])
            self.env['back.order.report'].create({
                'product_id': move.product_id.id,
                'partner_id': move.partner_id.id,
                'sale_id': sale_id.id,
                'qty': move.product_uom_qty ,
                'sale_price': move.product_id.lst_price,
                'total_amount':move.product_uom_qty * move.product_id.lst_price,
            })

    def refresh_back_order_report(self):
        query = "DELETE FROM back_order_report"
        self.env.cr.execute(query)
        return self.back_order_report_generation()

    def ontime_backorder_report_creation(self):
        query = "DELETE FROM back_order_report"
        self.env.cr.execute(query)
        self.back_order_report_generation()

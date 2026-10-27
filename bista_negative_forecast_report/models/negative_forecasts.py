# -*- coding: utf-8 -*-
from collections import defaultdict

from odoo import api, fields, models


class NegativeForecast(models.Model):
    _name = 'negative.forecast'
    _description = 'Negative Forecast'

    product_id = fields.Many2one('product.product', string="Product")
    date = fields.Date(string="Date", default=fields.Date.today)
    negative_qty = fields.Integer(string="Negative Quantity")
    total_qty = fields.Float(string="Total Quantity")

    @api.model
    def future_negative_forecasted_quantity(self):
        # 1. Products with currently negative stock
        negative_qty_products = self.env['product.product'].search([('qty_available', '<', 0)])

        # 2. Current positive on-hand quantities in internal locations
        current_quantities = {
            product.id: quantity
            for product, quantity in self.env['stock.quant']._read_group(
                [('quantity', '>=', 0), ('location_id.usage', '=', 'internal')],
                ['product_id'],
                ['quantity:sum'],
            )
        }

        # 3. Pending moves, in chronological order
        future_moves = self.env['stock.move'].search([
            ('state', 'in', ['waiting', 'confirmed', 'assigned']),
        ], order='date')
        moves_by_product = defaultdict(lambda: self.env['stock.move'])
        for move in future_moves:
            moves_by_product[move.product_id.id] |= move

        def incoming_outgoing(moves):
            incoming = moves.filtered(lambda m: m.picking_type_id.code == 'incoming')
            return sum(incoming.mapped('product_qty')), sum((moves - incoming).mapped('product_qty'))

        # 4. Clear existing records
        self.search([]).unlink()
        today = fields.Date.context_today(self)
        vals_list = []

        # 5. Products currently negative
        for product in negative_qty_products:
            incoming_qty, outgoing_qty = incoming_outgoing(moves_by_product[product.id])
            vals_list.append({
                'product_id': product.id,
                'negative_qty': product.qty_available,
                'date': today,
                'total_qty': product.qty_available + incoming_qty - outgoing_qty,
            })

        # 6. Each future point where the stock crosses into negative
        for product_id in set(current_quantities) | set(moves_by_product):
            current_qty = current_quantities.get(product_id, 0)
            moves_for_product = moves_by_product[product_id]
            incoming_qty, outgoing_qty = incoming_outgoing(moves_for_product)
            forecasted_qty = current_qty + incoming_qty - outgoing_qty
            future_qty = previous_qty = current_qty
            for move in moves_for_product:
                if move.picking_type_id.code == 'incoming':
                    future_qty += move.product_qty
                else:
                    future_qty -= move.product_qty
                if previous_qty >= 0 and future_qty < 0:
                    vals_list.append({
                        'product_id': product_id,
                        'negative_qty': future_qty,
                        'date': move.date,
                        'total_qty': forecasted_qty,
                    })
                previous_qty = future_qty

        self.create(vals_list)

    def refresh_negative_forecast(self):
        return self.future_negative_forecasted_quantity()

    @api.model
    def ontime_future_forcast_report_creation(self):
        self.future_negative_forecasted_quantity()

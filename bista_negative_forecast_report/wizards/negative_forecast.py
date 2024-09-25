# -*- coding: utf-8 -*-
from itertools import count

from odoo import models, fields, api, _
from datetime import datetime


class NegativeForecast(models.TransientModel):
    _name = 'negative.forecast'


    product_id = fields.Many2one('product.template',string="Product")
    date = fields.Date(string="Date")
    negative_qty = fields.Integer(string="Negative Quantity")

    def future_negative_forecasted_quantity(self):
        # 1. Fetch products with currently negative stock (qty_available < 0)
        negative_qty_products = self.env['product.product'].search(
            [('qty_available', '<', 0)]  # Filter for current negative quantities
        )

        # 2. Fetch current positive quantities from stock.quants (to calculate future quantities)
        current_quantities = self.env['stock.quant'].read_group(
            [('quantity', '>', 0)],
            ['product_id', 'quantity'],
            ['product_id']
        )
        future_forecasts = {}

        # 3. Fetch future moves
        future_moves = self.env['stock.move'].search([
            ('state', 'in', ['waiting', 'confirmed', 'assigned']),
            ('date', '>=', fields.Datetime.now())
        ])

        # 4. Adjust current quantities based on future stock moves
        for move in future_moves:
            product_id = move.product_id.id
            future_qty = future_forecasts.get(product_id, 0)
            if move.picking_type_id.code == 'incoming':
                future_qty += move.product_qty
            else:
                future_qty -= move.product_qty
            future_forecasts[product_id] = future_qty

        # 5. Clear existing records
        self.env['negative.forecast'].search([]).unlink()
        today = datetime.today().date()

        # 6. Create records for products currently negative
        for product in negative_qty_products:
            self.env['negative.forecast'].create({
                'product_id': product.id,
                'negative_qty': product.qty_available,
                'date': today
            })

        # 7. Process future negative products
        for product in current_quantities:
            product_id = product['product_id'][0]
            current_qty = product['quantity']
            # Calculate future forecasted quantity

            future_qty = current_qty + future_forecasts.get(product_id, 0)

            # If future quantity is negative, track it
            if future_qty < 0:
                future_move = self.env['stock.move'].search([
                    ('product_id', '=', product_id),
                    ('state', 'in', ['waiting', 'confirmed', 'assigned']),
                    ('date', '>', fields.Datetime.now())
                ], order='date', limit=1)
                 # Create a new record for products that will go negative
                self.env['negative.forecast'].create({
                    'product_id': product_id,
                    'negative_qty': future_qty,  # Future negative quantity
                    'date': future_move.date if future_move else today  # Date of the future move
                })

        return {
            'type': 'ir.actions.act_window',
            'name': 'Negative Forecast Report',
            'res_model': 'negative.forecast',
            'view_mode': 'tree',
            'view_id': self.env.ref('bista_negative_forecast_report.view_negative_forecast_report_tree').id,
            'target': 'inline',
        }
# -*- coding: utf-8 -*-
from itertools import count

from odoo import models, fields, api
from datetime import datetime, time


class NegativeForecast(models.Model):
    _name = 'negative.forecast'
    _description = 'Negative Forecast'

    product_id = fields.Many2one('product.template', string="Product")
    date = fields.Date(string="Date", default=fields.Date.today)
    negative_qty = fields.Integer(string="Negative Quantity")
    total_qty = fields.Float(string="Total Quantity")

    def future_negative_forecasted_quantity(self):
        # 1. Fetch products with currently negative stock (qty_available < 0)
        negative_qty_products = self.env['product.product'].search(
            [('qty_available', '<', 0)]  # Filter for current negative quantities
        )
        # 2. Fetch current positive quantities from stock.quants (to calculate future quantities)
        current_quantities = self.env['stock.quant'].read_group(
            [
                ('quantity', '>=', 0),
                ('location_id.usage', '=', 'internal')
            ],
            ['product_id', 'quantity'],
            ['product_id']
        )
        # 3. Fetch future moves
        future_moves = self.env['stock.move'].search([
            ('state', 'in', ['waiting', 'confirmed', 'assigned']),
        ], order='date')

        # 4. Clear existing records
        self.env['negative.forecast'].search([]).unlink()
        today = datetime.today().date()

        # 5. Create records for products currently negative
        for product in negative_qty_products:
            incoming_qty = sum(future_moves.filtered(
                lambda m: m.product_id.id == product.id and m.picking_type_id.code == 'incoming').mapped('product_qty'))
            outgoing_qty = sum(future_moves.filtered(
                lambda m: m.product_id.id == product.id and m.picking_type_id.code != 'incoming').mapped('product_qty'))

            forecasted_qty = product.qty_available + incoming_qty - outgoing_qty

            self.env['negative.forecast'].create({
                'product_id': product.id,
                'negative_qty': product.qty_available,
                'date': today,
                'total_qty': forecasted_qty  # Forecasted quantity calculation
            })
        # for product in negative_qty_products:
        #     self.env['negative.forecast'].create({
        #         'product_id': product.id,
        #         'negative_qty': product.qty_available,
        #         'date': today,
        #         'total_qty':
        #     })

        # 6. Process products and track multiple negative points
        all_product_ids = set([quant['product_id'][0] for quant in current_quantities]) | set(
            future_moves.mapped('product_id.id'))

        for product_id in all_product_ids:
            current_qty = next(
                (quant['quantity'] for quant in current_quantities if quant['product_id'][0] == product_id), 0
            )

            # Get all moves related to this product, sorted by date
            moves_for_product = future_moves.filtered(lambda m: m.product_id.id == product_id)
            future_qty = current_qty
            previous_qty = current_qty
            negative_recorded = False

            # Loop through the future moves in chronological order
            for move in moves_for_product:
                if move.picking_type_id.code == 'incoming':
                    future_qty += move.product_qty
                else:
                    future_qty -= move.product_qty
                # Check if the stock crosses into negative territory
                if previous_qty >= 0 and future_qty < 0:
                    incoming_qty = sum(
                        moves_for_product.filtered(lambda m: m.picking_type_id.code == 'incoming').mapped(
                            'product_qty'))
                    outgoing_qty = sum(
                        moves_for_product.filtered(lambda m: m.picking_type_id.code != 'incoming').mapped(
                            'product_qty'))
                    forecasted_qty = current_qty + incoming_qty - outgoing_qty
                    self.env['negative.forecast'].create({
                        'product_id': product_id,
                        'negative_qty': future_qty,
                        'date': move.date,
                        'total_qty':forecasted_qty
                    })
                    negative_recorded = True

                # Reset negative tracking if stock turns positive again
                if future_qty >= 0:
                    negative_recorded = False

                # Update previous_qty for the next iteration
                previous_qty = future_qty

        # tree_view_id = self.env.ref('bista_negative_forecast_report.view_negative_forecast_report_tree').id
        # search_view_id = self.env.ref('bista_negative_forecast_report.view_negative_forecast_search').id
        # action = self.env.ref('bista_negative_forecast_report.action_negative_forecast_tree').read()[0]
        # return action
        # return {
        #     'type': 'ir.actions.act_window',
        #     'name': 'Negative Forecast Report',
        #     'res_model': 'negative.forecast',
        #     'views': [(tree_view_id, 'tree'), (False, 'form')],
        #     'search_view_id': [search_view_id, 'search'],
        #   'target': 'inline',
        # }

    def refresh_negative_forecast(self):
        return self.future_negative_forecasted_quantity()

    def ontime_future_forcast_report_creation(self):
        query = "DELETE FROM negative_forecast"
        self.env.cr.execute(query)
        self.future_negative_forecasted_quantity()

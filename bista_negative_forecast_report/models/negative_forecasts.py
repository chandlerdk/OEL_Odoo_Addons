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

    # def future_negative_forecasted_quantity(self):
    #     # 1. Fetch products with currently negative stock (qty_available < 0)
    #     negative_qty_products = self.env['product.product'].search(
    #         [('qty_available', '<', 0)]  # Filter for current negative quantities
    #     )
    #
    #     # 2. Fetch current positive quantities from stock.quants (to calculate future quantities)
    #     current_quantities = self.env['stock.quant'].read_group(
    #         [
    #             ('quantity', '>=', 0),
    #             ('location_id.usage', '=', 'internal')
    #         ],
    #         ['product_id', 'quantity'],
    #         ['product_id']
    #     )
    #
    #     print("current_quantitiesss", current_quantities)
    #     future_forecasts = {}
    #
    #     # 3. Fetch future moves
    #     future_moves = self.env['stock.move'].search([
    #         ('state', 'in', ['waiting', 'confirmed', 'assigned']),
    #         # ('date', '>=', fields.Datetime.now())
    #     ])
    #
    #     print("future_movessssss", future_moves)
    #
    #     # 4. Adjust current quantities based on future stock moves
    #     for move in future_moves:
    #         product_id = move.product_id.id
    #         future_qty = future_forecasts.get(product_id, 0)
    #         if move.picking_type_id.code == 'incoming':
    #             future_qty += move.product_qty
    #         else:
    #             future_qty -= move.product_qty
    #         print("product_iddd", product_id)
    #         print("futureqtyyyy", future_qty)
    #         future_forecasts[product_id] = future_qty
    #
    #     # 5. Clear existing records
    #     self.env['negative.forecast'].search([]).unlink()
    #     today = datetime.today().date()
    #
    #     # 6. Create records for products currently negative
    #     for product in negative_qty_products:
    #         self.env['negative.forecast'].create({
    #             'product_id': product.id,
    #             'negative_qty': product.qty_available,
    #             'date': today
    #         })
    #
    #     # 7. Process future negative products
    #     # for product in current_quantities:
    #     #     product_id = product['product_id'][0]
    #     #     current_qty = product['quantity']
    #     #     print("product_idddd",product_id)
    #     #     print("current_qtyyyy",current_qty)
    #     #
    #     #     future_qty = current_qty + future_forecasts.get(product_id, 0)
    #     #     print("futurequyyyy",future_qty)
    #     #
    #     #     # If future quantity is negative, track it
    #     #     if future_qty < 0:
    #     #         future_move = self.env['stock.move'].search([
    #     #             ('product_id', '=', product_id),
    #     #             ('state', 'in', ['waiting', 'confirmed', 'assigned']),
    #     #             ('date', '>=', today)
    #     #         ], order='date', limit=1)
    #     #         # Create a new record for products that will go negative
    #     #         self.env['negative.forecast'].create({
    #     #             'product_id': product_id,
    #     #             'negative_qty': future_qty,  # Future negative quantity
    #     #             'date': future_move.date if future_move else today  # Date of the future move
    #     #         })
    #
    #     all_product_ids = set([quant['product_id'][0] for quant in current_quantities]) | set(future_forecasts.keys())
    #
    #     for product_id in all_product_ids:
    #         current_qty = next(
    #             (quant['quantity'] for quant in current_quantities if quant['product_id'][0] == product_id), 0
    #         )
    #
    #         # Sort future moves by date to simulate their impact over time
    #         moves_for_product = future_moves.filtered(lambda m: m.product_id.id == product_id)
    #         future_qty = current_qty
    #         first_negative_move = None
    #
    #         # Loop through the future moves in chronological order
    #         for move in moves_for_product:
    #             if move.picking_type_id.code == 'incoming':
    #                 future_qty += move.product_qty
    #             else:
    #                 future_qty -= move.product_qty
    #
    #             print(f"Move ID {move.id} - Adjusted future_qty for product {product_id}: {future_qty}")
    #
    #             # If the stock goes negative, capture the first move causing the negative balance
    #             if future_qty < 0 and not first_negative_move:
    #                 first_negative_move = move
    #                 print(
    #                     f"First negative move detected for product {product_id}: Move ID {move.id}, Date: {move.date}")
    #                 break  # We only care about the first negative occurrence
    #
    #         # If the product will go negative in the future, record it
    #         if first_negative_move:
    #             print("first_negative_move",first_negative_move)
    #             self.env['negative.forecast'].create({
    #                 'product_id': product_id,
    #                 'negative_qty': future_qty,  # Future negative quantity
    #                 'date': first_negative_move.date  # Date of the first move causing negative stock
    #             })
    #
    #     return {
    #         'type': 'ir.actions.act_window',
    #         'name': 'Negative Forecast Report',
    #         'res_model': 'negative.forecast',
    #         'view_mode': 'tree',
    #         'view_id': self.env.ref('bista_negative_forecast_report.view_negative_forecast_report_tree').id,
    #         'target': 'inline',
    #     }

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
        future_forecasts = {}

        # 3. Fetch future moves
        future_moves = self.env['stock.move'].search([
            ('state', 'in', ['waiting', 'confirmed', 'assigned']),
        ], order='date')

        # 4. Clear existing records
        self.env['negative.forecast'].search([]).unlink()
        today = datetime.today().date()

        # 5. Create records for products currently negative
        for product in negative_qty_products:
            self.env['negative.forecast'].create({
                'product_id': product.id,
                'negative_qty': product.qty_available,
                'date': today
            })

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
                # Adjust the stock level based on the move type (incoming/outgoing)
                if move.picking_type_id.code == 'incoming':
                    future_qty += move.product_qty
                else:
                    future_qty -= move.product_qty

                # Check if the stock crosses into negative territory
                if previous_qty >= 0 and future_qty < 0:
                    # If it crosses from positive to negative, create a record
                    self.env['negative.forecast'].create({
                        'product_id': product_id,
                        'negative_qty': future_qty,  # Future negative quantity
                        'date': move.date  # Date of the move that caused negative stock
                    })
                    negative_recorded = True

                # Reset negative tracking if stock turns positive again
                if future_qty >= 0:
                    negative_recorded = False

                # Update previous_qty for the next iteration
                previous_qty = future_qty

        tree_view_id = self.env.ref('bista_negative_forecast_report.view_negative_forecast_report_tree').id
        search_view_id = self.env.ref('bista_negative_forecast_report.view_negative_forecast_search').id

        return {
            'type': 'ir.actions.act_window',
            'name': 'Negative Forecast Report',
            'res_model': 'negative.forecast',
            'views': [(tree_view_id, 'tree'), (False, 'form')],
            'search_view_id': [search_view_id, 'search'],
          'target': 'inline',
        }

    # def action_add_from_catalog(self):
    #     kanban_view_id = self.env.ref('product.product_view_kanban_catalog').id
    #     search_view_id = self.env.ref('product.product_view_search_catalog').id
    #     tree_view_id = self.env.ref('bista_add_catalog_sale.product_view_tree_catalog').id
    #     additional_context = self._get_action_add_from_catalog_extra_context()
    #     return {
    #         'type': 'ir.actions.act_window',
    #         'name': _('Products'),
    #         'res_model': 'product.product',
    #         'views': [(tree_view_id, 'kanban'), (False, 'form')],
    #         'search_view_id': [search_view_id, 'search'],
    #         'domain': self._get_product_catalog_domain(),
    #         'context': {*self.env.context, *additional_context},
    #     }

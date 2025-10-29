# -*- coding: utf-8 -*-
from odoo import api, fields, models

class StockWarehouseOrderpoint(models.Model):
    _inherit = 'stock.warehouse.orderpoint'

    demand_basis = fields.Selection(
        selection=[
            ('forecast', 'Forecast'),
            ('forecasted', 'Forecasted'),
        ],
        string='Demand Basis',
        default='forecast',
        help=(
            'Choose which quantity field to use for reordering:\n'
            '- Forecast: Uses qty_forecast field.\n'
            '- Forecasted: Uses x_studio_forecasted field.'
        )
    )

    @api.depends(
        'product_min_qty', 'product_max_qty', 'qty_multiple',
        'qty_forecast', 'x_studio_forecasted', 'demand_basis',
    )
    def _compute_qty_to_order(self):
        """
        Override to use demand_basis to pick between qty_forecast and x_studio_forecasted.
        This controls whether the rule appears in the "To Reorder" list.
        """
        for op in self:
            if not op.product_id or not op.location_id:
                op.qty_to_order = 0.0
                continue

            # Pick the available qty based on demand_basis
            if op.demand_basis == 'forecast':
                available = op.qty_forecast or 0.0
            else:
                # Use x_studio_forecasted for 'forecasted' option
                available = op.x_studio_forecasted or 0.0

            min_qty = op.product_min_qty
            max_qty = op.product_max_qty or 0.0
            multiple = op.qty_multiple or 1.0

            # Compute qty to order
            if available < min_qty:
                # Order up to max if set, otherwise to min
                target = max_qty if max_qty > 0 else min_qty
                qty = target - available
                                         
                # Round up to multiple if needed
                if multiple and multiple > 1:
                    remainder = qty % multiple
                    if remainder:
                        qty += (multiple - remainder)

                op.qty_to_order = max(qty, 0.0)
            else:
                # Available is above min, no need to reorder
                op.qty_to_order = 0.0

    def write(self, vals):
        """
        Prevent auto-snooze when demand_basis changes and causes qty_to_order to drop to 0.
        """
        # If demand_basis is being changed, clear any snooze
        if 'demand_basis' in vals:
            for op in self:
                if op.snoozed_until:
                    vals['snoozed_until'] = False
                    break

        return super(StockWarehouseOrderpoint, self).write(vals)

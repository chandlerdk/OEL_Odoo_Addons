# -*- coding: utf-8 -*-
###############################################################################
#    License, author and contributors information in:                         #
#    __manifest__.py file at the root folder of this module.                  #
###############################################################################

from odoo import models, fields, api, _
from datetime import timedelta


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    tracking_ref = fields.Html(copy=False)

    # delivered_qty = fields.Float(
    #     string='Delivered Quantity',
    #     compute='_compute_delivered_qty',
    #     store=True,
    #     help="Delivered quantity of the product in the unit of measure specified on the sale order line."
    # )
    #
    # @api.depends('move_ids.state', 'move_ids.quantity', 'move_ids.picking_id')
    # def _compute_delivered_qty(self):
    #     for line in self:
    #         line.delivered_qty = 0
    #         pickings = line.move_ids.mapped('picking_id').filtered(lambda p: p.state in ['assigned', 'done'])
    #         current_picking = pickings and pickings[-1]
    #         if not current_picking:
    #             continue
    #         current_moves = line.move_ids.filtered(lambda m: m.picking_id == current_picking and m.state == 'done')
    #         if line.product_id.bom_ids and line.product_id.bom_ids[0].type == 'phantom':
    #             bom = line.product_id.bom_ids[0]
    #             total_delivered = 0
    #             for bom_line in bom.bom_line_ids:
    #                 moves = current_moves.filtered(lambda m: m.product_id == bom_line.product_id)
    #                 total_qty_done = sum(moves.mapped('quantity'))
    #                 kit_qty_done = total_qty_done / bom_line.product_qty
    #                 total_delivered = kit_qty_done
    #             line.delivered_qty = total_delivered
    #         else:
    #             line.delivered_qty = sum(current_moves.mapped('quantity'))

    def _get_tracking_ref(self):
        for x in self:
            picking_ids = x.mapped("move_ids").mapped("picking_id")
            if not picking_ids:
                continue
            done_picking = picking_ids.filtered(lambda rec: rec.state == 'done' and rec.carrier_tracking_ref)
            tracking_refs = []
            for picking in done_picking:
                ref = f"<span style='color: gray; font-style: italic'>{picking.date_done.strftime('%m/%d/%Y')}" \
                      f"</span><p><b>{picking.carrier_tracking_ref}</b></p>"
                if ref not in tracking_refs:
                    tracking_refs.append(ref)
            if len(tracking_refs):
                x.tracking_ref = "".join(tracking_refs)

# -*- encoding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (http://www.bistasolutions.com)
#
##############################################################################

from odoo import api, fields, models


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    delivery_generate = fields.Boolean(string="Delivery Generate", related="picking_type_id.delivery_generate")
    outgoing_picking_count = fields.Integer("Outgoing Shipment count", compute='_compute_outgoing_picking_count')

    @api.depends('picking_ids')
    def _compute_incoming_picking_count(self):
        for order in self:
            order.incoming_picking_count = len(order.picking_ids.filtered(lambda p: p.picking_type_id.code == 'incoming'))

    @api.depends('picking_ids')
    def _compute_outgoing_picking_count(self):
        for order in self:
            order.outgoing_picking_count = len(order.picking_ids.filtered(lambda p: p.picking_type_id.code == 'outgoing'))

    def action_view_incoming_picking(self):
        return self._get_action_view_picking(self.picking_ids.filtered(lambda p: p.picking_type_id.code == 'incoming'))

    def action_view_delivery_picking(self):
        return self._get_action_view_picking(self.picking_ids.filtered(lambda p: p.picking_type_id.code == 'outgoing'))

    def _get_action_view_picking(self, pickings):
        result = super()._get_action_view_picking(pickings)
        result['context'] = dict(result.get('context') or {}, default_origin=self.name)
        return result

    def button_approve(self, force=False):
        result = super().button_approve(force=force)
        self.filtered('delivery_generate')._create_delivery()
        return result

    def _create_delivery(self):
        for order in self.filtered(lambda po: po.state == 'purchase'):
            if any(product.type == 'consu' for product in order.order_line.product_id):
                order = order.with_company(order.company_id)
                moves = order.order_line.with_context(delivery_generate_moves=True)._create_stock_moves()
                moves = moves.with_env(order.env).filtered(lambda x: x.state not in ('done', 'cancel')).with_context(
                    move_picking_partner_id=order.partner_id).sudo()._action_confirm()
                moves._action_assign()
                pickings = moves.picking_id
                forward_pickings = self.env['stock.picking']._get_impacted_pickings(moves)
                (pickings | forward_pickings).action_confirm()
                pickings.message_post_with_source(
                    'mail.message_origin_link',
                    render_values={'self': pickings, 'origin': order},
                    subtype_xmlid='mail.mt_note',
                )
        return True


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    def _get_qty_procurement(self):
        if self.order_id.delivery_generate:
            return 0.0
        return super()._get_qty_procurement()

    def _prepare_qty_received(self):
        """Delivery generate orders only count their receipts: the deliveries sent to
        the vendor must not be deducted as purchase returns."""
        dg_lines = self.filtered(lambda l: l.qty_received_method == 'stock_moves' and l.order_id.delivery_generate)
        received_qties = super(PurchaseOrderLine, self - dg_lines)._prepare_qty_received()
        for line in dg_lines:
            total = 0.0
            for move in line._get_po_line_moves().filtered(lambda m: m.state == 'done'):
                quantity = move.uom_id._compute_quantity(move.quantity, line.uom_id, rounding_method='HALF-UP')
                if move._is_purchase_return():
                    if move.origin_returned_move_id and move.to_refund:
                        total -= quantity
                elif move.origin_returned_move_id and move.origin_returned_move_id._is_purchase_return() and not move.to_refund:
                    pass
                elif move.picking_type_id.code == 'incoming':
                    total += quantity
            line._track_qty_received(total)
            received_qties[line] = total
        return received_qties

# -*- encoding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (http://www.bistasolutions.com)
#
##############################################################################

from odoo import api, Command, fields, models, SUPERUSER_ID, _
from odoo.exceptions import UserError,ValidationError


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"


    delivery_generate = fields.Boolean(string="Delivery Generate",related="picking_type_id.delivery_generate")
    outgoing_picking_count = fields.Integer("Incoming Shipment count", compute='_compute_outgoing_picking_count')
    incoming_picking_count = fields.Integer("Incoming Shipment count", compute='_compute_incoming_picking_count')

    @api.depends('picking_ids')
    def _compute_incoming_picking_count(self):
        for order in self:
            incoming_pickings = order.picking_ids.filtered(lambda p: p.picking_type_id.code == 'incoming')
            order.incoming_picking_count = len(incoming_pickings)
            self.action_view_picking()

    def action_view_incoming_picking(self):
        incoming_pickings = self.picking_ids.filtered(lambda p: p.picking_type_id.code == 'incoming')
        return self._get_action_view_picking(incoming_pickings)

    def _get_action_view_picking(self, pickings):
        """ This function returns an action that display existing picking orders of given purchase order ids. When only one found, show the picking immediately.
        """
        self.ensure_one()
        result = self.env["ir.actions.actions"]._for_xml_id('stock.action_picking_tree_all')
        # override the context to get rid of the default filtering on operation type
        result['context'] = {'default_partner_id': self.partner_id.id, 'default_origin': self.name, 'default_picking_type_id': self.picking_type_id.id}
        # choose the view_mode accordingly
        if not pickings or len(pickings) > 1:
            result['domain'] = [('id', 'in', pickings.ids)]
        elif len(pickings) == 1:
            res = self.env.ref('stock.view_picking_form', False)
            form_view = [(res and res.id or False, 'form')]
            result['views'] = form_view + [(state, view) for state, view in result.get('views', []) if view != 'form']
            result['res_id'] = pickings.id
        return result


    @api.depends('picking_ids')
    def _compute_outgoing_picking_count(self):
        for order in self:
            outgoing_pickings = order.picking_ids.filtered(lambda p: p.picking_type_id.code == 'outgoing')
            order.outgoing_picking_count = len(outgoing_pickings)

    def action_view_delivery_picking(self):
        outgoing_pickings = self.picking_ids.filtered(lambda p: p.picking_type_id.code == 'outgoing')
        return self._get_action_view_delivery_picking(outgoing_pickings)

    def _get_action_view_delivery_picking(self, pickings):
        """ This function returns an action that display existing picking orders of given purchase order ids. When only one found, show the picking immediately.
        """
        self.ensure_one()
        result = self.env["ir.actions.actions"]._for_xml_id('stock.action_picking_tree_all')
        # override the context to get rid of the default filtering on operation type
        result['context'] = {'default_partner_id': self.partner_id.id, 'default_origin': self.name, 'default_picking_type_id': self.picking_type_id.id}
        # choose the view_mode accordingly
        if not pickings or len(pickings) > 1:
            result['domain'] = [('id', 'in', pickings.ids)]
        elif len(pickings) == 1:
            res = self.env.ref('stock.view_picking_form', False)
            form_view = [(res and res.id or False, 'form')]
            result['views'] = form_view + [(state, view) for state, view in result.get('views', []) if view != 'form']
            result['res_id'] = pickings.id
        return result

    def _check_delivery_done(self):
        for order in self:
            delivery_pickings = self.env['stock.picking'].search([
                ('origin', '=', order.name),
                ('picking_type_id', '=', self.picking_type_id.delivery_generate_id.id),
                ('state', '!=', 'done')
            ])
            if delivery_pickings:
                return False
        return True

    def button_approve(self, force=False):
        result = super(PurchaseOrder, self).button_approve(force=force)
        if self.delivery_generate:
            self._create_delivery()
        return result

    def _prepare_picking(self):
        if not self.group_id:
            self.group_id = self.group_id.create({
                'name': self.name,
                'partner_id': self.partner_id.id
            })
        if not self.partner_id.property_stock_supplier.id:
            raise UserError(_("You must set a Vendor Location for this partner %s", self.partner_id.name))
        if self.picking_type_id.delivery_generate:
            return {
                'picking_type_id': self.picking_type_id.id,
                'partner_id': self.partner_id.id,
                'user_id': False,
                'date': self.date_order,
                'origin': self.name,
                'location_dest_id':self.picking_type_id.default_location_dest_id.id ,
                'location_id': self.picking_type_id.default_location_src_id.id,
                'company_id': self.company_id.id,
                'state': 'draft',
            }
        else:
            return {
                'picking_type_id': self.picking_type_id.id,
                'partner_id': self.partner_id.id,
                'user_id': False,
                'date': self.date_order,
                'origin': self.name,
                'location_dest_id': self._get_destination_location(),
                'location_id': self.partner_id.property_stock_supplier.id,
                'company_id': self.company_id.id,
                'state': 'draft',
            }


    def _prepare_delivery_picking(self):
        if not self.group_id:
            self.group_id = self.env['procurement.group'].create({
                'name': self.name,
                'partner_id': self.partner_id.id
            })
        # stock_location = self.env.ref('stock.stock_location_stock')
        # if not self.partner_id.property_stock_supplier.id:
        #     raise UserError(_("You must set a Vendor Location for this partner %s", self.partner_id.name))
        return {
            'picking_type_id': self.picking_type_id.delivery_generate_id.id,
            'partner_id': self.partner_id.id,
            'user_id': False,
            'date': self.date_order,
            'origin': self.name,
            'location_dest_id': self.picking_type_id.delivery_generate_id.default_location_dest_id.id,
            'location_id': self.picking_type_id.delivery_generate_id.default_location_src_id.id,
            # 'location_dest_id': self.partner_id.property_stock_supplier.id,
            # 'location_id': stock_location.id,
            'company_id': self.company_id.id,
            'state': 'draft',
        }

    def _create_delivery(self):
        StockPicking = self.env['stock.picking']
        for order in self.filtered(lambda po: po.state in ('purchase', 'done')):
            if any(product.type in ['product', 'consu'] for product in order.order_line.product_id):
                order = order.with_company(order.company_id)
                res = order._prepare_delivery_picking()
                picking = StockPicking.with_user(SUPERUSER_ID).create(res)
                moves = order.order_line._create_stock_moves(picking)
                moves = moves.filtered(lambda x: x.state not in ('done', 'cancel'))._action_confirm()
                moves._action_assign()
                forward_pickings = self.env['stock.picking']._get_impacted_pickings(moves)
                (picking | forward_pickings).action_confirm()
                picking.message_post_with_source(
                    'mail.message_origin_link',
                    render_values={'self': picking, 'origin': order},
                    subtype_xmlid='mail.mt_note',
                )
        return True


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    def _get_qty_procurement(self):
        self.ensure_one()
        qty = 0.0
        if self.order_id.delivery_generate:
            return qty
        else:
            outgoing_moves, incoming_moves = self._get_outgoing_incoming_moves()
            for move in outgoing_moves:
                qty_to_compute = move.quantity if move.state == 'done' else move.product_uom_qty
                qty -= move.product_uom._compute_quantity(qty_to_compute, self.product_uom, rounding_method='HALF-UP')
            for move in incoming_moves:
                qty_to_compute = move.quantity if move.state == 'done' else move.product_uom_qty
                qty += move.product_uom._compute_quantity(qty_to_compute, self.product_uom, rounding_method='HALF-UP')
        return qty




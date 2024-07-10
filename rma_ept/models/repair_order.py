# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

from odoo import fields, models
from odoo.tools.misc import clean_context
STOCK_PICKING = 'stock.picking'
PROCUREMENT_GROUP = 'procurement.group'


class RepairOrder(models.Model):
    _inherit = "repair.order"

    claim_id = fields.Many2one('crm.claim.ept', string='Claim')
    picking_ids = fields.Many2many(STOCK_PICKING, string="Picking")

    def action_repair_done(self):
        """Inherit this method for checking created repair order is from claim"""
        result = super().action_repair_done()
        if self.claim_id and self.claim_id.return_picking_id:
            self.repair_action_launch_stock_rule()
        return result

    def repair_action_launch_stock_rule(self):
        """based on this method to create a picking one..two or three step."""
        procurements = []
        picking_vals = {}

        vals = self._prepare_procurement_group_vals()
        group_id = self.env[PROCUREMENT_GROUP].create(vals)

        values = self._prepare_procurement_values(group_id)
        location_id = self.claim_id.partner_delivery_id.property_stock_customer

        procurements.append(self.env[PROCUREMENT_GROUP].Procurement(
            self.product_id, self.product_qty, self.product_id.uom_id,
            location_id, self.name, self.claim_id.name, self.company_id, values))

        if procurements:
            self.env[PROCUREMENT_GROUP].with_context(
                clean_context(self.env.context)).run(procurements)
        pickings = self.env[STOCK_PICKING].search(
            [('group_id', '=', group_id.id)])
        picking_ids = self.picking_ids.ids + pickings.ids
        picking = pickings[-1]

        if picking.location_id.id != self.location_id.id:
            picking_vals.update({'location_id': self.location_id.id})
            picking.write({'location_id': self.location_id.id})
            picking.action_assign()
            move_line = picking.move_line_ids_without_package

            if self.lot_id and move_line and move_line.lot_id.id != self.lot_id.id:
                picking.move_line_ids_without_package.write(
                    {'lot_id': self.lot_id.id})

        self.write({'picking_ids': [(6, 0, picking_ids)]})

    def _prepare_procurement_values(self, group_id):
        """prepare values for procurement"""
        return {
            'group_id': group_id,
            'warehouse_id': self.claim_id.sale_id.warehouse_id or False,
            'partner_id': self.partner_id.id,
            'company_id': self.company_id,
            'repair_order_id': self.id,
        }

    def _prepare_procurement_group_vals(self):
        """prepare a procurement group vals."""
        return {
            'name': self.name,
            'partner_id': self.claim_id.partner_delivery_id.id,
            'move_type': self.claim_id.sale_id.picking_policy,
        }

    def show_delivery_picking(self):
        """display the delivery orders on RMA."""
        if len(self.picking_ids) == 1:
            picking_action = {
                'name': "Delivery",
                'view_mode': 'form',
                'res_model': STOCK_PICKING,
                'type': 'ir.actions.act_window',
                'res_id': self.picking_ids.id
            }
        else:
            picking_action = {
                'name': "Deliveries",
                'view_mode': 'tree,form',
                'res_model': STOCK_PICKING,
                'type': 'ir.actions.act_window',
                'domain': [('id', 'in', self.picking_ids.ids)]
            }
        return picking_action

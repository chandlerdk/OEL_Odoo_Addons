# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

from odoo import fields, models
from odoo.tools.misc import clean_context
STOCK_PICKING = 'stock.picking'


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

        # Odoo 20: use stock.reference + stock.rule.run
        reference = self.env['stock.reference'].create(
            self._prepare_stock_reference_vals())
        values = self._prepare_procurement_values(reference)
        location_id = self.claim_id.partner_delivery_id.property_stock_customer

        procurements.append(self.env['stock.rule'].Procurement(
            self.product_id, self.product_qty, self.product_id.uom_id,
            location_id, self.name, self.claim_id.name, self.company_id, values))

        if procurements:
            self.env['stock.rule'].with_context(
                clean_context(self.env.context)).run(procurements)
        pickings = reference.picking_ids
        if pickings:
            pickings.write({'repair_order_id': self.id})
        picking_ids = self.picking_ids.ids + pickings.ids
        if not pickings:
            self.write({'picking_ids': [(6, 0, picking_ids)]})
            return

        picking = pickings[-1]

        if picking.location_id.id != self.location_id.id:
            picking.write({'location_id': self.location_id.id})
            picking.action_assign()
            move_line = picking.move_line_ids

            if self.lot_id and move_line and move_line.lot_id.id != self.lot_id.id:
                picking.move_line_ids.write({'lot_id': self.lot_id.id})

        self.write({'picking_ids': [(6, 0, picking_ids)]})

    def _prepare_stock_reference_vals(self):
        """Prepare stock.reference vals for procurement linking."""
        return {
            'name': self.name,
        }

    def _prepare_procurement_values(self, reference):
        """prepare values for procurement"""
        return {
            'reference_ids': reference,
            'warehouse_id': self.claim_id.sale_id.warehouse_id or False,
            'partner_id': self.partner_id.id,
            'company_id': self.company_id,
            'repair_order_id': self.id,
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
                'view_mode': 'list,form',
                'res_model': STOCK_PICKING,
                'type': 'ir.actions.act_window',
                'domain': [('id', 'in', self.picking_ids.ids)]
            }
        return picking_action

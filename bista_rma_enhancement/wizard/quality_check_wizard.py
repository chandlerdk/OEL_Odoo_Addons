# -*- coding: utf-8 -*-

from odoo import models, _
from odoo.exceptions import UserError, ValidationError


class QualityCheckWizard(models.TransientModel):
    _inherit = 'quality.check.wizard'

    def action_generate_next_window(self):
        res = super().action_generate_next_window()
        picking_id = self.current_check_id.picking_id
        if self.is_last_check:
            picking_id.with_context(picking_id=picking_id).button_validate()
        return res

    def do_repair(self):
        RepairOrder = self.env["repair.order"]
        current_check_id = self.current_check_id
        vals = {
            'quality_check_id': current_check_id.id,
            'partner_id': current_check_id.partner_id.id,
            'product_id': current_check_id.product_id.id,
            'lot_id': current_check_id.lot_id.id,
            'product_qty': self.qty_line,
            'location_id': current_check_id.picking_id.location_dest_id.id,
            'company_id': current_check_id.company_id.id,
            'picking_id': current_check_id.picking_id.id,
            'picking_type_id': current_check_id.picking_id.picking_type_id.warehouse_id.repair_type_id.id
        }
        RepairOrder.create(vals)
        current_check_id.do_fail()
        return self.action_generate_next_window()

    def do_refurbish(self):
        BomLine = self.env['mrp.bom.line']
        MrpProduction = self.env['mrp.production']
        current_check_id = self.current_check_id
        warehouse_id = current_check_id.picking_id.location_dest_id.warehouse_id
        bom_line_id = BomLine.search([
            ('product_id', '=', current_check_id.product_id.id)
        ], limit=1)
        if not bom_line_id:
            raise UserError(_(
                "No Refurbished Bills of Materials found for this product => %s.",
                current_check_id.product_id.display_name,
            ))
        mrp_id = MrpProduction.create({
            'quality_check_id': current_check_id.id,
            'bom_id': bom_line_id.bom_id.id,
            'picking_type_id': warehouse_id.manu_refurbish_type_id.id
        })
        mrp_id._compute_bom_id()
        # mrp_id._compute_picking_type_id()
        current_check_id.do_fail()
        return self.action_generate_next_window()

    def get_rma_scrap_location(self):
        location_id = self.env['stock.location'].search([
            ('company_id', '=', self.env.company.id), ('rma_scrap_location', '=', True), ('scrap_location', '=', True)
        ])
        if not location_id:
            raise ValidationError(_(
                "The scrap location has not been configured."
            ))
        return location_id

    def do_scrap(self):
        StockScrap = self.env['stock.scrap']
        current_check_id = self.current_check_id
        vals = {
            'quality_check_id': current_check_id.id,
            'product_id': current_check_id.product_id.id,
            'scrap_qty': self.qty_line,
            'lot_id': current_check_id.move_line_id.lot_id.id,
            'location_id': current_check_id.picking_id.location_dest_id.id,
            'scrap_location_id': self.get_rma_scrap_location().id,
            'picking_id': False,
        }
        StockScrap.create(vals)
        current_check_id.do_fail()
        return self.action_generate_next_window()

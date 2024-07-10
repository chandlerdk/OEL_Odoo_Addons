# -*- coding: utf-8 -*-

from odoo import fields, models, api, _
from odoo.exceptions import UserError


class QualityCheck(models.Model):
    _inherit = 'quality.check'

    disposition_workflow = fields.Selection([
        ('pass', '(Pass) Use As Is'),
        ('repair', 'Repair'),
        ('refurbish', 'Refurbish'),
        ('scrap', 'Scrap')], string='Disposition Workflow', tracking=True,
        copy=False
    )
    repairs_count = fields.Integer(compute='_compute_repairs_count')
    mrp_count = fields.Integer(compute='_compute_mrp_count')
    scrap_count = fields.Integer(compute='_compute_scrap_count')
    int_transfer_count = fields.Integer(compute='_compute_int_transfer_count')

    def write(self, vals):
        picking_id = self.picking_id
        if 'picking_id' in vals:
            vals['picking_id'] = picking_id.id
        res = super().write(vals)
        for rec in self:
            if rec.move_line_id.lot_id:
                lot_id = rec.move_line_id.lot_id
                scrap_id = rec.env['stock.scrap'].search(
                    [('quality_check_id', '=', rec.id)], limit=1)
                repair_order = rec.env['repair.order'].search(
                    [('quality_check_id', '=', rec.id)], limit=1)
                if scrap_id:
                    scrap_id.lot_id = lot_id.id
                    # scrap_id.action_validate()
                if repair_order:
                    repair_order.lot_id = lot_id.id
        return res

    def _compute_repairs_count(self):
        for record in self:
            repair_order = self.env['repair.order'].search_count(
                [('quality_check_id', '=', self.id)])
            record.repairs_count = repair_order

    def action_view_repair_orders(self):
        repair_order = self.env['repair.order'].search(
            [('quality_check_id', '=', self.id)], limit=1)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Repairs'),
            'res_model': 'repair.order',
            'view_mode': 'form',
            'res_id': repair_order.id,
        }

    def _compute_mrp_count(self):
        for record in self:
            mrp_count = self.env['mrp.production'].search_count(
                [('quality_check_id', '=', record.id)])
            record.mrp_count = mrp_count

    def action_view_mrp_production(self):
        mrp_id = self.env['mrp.production'].search(
            [('quality_check_id', '=', self.id)], limit=1)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Manufacturing'),
            'res_model': 'mrp.production',
            'view_mode': 'form',
            'res_id': mrp_id.id,
        }

    def _compute_scrap_count(self):
        for record in self:
            scrap_count = self.env['stock.scrap'].search(
                [('quality_check_id', '=', record.id)], limit=1)
            record.scrap_count = scrap_count

    def action_see_move_scrap(self):
        scrap_id = self.env['stock.scrap'].search(
            [('quality_check_id', '=', self.id)], limit=1)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Scrap'),
            'res_model': 'stock.scrap',
            'view_mode': 'form',
            'res_id': scrap_id.id,
        }

    def do_pass(self):
        res = super().do_pass()
        # if self.env.context.get('is_disposition'):
        #     self._create_transfer()
        return res

    def _create_transfer(self):
        StockPicking = self.env['stock.picking']
        warehouse_id = self.picking_id.location_dest_id.warehouse_id
        location_id = self.picking_id.location_dest_id
        location_dest_id = warehouse_id.lot_stock_id
        picking_vals = {
            'picking_type_code': 'internal',
            'partner_id': self.picking_id.partner_id.id,
            'picking_type_id': warehouse_id.int_type_id.id,
            'location_id': location_id.id,
            'location_dest_id': location_dest_id.id,
            'quality_check_id': self.id,
        }
        new_picking_id = StockPicking.with_context(
            skip_sanity_check=True).create(picking_vals)
        picking_line_vals = {
            'product_id': self.product_id.id,
            'name': self.product_id.name,
            'picking_id': new_picking_id.id,
            'product_uom_qty': self.qty_line,
            'location_id': location_id.id,
            'location_dest_id': location_dest_id.id,
        }
        new_picking_id.move_ids_without_package = [(0, 0, picking_line_vals)]

    def _compute_int_transfer_count(self):
        for rec in self:
            int_transfer_count = self.env['stock.picking'].search(
                [('quality_check_id', '=', rec.id)], limit=1)
            rec.int_transfer_count = int_transfer_count

    def action_see_internal_transfer(self):
        picking_id = self.env['stock.picking'].search(
            [('quality_check_id', '=', self.id)], limit=1)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Internal Transfers'),
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': picking_id.id,
        }


class QualityPoint(models.Model):
    _inherit = 'quality.point'

    @api.constrains('test_type_id', 'measure_on')
    def check_control_per(self):
        if self.test_type in ['disposition_2', 'disposition_4'] and self.measure_on != 'move_line':
            raise UserError(_(
                "If you select a Disposition type, you must set a quantity for control per."
            ))

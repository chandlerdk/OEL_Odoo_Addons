# -*- coding: utf-8 -*-

from odoo import models, fields


class ResPartner(models.Model):
    _inherit = "res.partner"

    rma_count = fields.Integer('RMA Claims', compute='_compute_rma_count')
    delivery_count = fields.Integer(compute='_compute_delivery_count')

    def _compute_rma_count(self):
        for rec in self:
            rec.rma_count = self.env['crm.claim.ept'].search_count(
                [('partner_id', '=', rec.id)])

    def action_view_rma(self):
        rma = self.env['crm.claim.ept'].search([('partner_id', '=', self.id)])

        if len(rma) == 1:
            claim_action = {
                'name': "RMA",
                'view_mode': 'form',
                'res_model': 'crm.claim.ept',
                'type': 'ir.actions.act_window',
                'res_id': rma.id,
                'context': {
                    'create': False
                }
            }
        else:
            claim_action = {
                'name': "RMA",
                'view_mode': 'tree,form',
                'res_model': 'crm.claim.ept',
                'type': 'ir.actions.act_window',
                'domain': [('id', 'in', rma.ids)],
                'context': {
                    'create': False
                }
            }

        return claim_action

    def _compute_delivery_count(self):
        for rec in self:
            rec.delivery_count = self.env['stock.picking'].search_count(
                [('partner_id', 'child_of', rec.id)])

    def show_picking(self):
        picking_ids = self.env['stock.picking'].search(
            [('partner_id', 'child_of', self.id)])
        if len(picking_ids) == 1:
            picking_action = {
                'name': "Delivery",
                'view_mode': 'form',
                'res_model': 'stock.picking',
                'type': 'ir.actions.act_window',
                'res_id': picking_ids.id
            }
        else:
            picking_action = {
                'name': "Deliveries",
                'view_mode': 'tree,form',
                'res_model': 'stock.picking',
                'type': 'ir.actions.act_window',
                'domain': [('id', 'in', picking_ids.ids)]
            }
        return picking_action

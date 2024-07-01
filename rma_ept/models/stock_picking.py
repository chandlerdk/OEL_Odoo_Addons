# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    claim_count_out = fields.Integer(
        compute='_compute_claim_count_out', string="Claim Count")
    claim_id = fields.Many2one('crm.claim.ept', string="RMA Claim", copy=False)
    rma_sale_id = fields.Many2one(
        'sale.order', string="Rma Sale Order", copy=False)
    repair_order_id = fields.Many2one(
        'repair.order', string="Repair Order", copy=False)
    view_claim_button = fields.Boolean(compute='_compute_view_claim_button')

    def _compute_claim_count_out(self):
        """
        This method used to display the number of a claim for related picking.
        """
        for record in self:
            record.claim_count_out = self.env['crm.claim.ept'].search_count(
                [('picking_id', '=', record.id)])

    def _compute_view_claim_button(self):
        """
        This method used to display a claim button on the picking based on the picking stage.
        """
        for record in self:
            record.view_claim_button = False
            if record.sale_id and record.state == 'done' and \
                    record.picking_type_code in ('outgoing'):
                record.view_claim_button = True

    def write(self, vals):
        """
        This methos is used to write state of related claim.
        """
        for record in self.filtered(lambda l: l.state == 'done' and
                                    l.picking_type_code == 'incoming' and l.claim_id and
                                    l.claim_id.state == 'approve'):
            record.claim_id.write({'state': 'process'})
        return super().write(vals)

    # def write(self, vals):
    #     """
    #     This methos is used to write state of related claim.
    #     """
    #     for record in self.filtered(lambda l: l.state == 'done' and l.claim_id and
    #                                 l.claim_id.state == 'approve'):
    #         simple_pickings = self.search_count([('claim_id', '=', record.claim_id.id)])
    #         done_pickings = self.search_count([('claim_id', '=', record.claim_id.id), ('state', '=', 'done')])
    #         if simple_pickings == done_pickings:
    #             record.claim_id.write({'state': 'process'})
    #     return super().write(vals)

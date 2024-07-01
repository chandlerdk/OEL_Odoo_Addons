# -*- coding: utf-8 -*-

from odoo import models, fields


class RepairOrder(models.Model):
    _inherit = "repair.order"

    quality_check_id = fields.Many2one('quality.check', string="Quality Check")

    def _prepare_procurement_values(self, group_id):
        vals = super()._prepare_procurement_values(group_id)
        if self.claim_id.is_legacy_order:
            vals['warehouse_id'] = self.claim_id.location_id.warehouse_id
        return vals

    def _prepare_procurement_group_vals(self):
        vals = super()._prepare_procurement_group_vals()
        if self.claim_id.is_legacy_order:
            vals['move_type'] = 'direct'
        return vals

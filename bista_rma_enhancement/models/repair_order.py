# -*- coding: utf-8 -*-

from odoo import models, fields


class RepairOrder(models.Model):
    _inherit = "repair.order"

    quality_check_id = fields.Many2one('quality.check', string="Quality Check")

    def _prepare_procurement_values(self, reference):
        vals = super()._prepare_procurement_values(reference)
        if self.claim_id.is_legacy_order:
            vals['warehouse_id'] = self.claim_id.location_id.warehouse_id
        return vals

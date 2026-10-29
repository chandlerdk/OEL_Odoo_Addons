# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class StockProductionLot(models.Model):
    _inherit = "stock.lot"

    claim_line_id = fields.Many2one('claim.line.ept', string="Claim line")

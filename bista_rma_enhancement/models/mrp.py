# -*- coding: utf-8 -*-

from odoo import fields, models


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    quality_check_id = fields.Many2one('quality.check', string="Quality Check")

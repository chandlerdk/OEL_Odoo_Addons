# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

from odoo import models, fields


class StockWarehouse(models.Model):
    _inherit = 'stock.warehouse'

    return_partner_id = fields.Many2one('res.partner', "Return Address")

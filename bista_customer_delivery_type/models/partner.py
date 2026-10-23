# -*- coding: utf-8 -*-
from odoo import models, fields, api, _

class ResPartner(models.Model):
    _inherit = 'res.partner'

    delivery_id = fields.Many2one('stock.picking.type', string='Delivery Type')


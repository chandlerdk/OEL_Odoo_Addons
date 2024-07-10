# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class ShipstationOrder(models.Model):
    _name = 'shipstation.store'
    _rec_name = 'store_name'

    store_id = fields.Char(string='Store Id')
    store_name = fields.Char(string='Store Name')
    marketplace_id = fields.Char(string='MarkerPlace ID')
    marketplace_name = fields.Char(string='MarkerPlace Name')
    acc_number = fields.Char(string='Account Number')
    company_id = fields.Many2one('res.company')

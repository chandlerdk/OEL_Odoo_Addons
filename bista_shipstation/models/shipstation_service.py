# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class ShipStationService(models.Model):
    _name = 'shipstation.service'
    _description = 'Shipstation Service'
    _rec_name = 'service'

    name = fields.Char('Service Level Code', index=True)
    service = fields.Char('Service')
    shipstation_carrier = fields.Char('Carrier Prefix', index=True)

# -*- encoding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (http://www.bistasolutions.com)
#
##############################################################################

from odoo import api, Command, fields, models, SUPERUSER_ID, _
class StockPickingType(models.Model):
    _inherit = 'stock.picking.type'


    delivery_generate = fields.Boolean(string="Delivery Generate")

    delivery_generate_id = fields.Many2one('stock.picking.type',string="Delivery Operation Type")
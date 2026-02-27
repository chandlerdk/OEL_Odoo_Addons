from odoo import api, fields, models

class StockLocation(models.Model):
    _inherit = 'stock.location'

    water_location = fields.Boolean(string='On the water?', required=False,readonly=False)
    water_delay = fields.Integer(string='Expected days on the water',default=1, required=False)

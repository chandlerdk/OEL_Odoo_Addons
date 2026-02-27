from odoo import api, fields, models

class DeliveryCarrier(models.Model):
    _inherit = 'delivery.carrier'

    scac = fields.Char(string='SCAC', required=False,readonly=False)
    carrier_account = fields.Char(string='Carrier Account', required=False,readonly=False)

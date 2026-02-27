from odoo import api, fields, models

class ProductProduct(models.Model):
    _inherit = 'product.product'

    req_carrier = fields.Many2one(string='Required Delivery Carrier', comodel_name='delivery.carrier', required=False)

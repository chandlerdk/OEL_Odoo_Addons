from odoo import fields, models


class ProductPackaging(models.Model):
    _inherit = 'stock.package.type'

    height = fields.Float(string="Height")
    width = fields.Float(string="Width")
    packaging_length = fields.Float(string="Length")

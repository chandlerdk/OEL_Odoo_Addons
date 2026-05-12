from odoo import models, fields

class StockPickingTag(models.Model):
    _name = 'stock.picking.tag'
    _description = 'Stock Picking Tag'

    name = fields.Char(string='Tag Name', required=True)
    color = fields.Integer(string='Color Index', default=0)
    active = fields.Boolean(default=True)

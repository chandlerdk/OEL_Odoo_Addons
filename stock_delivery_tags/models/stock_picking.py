from odoo import models, fields

class StockPicking(models.Model):
    _inherit = 'stock.picking'

    tag_ids = fields.Many2many(
        comodel_name='stock.picking.tag',
        relation='stock_picking_tag_rel',
        column1='picking_id',
        column2='tag_id',
        string='Tags',
    )

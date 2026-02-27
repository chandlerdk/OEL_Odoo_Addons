from odoo import api, fields, models

class StockWarehouse(models.Model):
    _inherit = 'stock.warehouse'

    contract_warehouse = fields.Boolean(string='Contract Warehouse?', required=False,readonly=False)
    inv_feed_intrans_daysdelay = fields.Integer(string='In-Transit Standard Delay',default=1, required=False)

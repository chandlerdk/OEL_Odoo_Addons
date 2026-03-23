from odoo import api, fields, models

class WarehouseTPCodes(models.Model):
    _name = 'bsiedi.warehousetpcodes'
    _description = 'Warehouse Codes by Trading Partner'
    _rec_name = "name"

    name = fields.Char(string='Sequence', default='WTPC00000',copy=False,required=True,readonly=True)
    active = fields.Boolean(string='Active',default=True)

    trading_partner = fields.Many2one(string='Trading Partner', comodel_name='bsiedi.tradingpartner', required=False)
    warehouse_id = fields.Many2one(string='Warehouse', comodel_name='stock.warehouse', required=False)
    warehouse_code = fields.Char(string='Warehouse Code', required=True)

    @api.model_create_multi
    def create(self,vals_list):
    	for vals in vals_list:
    		if vals.get('name', ('WTPC00000'))==('WTPC00000'):
    			vals['name'] = self.env['ir.sequence'].next_by_code('warehousetpcodes.number')
    	return super().create(vals)    


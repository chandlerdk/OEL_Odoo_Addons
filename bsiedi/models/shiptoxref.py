from odoo import api, fields, models

class ShipToXRef(models.Model):
    _name = 'bsiedi.shiptoxref'
    _description = 'Ship-to Cross-Reference'

    name = fields.Char(string='Sequence', default='STX0000000',copy=False,required=True,readonly=True)
    active = fields.Boolean(string='Active',default=True)

    trading_partner = fields.Many2one(string='Trading Partner', comodel_name='bsiedi.tradingpartner', required=True)
    ship_to_no = fields.Char(string='EDI Ship-to Number', required=True)
    partner_id = fields.Many2one(string='Sell-To Partner', comodel_name='res.partner', required=True)
    bill_partner_id = fields.Many2one(string='Billing Partner', comodel_name='res.partner', required=True)
    zip_code = fields.Char(string='ZIP Code', required=False)

    
    @api.model_create_multi
    def create(self,vals_list):
    	for vals in vals_list:
    		if vals.get('name', ('STX0000000'))==('STX0000000'):
    			vals['name'] = self.env['ir.sequence'].next_by_code('shiptoxref.number')
    	return super().create(vals)    


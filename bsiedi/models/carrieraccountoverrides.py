from odoo import api, fields, models

class CarrierAccountOverrides(models.Model):
    _name = 'bsiedi.carrieraccountoverrides'
    _description = 'Carrier Account Overrides by Trading Partner'
    _rec_name = "name"

    name = fields.Char(string='Sequence', default='CAO00000',copy=False,required=True,readonly=True)
    active = fields.Boolean(string='Active',default=True)

    trading_partner = fields.Many2one(string='Trading Partner', comodel_name='bsiedi.tradingpartner', required=True)
    delivery_carrier = fields.Many2one(string='Delivery Carrier', comodel_name='delivery.carrier', required=True)
    carrier_account = fields.Char(string='Carrier Account', required=False,readonly=False)

    @api.model_create_multi
    def create(self,vals_list):
    	for vals in vals_list:
    		if vals.get('name', ('CAO00000'))==('CAO00000'):
    			vals['name'] = self.env['ir.sequence'].next_by_code('carrieraccountoverrides.number')
    	return super().create(vals)    


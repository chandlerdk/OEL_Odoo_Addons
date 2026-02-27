from odoo import api, fields, models

class InbShipViaXRef(models.Model):
    _name = 'bsiedi.inbshipviaxref'
    _description = 'Inbound Ship-Via Cross-Reference'

    name = fields.Char(string='Sequence', default='ISV0000000',copy=False,required=True,readonly=True)
    active = fields.Boolean(string='Active',default=True)

    ship_descr = fields.Char(string='Description', required=True)
    delivery_carrier = fields.Many2one(string='Delivery Carrier', comodel_name='delivery.carrier', required=False)
    ship_via_cd = fields.Many2one(string='Ship Via Product', comodel_name='product.product', required=True)
    partner_id = fields.Many2one(string='Partner', comodel_name='res.partner', required=False)
    trading_partner_id = fields.Many2one(string='Trading Partner', comodel_name='bsiedi.tradingpartner', required=False)

    scac = fields.Char(string='SCAC', required=False)
    trans_meth_code = fields.Char(string='Transportation Method Code', required=False)
    service_level_code = fields.Char(string='Service Level Code', required=False)
    ship_priority = fields.Char(string='Ship Priority Code', required=False)
    carrier_routing = fields.Char(string='Carrier Routing Code', required=False)

    @api.model_create_multi
    def create(self,vals_list):
    	for vals in vals_list:
    		if vals.get('name', ('ISV0000000'))==('ISV0000000'):
    			vals['name'] = self.env['ir.sequence'].next_by_code('inbshipviaxref.number')
    	return super().create(vals)    

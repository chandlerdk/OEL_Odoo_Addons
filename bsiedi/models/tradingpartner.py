from odoo import api, fields, models

class TradingPartner(models.Model):
    _name = 'bsiedi.tradingpartner'
    _description = 'Trading Partner'
    _rec_name = "trading_partner_name"

    name = fields.Char(string='Sequence', default='TP00000',copy=False,required=True,readonly=True)
    active = fields.Boolean(string='Active',default=True)

    trading_partner_id = fields.Char(string='Trading Partner ID', required=True)
    trading_partner_name = fields.Char(string='Trading Partner Name', required=True)
    requires_compliant_labels = fields.Boolean(string='Requires Compliant Labels?', required=False,readonly=False)
    requires_edi_asn = fields.Boolean(string='Requires Outbound ASN?', required=False,readonly=False)
    requires_edi_inv = fields.Boolean(string='Requires Outbound Invoice?', required=False,readonly=False)
    requires_edi_ack = fields.Boolean(string='Requires Outbound Acknowledgement?', required=False,readonly=False)
    requires_edi_invstat = fields.Boolean(string='Requires Outbound Inventory Status?', required=False,readonly=False)
    requires_edi_po = fields.Boolean(string='Requires Outbound Purchase Order?', required=False,readonly=False)
    requires_edi_cmemo = fields.Boolean(string='Requires Outbound Credit Memo?', required=False,readonly=False)
    requires_edi_routereq = fields.Boolean(string='Requires Outbound Routing Request?', required=False,readonly=False)
    requires_edi_whsord = fields.Boolean(string='Requires Warehouse Shipment Request?', required=False,readonly=False)
    inv_feed_locs = fields.Many2many('stock.location',string='Stock Locations for EDI Inventory Feed')
    inv_feed_intransit_locs = fields.Many2many('stock.location',string='Stock In-Transit Locations for EDI Inventory Feed',relation='invfeed_intran_warehouse')
    whsord_locs = fields.Many2many('stock.warehouse',string='Stock Warehouses for Warehouse Orders')
    whsord_locs_autosend = fields.Many2many('stock.warehouse',string='Stock Warehouses for Warehouse Orders (Auto-send)',relation='whsord_loc_as_warehouse')

    @api.model_create_multi
    def create(self,vals_list):
    	for vals in vals_list:
    		if vals.get('name', ('TP00000'))==('TP00000'):
    			vals['name'] = self.env['ir.sequence'].next_by_code('tradingpartner.number')
    	return super().create(vals)    


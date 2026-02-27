from odoo import api, fields, models

class ItemXRef(models.Model):
    _name = 'bsiedi.itemxref'
    _description = 'Item/Product Cross-Reference'

    name = fields.Char(string='Sequence', default='IX0000000',copy=False,required=True,readonly=True)
    active = fields.Boolean(string='Active',default=True)

    trading_partner = fields.Many2one(string='Trading Partner', comodel_name='bsiedi.tradingpartner', required=False)
    partner_id = fields.Many2one(string='Partner', comodel_name='res.partner', required=False)
    product_id = fields.Many2one(string='Product', comodel_name='product.product', required=True)
    item_no_in = fields.Char(string='External Item Number', required=True)

    inb_uom = fields.Char(string='Inbound Unit of Measure', required=False)
    inb_conv_factor = fields.Float('Inbound Conversion Factor',digits=(16, 6), required=False)    
    inb_conv_type = fields.Selection(string='Inbound Conversion Type',
        selection=[('m','Multiply'),('d','Divide'),('n','No Calculation')],copy=False)
    out_uom = fields.Char(string='Outbound Unit of Measure', required=False)
    out_conv_factor = fields.Float('Outbound Conversion Factor',digits=(16, 6), required=False)    
    out_conv_type = fields.Selection(string='Outbound Conversion Type',
        selection=[('m','Multiply'),('d','Divide'),('n','No Calculation')],copy=False)
    
    buyer_style_number = fields.Char(string='Buyer Style Number', required=False)
    buyer_sku = fields.Char(string='Buyer SKU', required=False)
    gtin_12 = fields.Char(string='GTIN 12', required=False)
    gtin_14 = fields.Char(string='GTIN 14', required=False)
    upc = fields.Char(string='UPC', required=False)
    ndc = fields.Char(string='National Drug Code', required=False)
    ean = fields.Char(string='EAN', required=False)
    isbn = fields.Char(string='ISBN', required=False)

    inner_pack_quantity = fields.Integer(string='Inner Pack Quantity',default=1, required=False)
    inners_per_outer = fields.Integer(string='Inners per Outer',default=0, required=False)
    cartons_per_pallet = fields.Integer(string='Cartons per Pallet',default=0, required=False)
    layers_per_pallet = fields.Integer(string='Layers per Pallet',default=0, required=False)

    pack = fields.Integer(string='Pack', required=False)
    pack_size = fields.Integer(string='Pack Size', required=False)
    pack_uom = fields.Char(string='Pack UOM', required=False)

    unit_volume = fields.Float('Unit Volume',digits=(16, 6), required=False)    
    volume_uom = fields.Char(string='Volume UOM', required=False)

    color_code = fields.Char(string='Color Code', required=False)
    size_code = fields.Char(string='Size Code', required=False)
    config_code = fields.Char(string='Configuration Code', required=False)

    extra_id_1 = fields.Char(string='Extra ID 1', required=False)
    extra_id_2 = fields.Char(string='Extra ID 2', required=False)
    extra_id_3 = fields.Char(string='Extra ID 3', required=False)

    effective_date = fields.Date('Effective Date', required=False)
    expiration_date = fields.Date('Expiration Date', required=False)

    inb_price_conv = fields.Float('Inbound Price Conversion Factor',digits=(16, 6), required=False)    
    out_price_conv = fields.Float('Outbound Price Conversion Factor',digits=(16, 6), required=False)    

    last_release_number = fields.Char(string='Last Release Number', required=False)
    purchase_order_number = fields.Char(string='Purchase Order Number', required=False)
    pkg_item_no = fields.Char(string='Packaging Item Number', required=False)
    plant_code = fields.Char(string='Plant Code', required=False)
    model_year = fields.Char(string='Model Year', required=False)
    last_cume_qty = fields.Integer('Last Cume Quantity',required=False)    

    suppress_on_feed = fields.Boolean(string='Suppress',default=False)
    force_to_zero = fields.Boolean(string='Force to Zero',default=False)
    min_qty_report = fields.Integer('Minimum Quantity to Report',required=False)    
    max_qty_report = fields.Integer('Maximum Quantity to Report',required=False)    
    obsolete_flag = fields.Boolean(string='Report as Obsolete',default=False)
    
    @api.model_create_multi
    def create(self,vals_list):
    	for vals in vals_list:
    		if vals.get('name', ('IX0000000'))==('IX0000000'):
    			vals['name'] = self.env['ir.sequence'].next_by_code('itemxref.number')
    	return super().create(vals)    


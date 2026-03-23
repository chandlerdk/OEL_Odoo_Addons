from odoo import api, fields, models

class ReleaseApproval(models.Model):
    _name = 'bsiedi.releaseapproval'
    _description = 'Release Approval'
    _rec_name = "name"

    name = fields.Char(string='Sequence', default='RA00000000',copy=False,required=True,readonly=True)
    active = fields.Boolean(string='Active',default=True)

    trading_partner_id = fields.Many2one(string='Trading Partner', comodel_name='bsiedi.tradingpartner', required=True)
    partner_id = fields.Many2one(string='Partner', comodel_name='res.partner', required=False)
    deliv_partner_id = fields.Many2one(string='Delivery Partner', comodel_name='res.partner', required=False)
    product_id = fields.Many2one(string='Product', comodel_name='product.product', required=True)

    ready = fields.Boolean(string='Ready',default=False)

    po_number = fields.Char(string='Purchase Order Number', required=False)
    release_number = fields.Char(string='Release Number', required=False)
    kanban_number = fields.Char(string='Kanban Number', required=False)

    isa_number = fields.Char(string='EDI ISA Control Number', required=False)
    gs_number = fields.Char(string='EDI GS Control Number', required=False)
    st_number = fields.Char(string='EDI ST Control Number', required=False)
    trx_date = fields.Date('EDI Transaction Date', required=False)
    
    release_quantity = fields.Float('Release Quantity',digits=(16, 6), required=False)    
    disposition = fields.Selection(string='Disposition?',
        selection=[('N','None'),('P','Process'),('I','Ignore')],copy=False)
    release_date = fields.Date('Release Date', required=False)
    state = fields.Selection(string='State?',
        selection=[('U','Unprocessed'),('P','Processing Selected'),('D','Done')],copy=False)

    def action_process(self):
        for releaseapproval in self:
            if releaseapproval.disposition != "N":
                releaseapproval.write({'state': "P"})
        return
    
    @api.model_create_multi
    def create(self,vals_list):
        for vals in vals_list:
            if vals.get('name', ('RA00000000'))==('RA00000000'):
                vals['name'] = self.env['ir.sequence'].next_by_code('releaseapproval.number')
        return super().create(vals_list)    


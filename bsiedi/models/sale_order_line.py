from odoo import api, fields, models

class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    orig_qty = fields.Float('Original Quantity from EDI',digits=(16, 6), required=False,readonly=True)
    orig_uom = fields.Char(string='Original UOM from EDI', required=False,readonly=True)
    orig_ship_date = fields.Date(string='Original Ship Date from EDI', required=False,readonly=True)
    cus_item_no = fields.Char(string='Customer Item Number', required=False,readonly=False)
    upc_code = fields.Char(string='UPC Code', required=False,readonly=False)
    gtin_14 = fields.Char(string='GTIN 14', required=False,readonly=False)
    edi_line_reject = fields.Boolean(string='Reject?', required=False)
    resched_ship_date = fields.Date(string='Rescheduled Ship Date', required=False,readonly=False)
    mark_for_partner_id = fields.Many2one(string='Mark-For Partner', comodel_name='res.partner', required=False)

    kanban = fields.Char(string='EDI Kanban', required=False,readonly=False)
    line_po = fields.Char(string='EDI Purchase Order', required=False,readonly=False)
    line_feed_loc = fields.Char(string='EDI Line Feed Location', required=False,readonly=False)

#    @api.depends('order_partner_id')
#    def _compute_edi_default_hidden(self):
#        tpid = self.pulltp()
#        if tpid is None:
#            self.edi_default_hidden="hide"
#        else:
#            self.edi_default_hidden="show"

#    @api.depends('order_partner_id')
#    def _compute_hide_edi_info(self):
#        tpid = self.pulltp()
#        if tpid is None:
#            self.hide_edi_info=True
#        else:
#            self.hide_edi_info=False
   

    def pulltp(self):
        if self.order_partner_id.tp_id.id != 0:
            return self.order_partner_id.tp_id
        else:
            if self.order_partner_id.parent_id.id != 0:
                return self.order_partner_id.parent_id.tp_id
            

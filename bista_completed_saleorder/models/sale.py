from odoo import api, models, fields, _



class SaleOrder(models.Model):
    _inherit = 'sale.order'

    complete_order = fields.Boolean(string="Completed Orders",compute='compute_order')
    flag = fields.Boolean(default=False)

    @api.depends('order_line.product_uom_qty','order_line.qty_delivered','order_line.qty_invoiced')
    def compute_order(self):
        for rec in self:
            if all([order.product_uom_qty == order.qty_delivered == order.qty_invoiced for order in rec.order_line.filtered(lambda p: p.product_id.detailed_type != 'service')]) == True:
                rec.flag = True
                rec.complete_order = True
            else:
                rec.flag = False
                rec.complete_order = False










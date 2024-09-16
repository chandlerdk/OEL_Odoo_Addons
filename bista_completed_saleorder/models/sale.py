from odoo import api, models, fields, _



class SaleOrder(models.Model):
    _inherit = 'sale.order'

    complete_order = fields.Boolean(string="Completed Orders", compute='compute_order_line', store=True)

    def compute_order_line(self):
        if self.order_line:
            for rec in self.order_line:
                if rec.product_uom_qty == rec.qty_delivered == rec.qty_invoiced:
                    self.complete_order = True
                else:
                    self.complete_order = False








from odoo import models, fields, api


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.constrains('partner_id')
    def add_sale_auto_follower(self):
        for order in self:
            order.message_subscribe(partner_ids=[order.partner_id.id])
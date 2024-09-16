# -*- coding: utf-8 -*-
from odoo import models, fields, api, _

class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    delivery_id = fields.Many2one('stock.picking.type', string='Delivery Type')


    @api.onchange('partner_id')
    def get_parnter(self):
        if self.partner_id:
            self.delivery_id = self.partner_id.delivery_id
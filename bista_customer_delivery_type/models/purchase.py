# -*- coding: utf-8 -*-
from odoo import models, fields, api, _

class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'


    @api.onchange('partner_id')
    def get_parnter(self):
        if self.partner_id:
            self.picking_type_id = self.partner_id.delivery_id
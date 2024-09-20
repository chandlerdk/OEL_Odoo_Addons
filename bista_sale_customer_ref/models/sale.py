# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.constrains('partner_id', 'client_order_ref')
    def _clienr_order_ref_check(self):
        if self.partner_id and self.client_order_ref:
            rec_id = self.env['sale.order'].search([('partner_id','=',self.partner_id.id),('client_order_ref','=',self.client_order_ref)])
            if len(rec_id) > 1:
                raise UserError('Same customer reference with same partner already exists!')

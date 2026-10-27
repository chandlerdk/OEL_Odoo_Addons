# -*- coding: utf-8 -*-
from odoo import _, api, models
from odoo.exceptions import ValidationError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.constrains('partner_id', 'client_order_ref')
    def _clienr_order_ref_check(self):
        for order in self.filtered(lambda o: o.partner_id and o.client_order_ref):
            if self.search_count([
                ('id', '!=', order.id),
                ('partner_id', '=', order.partner_id.id),
                ('client_order_ref', '=', order.client_order_ref),
            ], limit=1):
                raise ValidationError(_('Same customer reference with same partner already exists!'))

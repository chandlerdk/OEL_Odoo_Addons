# -*- coding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (https://www.bistasolutions.com)
#
##############################################################################

from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    sale_rep_id = fields.Many2one('res.partner', domain=[('is_sale_rep', '=', True)],tracking=True)

    @api.onchange("partner_id")
    def _get_sale_rep_id(self):
        for order in self:
            if order.partner_id and order.partner_id.sale_rep_id:
                order.sale_rep_id = order.partner_id.sale_rep_id

    @api.model
    def create(self, vals):
        ret = super().create(vals)
        if not ret.sale_rep_id:
            ret._get_sale_rep_id()
        return ret

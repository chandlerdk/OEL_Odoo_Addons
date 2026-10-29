# -*- coding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (http://www.bistasolutions.com)
#
##############################################################################

from odoo import fields, models, api

class SaleReport(models.Model):
    _inherit = "sale.report"

    sale_rep_id = fields.Many2one('res.partner', string="Sales Rep", domain=[('is_sale_rep', '=', True)])

    def _select_dict(self, table):
        return super()._select_dict(table) | {'sale_rep_id': table.order_id.sale_rep_id}

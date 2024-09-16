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


    def _select_sale(self):
        select_ = super()._select_sale() + """
            ,
            s.sale_rep_id AS sale_rep_id
        """
        return select_

    def _group_by_sale(self):
        group_by = super()._group_by_sale() + """
            ,
            s.sale_rep_id
        """
        return group_by


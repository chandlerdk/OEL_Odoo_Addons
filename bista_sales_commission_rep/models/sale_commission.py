# -*- coding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (https://www.bistasolutions.com)
#
##############################################################################

from odoo import api, fields, models


class SaleCommission(models.Model):
    _inherit = 'sale.commission'
    _description = 'Sale Commission'

    sale_partner_type = fields.Selection(selection_add=[('sale_rep', 'Sales Representative')],
                                         ondelete={'sale_rep': 'cascade'})
    sale_rep_id = fields.Many2one('res.partner',
                                  domain=[('is_sale_rep', '=', True)])

    commission_sequence = fields.Integer(string="Sequence")

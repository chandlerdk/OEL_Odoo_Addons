# -*- coding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (https://www.bistasolutions.com)
#
##############################################################################

from odoo import _, api, fields, models
from odoo.fields import Domain


class ResPartner(models.Model):
    _inherit = 'res.partner'

    partner_reference_no = fields.Char(string='Partner Reference', copy=False, required=True,
                                       readonly=False, default=lambda self: _('New'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('partner_reference_no', _('New')) == _('New'):
                vals['partner_reference_no'] = self.env['ir.sequence'].next_by_code('res.partner') or _('New')
        return super().create(vals_list)

    @api.model
    def _search_display_name(self, operator, value):
        domain = super()._search_display_name(operator, value)
        if value and operator in ('ilike', 'like', '=', '=like', '=ilike'):
            domain = Domain.OR([domain, [('partner_reference_no', operator, value)]])
        return domain

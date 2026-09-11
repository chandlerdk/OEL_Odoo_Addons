# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    ship_via = fields.Char(string='Ship Via')
    fob = fields.Many2one(
        'purchase.fob',
        string='FOB',
        help='FOB numbers are managed under Purchase > Configuration > FOB Numbers. '
             'Favorites and lower sequence values appear first.',
    )

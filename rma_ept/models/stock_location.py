# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class StockLocation(models.Model):
    _inherit = 'stock.location'

    return_location = fields.Boolean(
        string='Is a Return Location?',
        help='Check this box to allow using this location as a return location.',
    )

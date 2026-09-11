# -*- encoding: utf-8 -*-
##############################################################################
#
#    Bista Solutions Pvt. Ltd
#    Copyright (C) 2023 (http://www.bistasolutions.com)
#
##############################################################################

import ast
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    fob_ids = fields.Many2many(
        'purchase.fob',
        string='FOB',
        help='Favorite FOB numbers appear first in the FOB dropdown on purchase orders. '
             'You can also manage favorites from Purchase > Configuration > FOB Numbers.',
    )

    @api.model
    def get_values(self):
        res = super().get_values()
        # Prefer model-level favorites; fall back to legacy config parameter once.
        favorite_fobs = self.env['purchase.fob'].search([('is_favorite', '=', True)])
        if favorite_fobs:
            res['fob_ids'] = [(6, 0, favorite_fobs.ids)]
        else:
            fob_value = self.env['ir.config_parameter'].sudo().get_param('purchase.fob_ids')
            if fob_value:
                try:
                    fob_ids = ast.literal_eval(fob_value)
                    if isinstance(fob_ids, list):
                        existing = self.env['purchase.fob'].browse(fob_ids).exists()
                        res['fob_ids'] = [(6, 0, existing.ids)]
                except (ValueError, SyntaxError):
                    _logger.warning('Could not parse purchase.fob_ids: %s', fob_value)
        return res

    def set_values(self):
        super().set_values()
        PurchaseFob = self.env['purchase.fob']
        selected = self.fob_ids
        # Sync favorites onto the FOB master records (source of truth for PO ordering).
        (PurchaseFob.search([('is_favorite', '=', True)]) - selected).write({'is_favorite': False})
        selected.write({'is_favorite': True})
        # Keep legacy parameter in sync for older references.
        self.env['ir.config_parameter'].sudo().set_param(
            'purchase.fob_ids', selected.ids
        )

# -*- coding: utf-8 -*-
from odoo import models, fields


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    google_places_api = fields.Char(related="company_id.google_places_api", readonly=False)
    autocomplete_addresses = fields.Boolean(related="company_id.autocomplete_addresses", readonly=False)

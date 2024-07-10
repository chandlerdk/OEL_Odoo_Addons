# -*- coding: utf-8 -*-
from odoo import models, fields


class Company(models.Model):
    _inherit = 'res.company'

    google_places_api = fields.Char()
    autocomplete_addresses = fields.Boolean()

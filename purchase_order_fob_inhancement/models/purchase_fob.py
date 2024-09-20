# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, models, fields


class PurchaseFob(models.Model):
    _name = 'purchase.fob'

    name = fields.Char(string="Name")

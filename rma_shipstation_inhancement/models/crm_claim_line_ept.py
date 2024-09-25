# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class CRMClaimLine(models.Model):
    _inherit = 'claim.line.ept'


    delivery_cost = fields.Float(string="Delivery Cost")
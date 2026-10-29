# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    claim_id = fields.Many2one('crm.claim.ept', string='Claim')

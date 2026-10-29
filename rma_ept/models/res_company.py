# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class Company(models.Model):
    _inherit = "res.company"

    rma_template = fields.Boolean("RMA Templates")
    rma_template_id = fields.Many2one('mail.template', domain=[('model', '=', 'crm.claim.ept')],
                                      help="This email template will send mail RMA notification email")

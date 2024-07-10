# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models, api


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    rma_template = fields.Boolean("RMA Templates")
    rma_template_id = fields.Many2one('mail.template', domain=[('model', '=', 'crm.claim.ept')],
                                      help="This email template will send mail RMA notification email")

    def execute(self):
        """
         use: this method is used to set RMA template into the company
         @param self.
         @return: res
         """
        res = super(ResConfigSettings, self).execute()
        company_id = self.env.user.company_id
        company_id.write({'rma_template_id': self.rma_template_id.id if self.rma_template_id.id else False,
                          'rma_template': self.rma_template})
        return res

    @api.model
    def default_get(self, fields):
        """
         use: this method is used to set template and configured credit
         from the company.
         @param self, fields.
         @return: res(dict)
         @author: @TwinkalC on dated 28-nov-2019
         """
        res = super(ResConfigSettings, self).default_get(fields)
        company_id = self.env.user.company_id
        res.update({'rma_template_id': company_id.rma_template_id.id if company_id.rma_template_id else False,
                    'rma_template': company_id.rma_template})
        return res

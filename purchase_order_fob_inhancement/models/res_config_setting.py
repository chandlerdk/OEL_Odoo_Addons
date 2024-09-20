# -*- encoding: utf-8 -*-
##############################################################################
#
#    Bista Solutions Pvt. Ltd
#    Copyright (C) 2023 (http://www.bistasolutions.com)
#
##############################################################################

from odoo import fields, models,api


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    fob_ids = fields.Many2many('purchase.fob',string="FOB")

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        fob_value = self.env['ir.config_parameter'].get_param('purchase.fob_ids')
        if fob_value:
            res['fob_ids'] = [(6, 0, eval(fob_value))]
        return res

    def set_values(self):
        super(ResConfigSettings, self).set_values()
        self.env['ir.config_parameter'].set_param(
            'purchase.fob_ids', self.fob_ids.ids
        )
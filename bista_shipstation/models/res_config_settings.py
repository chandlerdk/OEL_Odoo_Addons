# -*- coding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (http://www.bistasolutions.com)
#
##############################################################################

from odoo import api, fields, models, _


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    order_confirm_hour = fields.Char(related="company_id.order_confirm_hour",
                                     readonly=False)


class Company(models.Model):
    _inherit = 'res.company'

    order_confirm_hour = fields.Char(string="Order Confirm Hour")

# -*- coding: utf-8 -*-
##############################################################################
#
# Cron QuoTech
# Copyright (C) 2021 (https://cronquotech.odoo.com)
#
##############################################################################

from odoo import fields, models


class IrModel(models.Model):
    _inherit = 'ir.model'

    disable_create_edit = fields.Boolean(
        string='Disable Create & Edit')

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
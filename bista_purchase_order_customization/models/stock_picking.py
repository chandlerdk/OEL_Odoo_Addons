# -*- encoding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (http://www.bistasolutions.com)
#
##############################################################################

from odoo import api, Command, fields, models, SUPERUSER_ID, _
from odoo.exceptions import UserError,ValidationError


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def button_validate(self):
        for picking in self:
            if picking.picking_type_id.code == 'incoming':
                related_purchase_orders = self.env['purchase.order'].search([
                    ('name', '=', picking.origin),
                    ('delivery_generate', '=', True)
                ])
                for po in related_purchase_orders:
                    if not po._check_delivery_done():
                        raise ValidationError(_("You cannot validate this receipt until the related delivery is done."))
        return super(StockPicking, self).button_validate()




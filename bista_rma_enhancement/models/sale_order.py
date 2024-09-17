# -*- coding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (https://www.bistasolutions.com)
#
##############################################################################
import re

from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    delivery_note = fields.Html('Delivery Note')
    internal_delivery_note = fields.Html('Internal Delivery Note')

    def action_confirm(self):
        res = super(SaleOrder, self).action_confirm()
        for rec in self:
            if rec.picking_ids:
                rec.picking_ids.note = rec.delivery_note
                rec.picking_ids.internal_delivery_note = rec.internal_delivery_note
        return res
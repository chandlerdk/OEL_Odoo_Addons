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

    def action_confirm(self):
        res = super(SaleOrder, self).action_confirm()
        for rec in self:
            # if rec.picking_ids:
            #     rec.picking_ids.move_ids.delivery_note = rec.note
            plain_text_note = re.sub(r'<[^>]*?>', '', rec.note or '')
            if rec.picking_ids:
                rec.picking_ids.move_ids.delivery_note = plain_text_note
        return res
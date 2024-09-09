# -*- coding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (https://www.bistasolutions.com)
#
##############################################################################

from odoo import api, fields, models
class AccountMove(models.Model):
    _inherit = 'account.move'

    def get_tracking_references(self):
        tracking_references = []
        print("innnnnntaracccccc")
        for record in self:
            sale_order = self.env['sale.order'].search([('name', '=', record.invoice_origin)], limit=1)
            print("saleorderrrrr",sale_order)
            if sale_order:
                pickings = self.env['stock.picking'].search([('sale_id', '=', sale_order.id)])
                print("pickingsgssss",pickings)
                for picking in pickings:
                    tracking_references.append(', '.join(picking.mapped('carrier_tracking_ref')))
                    print("tracking_treeeeeeee",tracking_references)
        print(">>>>>>>>>>",tracking_references)
        return ', '.join(tracking_references) if tracking_references else 'N/A'
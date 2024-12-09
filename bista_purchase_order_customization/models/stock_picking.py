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
                    for line in po.order_line:
                        delivered_qty = sum(
                            line.move_ids.filtered(
                                lambda m: m.state == 'done' and m.picking_id.picking_type_id.code == 'outgoing'
                            ).mapped('product_uom_qty')
                        )
                        received_qty = sum(
                            line.move_ids.filtered(
                                lambda m: m.product_id == line.product_id and m.picking_id.picking_type_id.code == 'incoming'
                            ).mapped('quantity')
                        )
                        if received_qty > delivered_qty:
                            raise ValidationError(_(
                                "You cannot validate this receipt until the related delivery is done. %s.\n"
                                "Delivered Quantity: %s, Trying to Receive: %s" % (
                                    line.product_id.display_name,
                                    delivered_qty,
                                    received_qty
                                )
                            ))
        return super(StockPicking, self).button_validate()




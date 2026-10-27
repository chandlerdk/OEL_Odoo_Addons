# -*- encoding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (http://www.bistasolutions.com)
#
##############################################################################

from odoo import _, models
from odoo.exceptions import ValidationError


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def button_validate(self):
        for picking in self.filtered(lambda p: p.picking_type_id.code == 'incoming'):
            orders = picking.move_ids.purchase_line_id.order_id.filtered('delivery_generate')
            for line in orders.order_line:
                delivered_qty = sum(line.move_ids.filtered(
                    lambda m: m.state == 'done' and m.picking_type_id.code == 'outgoing'
                ).mapped('quantity'))
                received_qty = sum(line.move_ids.filtered(
                    lambda m: m.product_id == line.product_id and m.state != 'cancel'
                    and m.picking_type_id.code == 'incoming'
                ).mapped('quantity'))
                if received_qty > delivered_qty:
                    raise ValidationError(_(
                        "You cannot validate this receipt until the related delivery is done. %(product)s.\n"
                        "Delivered Quantity: %(delivered)s, Trying to Receive: %(received)s",
                        product=line.product_id.display_name,
                        delivered=delivered_qty,
                        received=received_qty,
                    ))
        return super().button_validate()

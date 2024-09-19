# -*- encoding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2024 (http://www.bistasolutions.com)
#
##############################################################################


from odoo import api, models
from odoo.tools import float_is_zero, format_date, float_round, float_compare


class StockForecasted(models.AbstractModel):
    _inherit = 'stock.forecasted_product_product'
    _description = "Stock Replenishment Report"


    def _prepare_report_line(self, quantity, move_out=None, move_in=None, replenishment_filled=True, product=False, reserved_move=False, in_transit=False, read=True):
        product = product or (move_out.product_id if move_out else move_in.product_id)
        is_late = move_out.date < move_in.date if (move_out and move_in) else False

        move_to_match_ids = self.env.context.get('move_to_match_ids') or []
        move_in_id = move_in.id if move_in else None
        move_out_id = move_out.id if move_out else None
        line = {
            'document_in': False,
            'document_out': False,
            'receipt_date': False,
            'delivery_date': False,
            'product': {
                'id': product.id,
                'display_name': product.display_name,
            },
            'replenishment_filled': replenishment_filled,
            'is_late': is_late,
            'quantity': float_round(quantity, precision_rounding=product.uom_id.rounding),
            'move_out': move_out,
            'move_in': move_in,
            'reservation': self._get_reservation_data(reserved_move) if reserved_move else False,
            'in_transit': in_transit,
            'is_matched': any(move_id in [move_in_id, move_out_id] for move_id in move_to_match_ids),
            'uom_id' : product.uom_id.read()[0] if read else product.uom_id,
        }
        if move_in:
            document_in = move_in._get_source_document()
            line.update({
                'move_in': move_in.read(fields=self._get_report_moves_fields())[0] if read else move_in,
                'document_in' : {
                    '_name' : document_in._name,
                    'id' : document_in.id,
                    'name' : document_in.display_name,
                } if document_in else False,
                'receipt_date': format_date(self.env, move_in.date),
            })

        if move_out:
            document_out = move_out.sudo()._get_source_document()
            line.update({
                'move_out': move_out.read(fields=self._get_report_moves_fields())[0] if read else move_out,
                'document_out' : {
                    '_name' : document_out._name,
                    'id' : document_out.id,
                    'name' : document_out.display_name,
                } if document_out else False,
                'delivery_date': format_date(self.env, move_out.date),
            })
            if move_out.picking_id and read:
                line['move_out'].update({
                    'picking_id': move_out.picking_id.read(fields=['id', 'priority'])[0],
                })
        return line

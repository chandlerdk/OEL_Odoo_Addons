from odoo import fields, models
from odoo.tools.float_utils import float_compare, float_is_zero, float_round

from collections import defaultdict
from odoo import _, api, Command, fields, models
from odoo.exceptions import UserError
from odoo.osv import expression
from odoo.osv.expression import OR
from odoo.tools.float_utils import float_compare, float_is_zero, float_round
from odoo.tools.misc import clean_context, OrderedSet, groupby


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    # def action_check_availability_manual(self):
    #     moves = self.move_ids.filtered(lambda move: move.state not in ('draft', 'cancel', 'done', 'assigned')).sorted(
    #         key=lambda move: (-int(move.priority), not bool(move.date_deadline), move.date_deadline, move.date, move.id)
    #     )
    #     if not moves:
    #         raise UserError(_('Nothing to check the availability for.'))
    #     for move in moves:
    #         # if move.product_id and move.sale_line_id.product_id.bom_ids.filtered(lambda b: b.type == 'phantom'):
    #         bom = move.sale_line_id.product_id.bom_ids.filtered(lambda b: b.type == 'phantom')
    #         if bom:
    #             min_available_qty = float('inf')
    #             has_zero_qty = False
    #             for component_line in bom.bom_line_ids:
    #                 component_product = component_line.product_id
    #                 available_qty = component_product.qty_available
    #                 if available_qty <= 0:
    #                     has_zero_qty = True
    #                     break
    #                 if available_qty < min_available_qty:
    #                     min_available_qty = available_qty
    #             if has_zero_qty:
    #                 moves.write({'quantity': 0})
    #             else:
    #                 moves.write({'quantity': min_available_qty})
    #         else:
    #             move._action_assign()
    #         # else:
    #         #    move._action_assign()
    #
    #     return True

    def action_check_availability_manual(self):
        moves = self.move_ids.filtered(lambda move: move.state not in ('draft', 'cancel', 'done', 'assigned')).sorted(
            key=lambda move: (-int(move.priority), not bool(move.date_deadline), move.date_deadline, move.date, move.id)
        )

        if not moves:
            raise UserError(_('Nothing to check the availability for.'))

        sale_lines_dict = {}
        for move in moves:
            sale_line = move.sale_line_id
            if sale_line not in sale_lines_dict:
                sale_lines_dict[sale_line] = []
            sale_lines_dict[sale_line].append(move)

        for sale_line, sale_moves in sale_lines_dict.items():
            kit_product = sale_line.product_id
            bom = kit_product.bom_ids.filtered(lambda b: b.type == 'phantom')
            if bom:
                min_available_qty = float('inf')
                has_zero_qty = False

                for component_line in bom.bom_line_ids:
                    component_product = component_line.product_id
                    available_qty = component_product.qty_available

                    if available_qty <= 0:
                        has_zero_qty = True
                        break

                    min_available_qty = min(min_available_qty, available_qty)

                if has_zero_qty:
                    for move in sale_moves:
                        move.write({'quantity': 0})
                else:
                    for move in sale_moves:
                        move.write({'quantity': min(min_available_qty, move.product_uom_qty)})
            else:
                for move in sale_moves:
                    move._action_assign()

        return True

    def action_assign(self):
        """ Check availability of picking moves.
        This has the effect of changing the state and reserve quants on available moves, and may
        also impact the state of the picking as it is computed based on move's states.
        @return: True
        """
        if self.picking_type_code == 'incoming':
            self.mapped('package_level_ids').filtered(
                lambda pl: pl.state == 'draft' and not pl.move_ids)._generate_moves()
            self.filtered(lambda picking: picking.state == 'draft').action_confirm()
            moves = self.move_ids.filtered(lambda move: move.state not in ('draft', 'cancel', 'done')).sorted(
                key=lambda move: (
                    -int(move.priority), not bool(move.date_deadline), move.date_deadline, move.date, move.id)
            )
            if not moves:
                raise UserError(_('Nothing to check the availability for.'))

            moves._action_assign()

        elif self.picking_type_code == 'outgoing':
            self.action_check_availability_manual()
        return True

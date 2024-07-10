# -*- coding: utf-8 -*-

from odoo import models, fields, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_is_zero


class ReturnPicking(models.TransientModel):
    _inherit = 'stock.return.picking'

    def _prepare_picking_default_values(self):
        if self.env.context.get('rma_id'):
            rma_id = self.env.context.get('rma_id')
            claim_lines = self.env.context.get('claim_lines')
            vals = {
                'move_ids': [],
                'picking_type_id': rma_id.location_id.warehouse_id.rma_in_type_id.id,
                'state': 'draft',
                'claim_id': rma_id.id if not claim_lines else False,
                'partner_id': rma_id.partner_id.id,
                'origin': _("Return of %s", rma_id.name),
                'location_dest_id': rma_id.location_id.id,
            }
            return vals
        result = super(ReturnPicking, self)._prepare_picking_default_values()

        if self.env.context.get('rma_sale_warehouse'):
            rma_in_type_id = self.env.context.get(
                'rma_sale_warehouse'
            ).rma_in_type_id

            result['picking_type_id'] = rma_in_type_id.id

        return result

    def _prepare_move_default_values(self, return_line, new_picking):
        vals = super()._prepare_move_default_values(return_line, new_picking)
        if return_line.claim_line_id:
            vals['receipt_note'] = return_line.claim_line_id.receipt_note
        return vals

    def _create_returns(self):
        if self.env.context.get('rma_id'):
            for return_move in self.product_return_moves.mapped('move_id'):
                return_move.move_dest_ids.filtered(
                    lambda m: m.state not in ('done', 'cancel'))._do_unreserve()

            # create new picking for returned products
            new_picking = self.env['stock.picking'].create(
                self._prepare_picking_default_values())
            picking_type_id = new_picking.picking_type_id.id
            new_picking.message_post_with_source(
                'mail.message_origin_link',
                render_values={'self': new_picking, 'origin': self.picking_id},
                subtype_xmlid='mail.mt_note',
            )
            returned_lines = 0
            for return_line in self.product_return_moves:
                if self.env.context.get('rma_id'):
                    if not float_is_zero(return_line.quantity, return_line.uom_id.rounding):
                        returned_lines += 1
                        vals = self._prepare_move_default_values(
                            return_line, new_picking)
                        vals['origin_returned_move_id'] = False
                        if not new_picking.picking_type_id.default_location_src_id:
                            raise UserError(
                                _("Please set Default Source Location for %s Operations Types!." % new_picking.picking_type_id.display_name))
                        vals['location_id'] = new_picking.picking_type_id.default_location_src_id.id
                        vals['name'] = return_line.product_id.name
                        self.env['stock.move'].create(vals)
                    else:
                        if not return_line.move_id:
                            raise UserError(
                                _("You have manually created product lines, please delete them to proceed."))
                        if not float_is_zero(return_line.quantity, return_line.uom_id.rounding):
                            returned_lines += 1
                            vals = self._prepare_move_default_values(
                                return_line, new_picking)
                            r = return_line.move_id.copy(vals)
                            vals = {}

                            # +--------------------------------------------------------------------------------------------------------+
                            # |       picking_pick     <--Move Orig--    picking_pack     --Move Dest-->   picking_ship
                            # |              | returned_move_ids              ↑                                  | returned_move_ids
                            # |              ↓                                | return_line.move_id              ↓
                            # |       return pick(Add as dest)          return toLink                    return ship(Add as orig)
                            # +--------------------------------------------------------------------------------------------------------+
                            move_orig_to_link = return_line.move_id.move_dest_ids.mapped(
                                'returned_move_ids')
                            # link to original move
                            move_orig_to_link |= return_line.move_id
                            # link to siblings of original move, if any
                            move_orig_to_link |= return_line.move_id\
                                .mapped('move_dest_ids').filtered(lambda m: m.state not in ('cancel'))\
                                .mapped('move_orig_ids').filtered(lambda m: m.state not in ('cancel'))
                            move_dest_to_link = return_line.move_id.move_orig_ids.mapped(
                                'returned_move_ids')
                            # link to children of originally returned moves, if any. Note that the use of
                            # 'return_line.move_id.move_orig_ids.returned_move_ids.move_orig_ids.move_dest_ids'
                            # instead of 'return_line.move_id.move_orig_ids.move_dest_ids' prevents linking a
                            # return directly to the destination moves of its parents. However, the return of
                            # the return will be linked to the destination moves.
                            move_dest_to_link |= return_line.move_id.move_orig_ids.mapped('returned_move_ids')\
                                .mapped('move_orig_ids').filtered(lambda m: m.state not in ('cancel'))\
                                .mapped('move_dest_ids').filtered(lambda m: m.state not in ('cancel'))
                            vals['move_orig_ids'] = [
                                (4, m.id) for m in move_orig_to_link]
                            vals['move_dest_ids'] = [
                                (4, m.id) for m in move_dest_to_link]
                            r.write(vals)
            if not returned_lines:
                raise UserError(
                    _("Please specify at least one non-zero quantity."))

            # if self.env.context.get('rma_scrap_pciking'):
            #     new_picking.action_confirm()
            #     new_picking.action_assign()

            return new_picking.id, picking_type_id
        if self.env.context.get('no_legacy_order'):
            for return_move in self.product_return_moves.mapped('move_id'):
                return_move.move_dest_ids.filtered(lambda m: m.state not in ('done', 'cancel'))._do_unreserve()

            # create new picking for returned products
            new_picking = self.picking_id.copy(self._prepare_picking_default_values())
            picking_type_id = new_picking.picking_type_id.id
            new_picking.message_post_with_source(
                'mail.message_origin_link',
                render_values={'self': new_picking, 'origin': self.picking_id},
                subtype_xmlid='mail.mt_note',
            )
            returned_lines = 0
            for return_line in self.product_return_moves:
                if not return_line.move_id:
                    raise UserError(_("You have manually created product lines, please delete them to proceed."))
                if not float_is_zero(return_line.quantity, return_line.uom_id.rounding):
                    returned_lines += 1
                    vals = self._prepare_move_default_values(return_line, new_picking)
                    r = return_line.move_id.copy(vals)
                    vals = {}

                    # +--------------------------------------------------------------------------------------------------------+
                    # |       picking_pick     <--Move Orig--    picking_pack     --Move Dest-->   picking_ship
                    # |              | returned_move_ids              ↑                                  | returned_move_ids
                    # |              ↓                                | return_line.move_id              ↓
                    # |       return pick(Add as dest)          return toLink                    return ship(Add as orig)
                    # +--------------------------------------------------------------------------------------------------------+
                    move_orig_to_link = return_line.move_id.move_dest_ids.mapped('returned_move_ids')
                    # link to original move
                    move_orig_to_link |= return_line.move_id
                    # link to siblings of original move, if any
                    move_orig_to_link |= return_line.move_id\
                        .mapped('move_dest_ids').filtered(lambda m: m.state not in ('cancel'))\
                        .mapped('move_orig_ids').filtered(lambda m: m.state not in ('cancel'))
                    move_dest_to_link = return_line.move_id.move_orig_ids.mapped('returned_move_ids')
                    # link to children of originally returned moves, if any. Note that the use of
                    # 'return_line.move_id.move_orig_ids.returned_move_ids.move_orig_ids.move_dest_ids'
                    # instead of 'return_line.move_id.move_orig_ids.move_dest_ids' prevents linking a
                    # return directly to the destination moves of its parents. However, the return of
                    # the return will be linked to the destination moves.
                    move_dest_to_link |= return_line.move_id.move_orig_ids.mapped('returned_move_ids')\
                        .mapped('move_orig_ids').filtered(lambda m: m.state not in ('cancel'))\
                        .mapped('move_dest_ids').filtered(lambda m: m.state not in ('cancel'))
                    vals['move_orig_ids'] = [(4, m.id) for m in move_orig_to_link]
                    vals['move_dest_ids'] = [(4, m.id) for m in move_dest_to_link]
                    r.write(vals)
            if not returned_lines:
                raise UserError(_("Please specify at least one non-zero quantity."))

            new_picking._change_location()
            return new_picking.id, picking_type_id
        else:
            return super()._create_returns()


class ReturnPickingLine(models.TransientModel):
    _inherit = "stock.return.picking.line"

    claim_line_id = fields.Many2one("claim.line.ept")

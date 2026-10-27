from odoo import models


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _action_confirm(self, merge=True, merge_into=False, create_proc=True):
        """Pickings with kits are not reserved automatically at confirmation."""
        moves = super(StockMove, self.with_context(skip_kit_picking_assign=True))._action_confirm(
            merge=merge, merge_into=merge_into, create_proc=create_proc)
        return moves.with_env(self.env)

    def _get_kit_picking_reservable_qty(self):
        """Lowest quantity (in move UoM) that every move of the picking can reserve."""
        quantities = []
        for move in self:
            missing = move.product_uom_qty - move.quantity
            available = move.product_id.uom_id._compute_quantity(
                move._get_available_quantity(move.location_id),
                move.uom_id, rounding_method='HALF-UP')
            quantities.append(max(min(missing, available), 0))
        return min(quantities) if quantities else 0

    def _action_assign(self, force_qty=False):
        if force_qty:
            return super()._action_assign(force_qty=force_qty)

        kit_moves = self.filtered(lambda m: m.picking_id.has_kits)
        res = super(StockMove, self - kit_moves)._action_assign()
        if self.env.context.get('skip_kit_picking_assign'):
            return res

        to_assign = kit_moves.filtered(
            lambda m: not m.picked and m.state in ['confirmed', 'waiting', 'partially_available'])
        for picking in to_assign.picking_id:
            moves = to_assign.filtered(lambda m: m.picking_id == picking)
            qty = moves._get_kit_picking_reservable_qty()
            if qty > 0:
                super(StockMove, moves)._action_assign(force_qty=qty)
                moves._recompute_state()
        return res

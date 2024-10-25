from odoo import fields,models
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

    def action_assign(self):
        """ Check availability of picking moves.
        This has the effect of changing the state and reserve quants on available moves, and may
        also impact the state of the picking as it is computed based on move's states.
        @return: True
        """
        if self.picking_type_code == 'incoming':
            self.mapped('package_level_ids').filtered(lambda pl: pl.state == 'draft' and not pl.move_ids)._generate_moves()
            self.filtered(lambda picking: picking.state == 'draft').action_confirm()
            moves = self.move_ids.filtered(lambda move: move.state not in ('draft', 'cancel', 'done')).sorted(
                key=lambda move: (-int(move.priority), not bool(move.date_deadline), move.date_deadline, move.date, move.id)
            )
            if not moves:
                raise UserError(_('Nothing to check the availability for.'))

            moves._action_assign()

        elif self.picking_type_code == 'outgoing':
            self.mapped('package_level_ids').filtered(
                lambda pl: pl.state == 'draft' and not pl.move_ids)._generate_moves()
            self.filtered(lambda picking: picking.state == 'draft').action_confirm()
            moves = self.move_ids.filtered(lambda move: move.state not in ('draft', 'cancel', 'done')).sorted(
                key=lambda move: (
                -int(move.priority), not bool(move.date_deadline), move.date_deadline, move.date, move.id)
            )
            if not moves:
                raise UserError(_('Nothing to check the availability for.'))

            moves._action_custom_assign()
        return True

class StockMove(models.Model):
    _inherit = 'stock.move'

    def _action_confirm(self, merge=True, merge_into=False):

        moves = self
        if self.purchase_line_id:
            res = super(StockMove,self)._action_confirm(merge=True, merge_into=False)
            return res

        if self.sale_line_id:
            move_create_proc, move_to_confirm, move_waiting = OrderedSet(), OrderedSet(), OrderedSet()
            to_assign = defaultdict(OrderedSet)
            for move in self:

                if move.state != 'draft':
                    continue
                # if the move is preceded, then it's waiting (if preceding move is done, then action_assign has been called already and its state is already available)
                if move.move_orig_ids:
                    move_waiting.add(move.id)
                else:
                    if move.procure_method == 'make_to_order':
                        move_create_proc.add(move.id)
                    else:
                        move_to_confirm.add(move.id)
                if move._should_be_assigned():
                    key = (move.group_id.id, move.location_id.id, move.location_dest_id.id)
                    to_assign[key].add(move.id)

            move_create_proc, move_to_confirm, move_waiting = self.browse(move_create_proc), self.browse(move_to_confirm), self.browse(move_waiting)

            # create procurements for make to order moves
            procurement_requests = []
            for move in move_create_proc:
                values = move._prepare_procurement_values()
                origin = move._prepare_procurement_origin()
                procurement_requests.append(self.env['procurement.group'].Procurement(
                    move.product_id, move.product_uom_qty, move.product_uom,
                    move.location_id, move.rule_id and move.rule_id.name or "/",
                    origin, move.company_id, values))
            self.env['procurement.group'].run(procurement_requests, raise_user_error=not self.env.context.get('from_orderpoint'))

            move_to_confirm.write({'state': 'confirmed'})
            (move_waiting | move_create_proc).write({'state': 'waiting'})
            # procure_method sometimes changes with certain workflows so just in case, apply to all moves
            (move_to_confirm | move_waiting | move_create_proc).filtered(lambda m: m.picking_type_id.reservation_method == 'at_confirm')\
                .write({'reservation_date': fields.Date.today()})

            # assign picking in batch for all confirmed move that share the same details
            for moves_ids in to_assign.values():
                self.browse(moves_ids).with_context(clean_context(self.env.context))._assign_picking()
            new_push_moves = self._push_apply()
            self._check_company()

            if merge:
                moves = self._merge_moves(merge_into=merge_into)

            # Transform remaining move in return in case of negative initial demand
            neg_r_moves = moves.filtered(lambda move: float_compare(
                move.product_uom_qty, 0, precision_rounding=move.product_uom.rounding) < 0)
            for move in neg_r_moves:
                move.location_id, move.location_dest_id = move.location_dest_id, move.location_id
                orig_move_ids, dest_move_ids = [], []
                for m in move.move_orig_ids | move.move_dest_ids:
                    from_loc, to_loc = m.location_id, m.location_dest_id
                    if float_compare(m.product_uom_qty, 0, precision_rounding=m.product_uom.rounding) < 0:
                        from_loc, to_loc = to_loc, from_loc
                    if to_loc == move.location_id:
                        orig_move_ids += m.ids
                    elif move.location_dest_id == from_loc:
                        dest_move_ids += m.ids
                move.move_orig_ids, move.move_dest_ids = [(6, 0, orig_move_ids)], [(6, 0, dest_move_ids)]
                move.product_uom_qty *= -1
                if move.picking_type_id.return_picking_type_id:
                    move.picking_type_id = move.picking_type_id.return_picking_type_id
                # We are returning some products, we must take them in the source location
                move.procure_method = 'make_to_stock'
            neg_r_moves._assign_picking()

            # call `_action_assign` on every confirmed move which location_id bypasses the reservation + those expected to be auto-assigned
            if self.picking_id.has_kits == False:
                moves.filtered(lambda move: move.state in ('confirmed', 'partially_available')
                               and (move._should_bypass_reservation()
                                    or move.picking_type_id.reservation_method == 'at_confirm'
                                    or (move.reservation_date and move.reservation_date <= fields.Date.today())))\
                     ._action_custom_assign()
            if new_push_moves:
                neg_push_moves = new_push_moves.filtered(lambda sm: float_compare(sm.product_uom_qty, 0, precision_rounding=sm.product_uom.rounding) < 0)
                (new_push_moves - neg_push_moves).sudo()._action_confirm()
                # Negative moves do not have any picking, so we should try to merge it with their siblings
                neg_push_moves._action_confirm(merge_into=neg_push_moves.move_orig_ids.move_dest_ids)

        return moves


    def _update_custom_reserved_quantity(self, need, location_id, quant_ids=None, lot_id=None, package_id=None, owner_id=None, strict=True):
        """ Create or update move lines and reserves quantity from quants
            Expects the need (qty to reserve) and location_id to reserve from.
            `quant_ids` can be passed as an optimization since no search on the database
            is performed and reservation is done on the passed quants set
        """
        self.ensure_one()
        if quant_ids is None:
            quant_ids = self.env['stock.quant']
        if not lot_id:
            lot_id = self.env['stock.lot']
        if not package_id:
            package_id = self.env['stock.quant.package']
        if not owner_id:
            owner_id = self.env['res.partner']



        quants = quant_ids._get_reserve_quantity(
            self.product_id, location_id, need, product_packaging_id=self.product_packaging_id,
            uom_id=self.product_uom, lot_id=lot_id, package_id=package_id, owner_id=owner_id, strict=strict)

        if len(quants) < 0:
            quants = 0
        return quants


    def get_quants_list(self,force_qty=False):
        moves_to_assign = self
        list_move = []
        for move in moves_to_assign:

            forced_package_id = move.package_level_id.package_id or None
            reserved_availability = {move: move.quantity for move in self}
            quants_by_product = self.env['stock.quant']._get_quants_by_products_locations(move.product_id,
                                                                                          move.location_id)
            quants = quants_by_product[move.product_id.id]
            if not force_qty:
                missing_reserved_uom_quantity = move.product_uom_qty - reserved_availability[move]
            else:
                missing_reserved_uom_quantity = force_qty
            missing_reserved_quantity = move.product_uom._compute_quantity(missing_reserved_uom_quantity, move.product_id.uom_id, rounding_method='HALF-UP')

            need = missing_reserved_quantity

            t_quantity = move._update_custom_reserved_quantity(need, move.location_id, quant_ids=quants,
                                                                   package_id=forced_package_id, strict=False)
            if len(t_quantity) == 0:
                list_move.append(0)
            else:
                list_move.append(t_quantity[0][1])
        if len(list_move) > 0:
            low_val = min(list_move)
            return low_val
        else:
            return None

    def _action_custom_assign(self, force_qty=False):
        """ Reserve stock moves by creating their stock move lines. A stock move is
        considered reserved once the sum of `reserved_qty` for all its move lines is
        equal to its `product_qty`. If it is less, the stock move is considered
        partially available.
        """

        if self.picking_id.has_kits == True:
            val = self.get_quants_list()
        else:
            val = 0.0

        StockMove = self.env['stock.move']
        assigned_moves_ids = OrderedSet()
        partially_available_moves_ids = OrderedSet()
        # Read the `reserved_availability` field of the moves out of the loop to prevent unwanted
        # cache invalidation when actually reserving the move.
        reserved_availability = {move: move.quantity for move in self}
        roundings = {move: move.product_id.uom_id.rounding for move in self}
        move_line_vals_list = []
        # Once the quantities are assigned, we want to find a better destination location thanks
        # to the putaway rules. This redirection will be applied on moves of `moves_to_redirect`.
        moves_to_redirect = OrderedSet()
        moves_to_assign = self
        if not force_qty:
            moves_to_assign = moves_to_assign.filtered(
                lambda m: not m.picked and m.state in ['confirmed', 'waiting', 'partially_available']
            )
        moves_mto = moves_to_assign.filtered(lambda m: m.move_orig_ids and not m._should_bypass_reservation())
        quants_cache = self.env['stock.quant']._get_quants_cache_by_products_locations(moves_mto.product_id, moves_mto.location_id)
        for move in moves_to_assign:
            rounding = roundings[move]
            if not force_qty:
                missing_reserved_uom_quantity = move.product_uom_qty - reserved_availability[move]
            else:
                missing_reserved_uom_quantity = force_qty
            if float_compare(missing_reserved_uom_quantity, 0, precision_rounding=rounding) <= 0:
                assigned_moves_ids.add(move.id)
                continue
            missing_reserved_quantity = move.product_uom._compute_quantity(missing_reserved_uom_quantity, move.product_id.uom_id, rounding_method='HALF-UP')
            if move._should_bypass_reservation():
                # create the move line(s) but do not impact quants
                if move.move_orig_ids:
                    available_move_lines = move._get_available_move_lines(assigned_moves_ids, partially_available_moves_ids)
                    for (location_id, lot_id, package_id, owner_id), quantity in available_move_lines.items():
                        qty_added = min(missing_reserved_quantity, quantity)
                        move_line_vals = move._prepare_custom_move_line_vals(qty_added)
                        move_line_vals.update({
                            'location_id': location_id.id,
                            'lot_id': lot_id.id,
                            'lot_name': lot_id.name,
                            'owner_id': owner_id.id,
                            'package_id': package_id.id,
                        })
                        move_line_vals_list.append(move_line_vals)
                        missing_reserved_quantity -= qty_added
                        if float_is_zero(missing_reserved_quantity, precision_rounding=move.product_id.uom_id.rounding):
                            break

                if missing_reserved_quantity and move.product_id.tracking == 'serial' and (move.picking_type_id.use_create_lots or move.picking_type_id.use_existing_lots):
                    for i in range(0, int(missing_reserved_quantity)):
                        move_line_vals_list.append(move._prepare_custom_move_line_vals(quantity=1))
                elif missing_reserved_quantity:
                    to_update = move.move_line_ids.filtered(lambda ml: ml.product_uom_id == move.product_uom and
                                                            ml.location_id == move.location_id and
                                                            ml.location_dest_id == move.location_dest_id and
                                                            ml.picking_id == move.picking_id and
                                                            not ml.picked and
                                                            not ml.lot_id and
                                                            not ml.result_package_id and
                                                            not ml.package_id and
                                                            not ml.owner_id)
                    if to_update:
                        to_update[0].quantity += move.product_id.uom_id._compute_quantity(
                            missing_reserved_quantity, move.product_uom, rounding_method='HALF-UP')
                    else:
                        move_line_vals_list.append(move._prepare_custom_move_line_vals(val,quantity=missing_reserved_quantity))
                assigned_moves_ids.add(move.id)
                moves_to_redirect.add(move.id)
            else:
                if float_is_zero(move.product_uom_qty, precision_rounding=move.product_uom.rounding) and not force_qty:
                    assigned_moves_ids.add(move.id)
                elif not move.move_orig_ids:
                    if move.procure_method == 'make_to_order':
                        continue
                    # If we don't need any quantity, consider the move assigned.
                    need = missing_reserved_quantity
                    if float_is_zero(need, precision_rounding=rounding):
                        assigned_moves_ids.add(move.id)
                        continue
                    # Reserve new quants and create move lines accordingly.
                    forced_package_id = move.package_level_id.package_id or None
                    taken_quantity = move._update__reserved_quantity(need,val, move.location_id, package_id=forced_package_id, strict=False)
                    if float_is_zero(taken_quantity, precision_rounding=rounding):
                        continue
                    moves_to_redirect.add(move.id)
                    if float_compare(need, taken_quantity, precision_rounding=rounding) == 0:
                        assigned_moves_ids.add(move.id)
                    else:
                        partially_available_moves_ids.add(move.id)
                else:
                    # Check what our parents brought and what our siblings took in order to
                    # determine what we can distribute.
                    # `quantity` is in `ml.product_uom_id` and, as we will later increase
                    # the reserved quantity on the quants, convert it here in
                    # `product_id.uom_id` (the UOM of the quants is the UOM of the product).
                    available_move_lines = move._get_available_move_lines(assigned_moves_ids, partially_available_moves_ids)
                    if not available_move_lines:
                        continue
                    for move_line in move.move_line_ids.filtered(lambda m: m.quantity_product_uom):
                        if available_move_lines.get((move_line.location_id, move_line.lot_id, move_line.package_id, move_line.owner_id)):
                            available_move_lines[(move_line.location_id, move_line.lot_id, move_line.package_id, move_line.owner_id)] -= move_line.quantity_product_uom
                    for (location_id, lot_id, package_id, owner_id), quantity in available_move_lines.items():
                        need = move.product_qty - sum(move.move_line_ids.mapped('quantity_product_uom'))
                        # `quantity` is what is brought by chained done move lines. We double check
                        # here this quantity is available on the quants themselves. If not, this
                        # could be the result of an inventory adjustment that removed totally of
                        # partially `quantity`. When this happens, we chose to reserve the maximum
                        # still available. This situation could not happen on MTS move, because in
                        # this case `quantity` is directly the quantity on the quants themselves.

                        taken_quantity = move.with_context(quants_cache=quants_cache)._update__reserved_quantity(
                            min(quantity, need),val, location_id, None, lot_id, package_id, owner_id)
                        if float_is_zero(taken_quantity, precision_rounding=rounding):
                            continue
                        moves_to_redirect.add(move.id)
                        if float_is_zero(need - taken_quantity, precision_rounding=rounding):
                            assigned_moves_ids.add(move.id)
                            break
                        partially_available_moves_ids.add(move.id)
            if move.product_id.tracking == 'serial':
                move.next_serial_count = move.product_uom_qty

        self.env['stock.move.line'].create(move_line_vals_list)
        StockMove.browse(partially_available_moves_ids).write({'state': 'partially_available'})
        StockMove.browse(assigned_moves_ids).write({'state': 'assigned'})
        if not self.env.context.get('bypass_entire_pack'):
            self.picking_id._check_entire_pack()
        StockMove.browse(moves_to_redirect).move_line_ids._apply_putaway_strategy()

    def _update__reserved_quantity(self, need,val, location_id, quant_ids=None, lot_id=None, package_id=None, owner_id=None, strict=True):
        """ Create or update move lines and reserves quantity from quants
            Expects the need (qty to reserve) and location_id to reserve from.
            `quant_ids` can be passed as an optimization since no search on the database
            is performed and reservation is done on the passed quants set
        """
        self.ensure_one()
        if not quant_ids:
            quant_ids = self.env['stock.quant']
        if not lot_id:
            lot_id = self.env['stock.lot']
        if not package_id:
            package_id = self.env['stock.quant.package']
        if not owner_id:
            owner_id = self.env['res.partner']

        quants = quant_ids._get_reserve_quantity(
            self.product_id, location_id, need, product_packaging_id=self.product_packaging_id,
            uom_id=self.product_uom, lot_id=lot_id, package_id=package_id, owner_id=owner_id, strict=strict)
        taken_quantity = 0
        rounding = self.env['decimal.precision'].precision_get('Product Unit of Measure')
        # Find a candidate move line to update or create a new one.
        candidate_lines = {}
        for line in self.move_line_ids:
            if line.result_package_id or line.product_id.tracking == 'serial':
                continue
            candidate_lines[(line.location_id, line.lot_id, line.package_id, line.owner_id)] = line
        move_line_vals = []
        grouped_quants = {}
        # Handle quants duplication
        for quant, quantity in quants:
            if (quant.location_id, quant.lot_id, quant.package_id, quant.owner_id) not in grouped_quants:
                grouped_quants[quant.location_id, quant.lot_id, quant.package_id, quant.owner_id] = [quant, quantity]
            else:
                grouped_quants[quant.location_id, quant.lot_id, quant.package_id, quant.owner_id][1] += quantity
        for reserved_quant, quantity in grouped_quants.values():
            taken_quantity += val
            to_update = candidate_lines.get((reserved_quant.location_id, reserved_quant.lot_id, reserved_quant.package_id, reserved_quant.owner_id))
            if to_update:
                uom_quantity = self.product_id.uom_id._compute_quantity(quantity, to_update.product_uom_id, rounding_method='HALF-UP')
                uom_quantity = float_round(uom_quantity, precision_digits=rounding)
                uom_quantity_back_to_product_uom = to_update.product_uom_id._compute_quantity(uom_quantity, self.product_id.uom_id, rounding_method='HALF-UP')
            if to_update and float_compare(quantity, uom_quantity_back_to_product_uom, precision_digits=rounding) == 0:
                to_update.quantity += val
            else:
                if self.product_id.tracking == 'serial':
                    vals_list = self._add_serial_move_line_to_vals_list(reserved_quant, quantity)
                    if vals_list:
                        move_line_vals += vals_list
                else:
                    move_line_vals.append(self._prepare_custom_move_line_vals(val,quantity=quantity, reserved_quant=reserved_quant))
        if move_line_vals:
            self.env['stock.move.line'].create(move_line_vals)
        return taken_quantity

    def _prepare_custom_move_line_vals(self,val ,quantity=None, reserved_quant=None):
        self.ensure_one()
        vals = {
            'move_id': self.id,
            'product_id': self.product_id.id,
            'product_uom_id': self.product_uom.id,
            'location_id': self.location_id.id,
            'location_dest_id': self.location_dest_id.id,
            'picking_id': self.picking_id.id,
            'company_id': self.company_id.id,
        }
        if quantity:

            # TODO could be also move in create/write
            rounding = self.env['decimal.precision'].precision_get('Product Unit of Measure')
            uom_quantity = self.product_id.uom_id._compute_quantity(quantity, self.product_uom, rounding_method='HALF-UP')
            uom_quantity = float_round(uom_quantity, precision_digits=rounding)
            uom_quantity_back_to_product_uom = self.product_uom._compute_quantity(uom_quantity, self.product_id.uom_id, rounding_method='HALF-UP')

            if val != None:
                if float_compare(quantity, uom_quantity_back_to_product_uom, precision_digits=rounding) == 0:
                    vals = dict(vals, quantity=val)
                else:
                    vals = dict(vals, quantity=val, product_uom_id=self.product_id.uom_id.id)
            else:
                if float_compare(quantity, uom_quantity_back_to_product_uom, precision_digits=rounding) == 0:
                    vals = dict(vals, quantity=uom_quantity)
                else:
                    vals = dict(vals, quantity=quantity, product_uom_id=self.product_id.uom_id.id)
        package = None
        if reserved_quant:
            package = reserved_quant.package_id
            vals = dict(
                vals,
                location_id=reserved_quant.location_id.id,
                lot_id=reserved_quant.lot_id.id or False,
                package_id=package.id or False,
                owner_id =reserved_quant.owner_id.id or False,
            )
        return vals








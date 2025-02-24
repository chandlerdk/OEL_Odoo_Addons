# -*- coding: utf-8 -*-

from odoo import fields, models, api,  _
from odoo.tools.misc import clean_context
from collections import defaultdict
from odoo.exceptions import UserError, ValidationError


class RmaClaim(models.Model):
    _inherit = 'crm.claim.ept'

    helpdesk_ticket_id = fields.Many2one(
        'helpdesk.ticket', string="Helpdesk Ticket", copy=False
    )
    scrap_count = fields.Integer(compute='_compute_scrap_count')
    is_legacy_order = fields.Boolean(string="Legacy Order")
    helpdesk_ticket_count = fields.Integer(compute='_compute_ticket_count')
    legacy_order_line_ids = fields.One2many(
        "claim.line.ept", "legacy_claim_id", string="Legacy Order Line"
    )
    delivery_count = fields.Integer(compute='_compute_delivery_count')
    receipt_count = fields.Integer(compute='_compute_receipt_count')

    def check_and_create_refund_invoice(self, claim_lines):
        """
        This method checks if an invoice is posted and creates the refund invoice accordingly.
        It ensures kit products are included instead of their subcomponents.
        """
        product_process_dict = {}
        refund_invoice_ids = {}
        kit_product_quantities = {}  # Track total kit quantities per sale order line

        for line in claim_lines:
            sale_line = line.move_id.sale_line_id
            kit_product_id = False

            # Identify if the sale line is a kit
            if sale_line:
                bom = self.env['mrp.bom'].search([
                    ('product_tmpl_id', '=', sale_line.product_id.product_tmpl_id.id),
                    ('type', '=', 'phantom')
                ], limit=1)

                if bom:
                    kit_product = self.env['product.product'].search([
                        ('product_tmpl_id', '=', sale_line.product_id.product_tmpl_id.id)
                    ], limit=1)

                    if kit_product:
                        kit_product_id = kit_product.id

            if kit_product_id:
                key = (kit_product_id, sale_line.id)

                # Aggregate refund quantities for kit products
                if key in kit_product_quantities:
                    kit_product_quantities[key] = line.quantity
                else:
                    kit_product_quantities[key] = line.quantity

            else:
                # Normal product processing
                if line.id not in product_process_dict:
                    product_process_dict.update({
                        line.id: {'total_qty': line.return_qty, 'invoice_line_ids': {}}
                    })

                invoice_lines = line.move_id.sale_line_id.invoice_lines
                for invoice_line in invoice_lines.filtered(lambda l: l.move_id.move_type == 'out_invoice'):
                    if invoice_line.move_id.state != 'posted':
                        message = _("The invoice was not posted. Please check invoice :"
                                    "<a href=# data-oe-model=account.move data-oe-id=%d>%s</a>") % (
                                      invoice_line.move_id.id, invoice_line.move_id.display_name)
                        self.message_post(body=message)
                        return False

                    product_line = product_process_dict.get(line.id)
                    if product_line.get('process_qty', 0) < product_line.get('total_qty', 0):
                        product_line, process_qty = self.prepare_product_qty_dict(
                            product_line, invoice_line)

                        product_line.get('invoice_line_ids').update({
                            invoice_line.id: process_qty,
                            'invoice_id': invoice_line.move_id.id
                        })

                        # ✅ Pass **claim line** for normal products
                        refund_invoice_ids = self.prepare_refund_invoice_dict(
                            line, refund_invoice_ids, invoice_line, process_qty)

        # Process the aggregated kit product quantities
        for (kit_product_id, sale_line_id), total_qty in kit_product_quantities.items():
            sale_line = self.env['sale.order.line'].browse(sale_line_id)
            invoice_lines = sale_line.invoice_lines.filtered(lambda l: l.move_id.move_type == 'out_invoice')

            for invoice_line in invoice_lines:
                # ✅ Pass **sale order line** instead of claim line for kits
                refund_invoice_ids = self.prepare_refund_invoice_dict(
                    sale_line, refund_invoice_ids, invoice_line, total_qty, is_kit=True, kit_product_id=kit_product_id)

        return refund_invoice_ids

    def prepare_refund_invoice_dict(self, line, refund_invoice_ids, invoice_line, process_qty, is_kit=False,
                                    kit_product_id=None):
        """Prepare refund invoice values based on invoice."""
        print("Processing Line:", line, "Is Kit:", is_kit)

        if is_kit:
            sale_line = line
            product_id = kit_product_id
        else:
            sale_line = line.move_id.sale_line_id
            product_id = line.product_id.id

        invoice_id = invoice_line.move_id.id
        if invoice_id not in refund_invoice_ids:
            refund_invoice_ids[invoice_id] = []

        refund_invoice_ids[invoice_id].append({
            product_id: process_qty,
            'price': invoice_line.price_unit,
            'tax_id': invoice_line.tax_ids.ids,
            'discount': invoice_line.discount,
            'sale_line_id': sale_line.id
        })

        return refund_invoice_ids

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        if self.env.context.get('helpdesk_ticket_name'):
            defaults['name'] = self.env.context.get('helpdesk_ticket_name')
        return defaults

    @api.constrains('legacy_order_line_ids')
    def _legacy_order_line(self):
        self.claim_line_ids = self.legacy_order_line_ids.ids
        self._check_duplicate_itmes()

    @api.constrains('claim_line_ids')
    def _claim_line_ids(self):
        self._check_duplicate_itmes()

    @api.onchange('is_legacy_order')
    def _onchange_legacy_order(self):
        if self.is_legacy_order:
            self.picking_id = False
            self.claim_line_ids = False
            self.sale_id = False

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        self.email_from = self.partner_id.email
        self.partner_phone = self.partner_id.phone

    def _check_duplicate_itmes(self):
        vals = defaultdict(list)
        for rec in self.claim_line_ids:
            vals[rec.product_id.id] += [rec.claim_type]
        for value in vals.values():
            if len(set(value)) != 1:
                raise ValidationError(
                    "Kindly create another RMA record to assigned different RMA workflow to the same product."
                )

    def create_picking_grp(self):
        group_id = self.env['procurement.group'].create({
            'name': self.code,
            'partner_id': self.partner_id.id
        })
        return group_id

    def dist_between_two_lat_lon(self, *args):
        from math import asin, cos, radians, sin, sqrt
        lat1, lat2, long1, long2 = map(radians, args)

        dist_lats = abs(lat2 - lat1)
        dist_longs = abs(long2 - long1)
        a = sin(dist_lats / 2) ** 2 + cos(lat1) * \
            cos(lat2) * sin(dist_longs / 2) ** 2
        c = asin(sqrt(a)) * 2
        # the "Earth radius" R varies from 6356.752 km at the poles to 6378.137 km at the equator.
        radius_earth = 6378
        return c * radius_earth

    def find_closest_lat_lon(self, data, v):
        try:
            return min(data, key=lambda p: self.dist_between_two_lat_lon(
                v['lat'], p['lat'], v['lon'], p['lon']))
        except TypeError:
            print('Not a list or not a number.')

    def find_closest_warehouse(self, route_ids, partner_id):
        city_list = []
        for route in route_ids:
            warehouse_id = route.rule_ids.location_src_id.warehouse_id
            if warehouse_id and warehouse_id.partner_id.partner_latitude != 0.00 \
                    and warehouse_id.partner_id.partner_longitude != 0.00:
                city_list.append(
                    {'lat': warehouse_id.partner_id.partner_latitude,
                     'lon': warehouse_id.partner_id.partner_longitude,
                     'route_id': route.id})
        if city_list:
            if partner_id.partner_latitude != 0.00 and partner_id.partner_longitude != 0.00:
                city_to_find = {'lat': partner_id.partner_latitude,
                                'lon': partner_id.partner_longitude}
                data = self.find_closest_lat_lon(city_list, city_to_find)
                route_id = self.env['stock.route'].browse(
                    data.get('route_id'))
                return route_id
            else:
                return False

    def find_nearest_warehouse(self):
        not_available_lines = self.claim_line_ids.filtered(
            lambda l: l.product_id and l.product_id.type == 'product' and
            l.product_id.qty_available < l.quantity)
        product_ids = self.claim_line_ids.filtered(
            lambda l: l.product_id and l.product_id.type == 'product').mapped('product_id')
        if not (self.partner_delivery_id.partner_latitude and self.partner_delivery_id.partner_longitude):
            raise ValidationError(
                "Please validate clients Delivery Address for Geo Localization to update latitude and longitude of delivery address.")
        route_ids = self.env['stock.route'].search([('sale_selectable', '=', True), ('rule_ids', '!=', False)])
        suspence_route_ids = self.env['stock.route']
        simple_route_ids = self.env['stock.route']
        for rec in route_ids:
            if rec.rule_ids:
                suspence_rule_id = rec.rule_ids[0].filtered(
                    lambda r: r.location_src_id.warehouse_id.suspence_warehouse
                )
                simple_rule_id = rec.rule_ids[0].filtered(
                    lambda r: r.location_src_id.warehouse_id.auto_allocation_warehouse and not r.location_src_id.warehouse_id.suspence_warehouse
                )
                if suspence_rule_id:
                    suspence_route_ids += rec
                if simple_rule_id:
                    simple_route_ids += rec
        if not suspence_route_ids:
                raise ValidationError(
                    "There is no route configure with suspence warehouse."
                )
        if len(not_available_lines) == len(product_ids):
            self.claim_line_ids.write({
                'route_id': suspence_route_ids[0].id
            })
        else:
            available_routes = []
            if simple_route_ids:
                for route in simple_route_ids:
                    available_quants = {}
                    for line in self.claim_line_ids.filtered(lambda l: l.product_id and l.product_id.type == 'product'):
                        quant_ids = self.env['stock.quant'].search(
                            [('product_id', 'in', line.product_id.ids),
                             ('on_hand', '=', True),
                             ('location_id', '=', route.rule_ids[0].location_src_id.id)])
                        if line.quantity < sum(quant_ids.mapped('quantity')):
                            available_quants[line.product_id.id] = quant_ids
                    if len(available_quants) == len(product_ids.ids):
                        available_routes.append(route)
                if available_routes:
                    closed_route = self.find_closest_warehouse(
                        available_routes, self.partner_delivery_id)
                    self.claim_line_ids.write({
                        'route_id': closed_route.id
                    })
                else:
                    self.claim_line_ids.write({
                        'route_id': suspence_route_ids[0].id
                    })

    def approve_claim(self):
        if not self.partner_delivery_id:
            raise UserError(_("Please set partner delivery address."))

        if self.is_legacy_order:
            refund_lines, do_lines, so_lines = self.prepare_list_replace_other_product()

            if refund_lines:
                self.create_refund(refund_lines)
            if do_lines:
                self.create_do(do_lines)
            if so_lines:
                self.create_so(so_lines)
            self.write({'state': 'approve'})
            if self.is_rma_without_incoming:
                self.write({'state': 'process'})
            else:
                group_id = self.create_picking_grp()
                return_picking_id = self.create_return_picking()
                if return_picking_id:
                    return_picking_id.write({
                        'claim_id': self.id,
                        'group_id': group_id.id
                    })
                    return_picking_id.with_context(claim_id=self, rma_group_id=group_id).action_assign()
                scrap_return_picking_id = self.scrap_create_return_picking()
                if scrap_return_picking_id:
                    scrap_return_picking_id.write({'claim_id': self.id})
                    scrap_return_picking_id.with_context(scrap_receipt=True).action_confirm()
                    scrap_return_picking_id.with_context(scrap_receipt=True).action_assign()
                    scrap_return_picking_id.with_context(scrap_receipt=True).button_validate()

            self.sudo().action_rma_send_email()
        else:
            refund_lines, do_lines, so_lines = self.prepare_list_replace_other_product()

            if len(self.claim_line_ids) <= 0:
                raise UserError(_("Please set return products."))

            processed_product_list = []
            for line in self.claim_line_ids:
                total_qty = 0
                self.check_claim_line_validate(line)

                # no need (discuss)
                prev_claim_lines = line.search([('move_id', '=', line.move_id.id),
                                                ('claim_id.state', 'in', ['process',
                                                                          'approve',
                                                                          'close'])])
                for move in prev_claim_lines:
                    total_qty += move.quantity
                if total_qty >= line.move_id.quantity:
                    processed_product_list.append(line.product_id.name)
                # end
                self.check_previous_claim_qty(line)

            if processed_product_list:
                raise UserError(_('%s Product\'s delivered quantites were already '
                                  'processed for RMA') % (", ".join(processed_product_list)))
            if refund_lines:
                self.create_refund(refund_lines)
            if do_lines:
                self.create_do(do_lines)
            if so_lines:
                self.create_so(so_lines)
            self.write({'state': 'approve'})

            if self.is_rma_without_incoming:
                self.write({'state': 'process'})
            else:
                group_id = self.create_picking_grp()
                return_picking_id = self.create_return_picking()
                if return_picking_id:
                    return_picking_id.write({
                        'claim_id': self.id,
                        'group_id': group_id.id
                    })
                    return_picking_id.with_context(claim_id=self, rma_group_id=group_id).action_assign()
                scrap_return_picking_id = self.scrap_create_return_picking()
                if scrap_return_picking_id:
                    scrap_return_picking_id.write({'claim_id': self.id})
                    scrap_return_picking_id.with_context(scrap_receipt=True).action_confirm()
                    scrap_return_picking_id.with_context(scrap_receipt=True).action_assign()
                    scrap_return_picking_id.with_context(scrap_receipt=True).button_validate()

            self.sudo().action_rma_send_email()

    def _compute_ticket_count(self):
        for record in self:
            ticket_count = self.env['helpdesk.ticket'].search_count([
                ('id', '=', record.helpdesk_ticket_id.id)
            ])
            record.helpdesk_ticket_count = ticket_count

    def _compute_scrap_count(self):
        for record in self:
            scrap_count = self.env['stock.scrap'].search_count(
                [('claim_id', '=', record.id)])
            record.scrap_count = scrap_count

    def _compute_delivery_count(self):
        for rec in self:
            rec.delivery_count = len(rec.to_return_picking_ids.ids)

    def _compute_receipt_count(self):
        for rec in self:
            picking_receipts = self.env['stock.picking'].search_count([('claim_id', '=', rec.id)])
            rec.receipt_count = picking_receipts

    def action_see_move_scrap(self):
        """This action used to display the scrap on the RMA."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Scrap'),
            'res_model': 'stock.scrap',
            'view_mode': 'tree,form',
            'domain': [('claim_id', '=', self.id)],
            'context': {
                'default_claim_id': self.id
            },
        }

    def action_see_helpdesk_ticket(self):
        ticket_id = self.env['helpdesk.ticket'].search([
            ('id', '=', self.helpdesk_ticket_id.id)
        ], limit=1)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Helpdesk Ticket'),
            'res_model': 'helpdesk.ticket',
            'view_mode': 'form',
            'res_id': ticket_id.id,
        }

    def get_rma_scrap_location(self):
        location_id = self.env['stock.location'].search([
            ('company_id', '=', self.env.company.id), ('rma_scrap_location', '=', True), ('scrap_location', '=', True)
        ])
        if not location_id:
            raise ValidationError(_(
                "The scrap location has not been configured."
            ))
        return location_id

    def create_scrap(self, claim_lines):
        for line in claim_lines:

            lot_id = False
            if line.claim_id.is_legacy_order:
                    srch_lot = self.env['stock.lot'].search(
                        [('product_id',  '=', line.product_id.id)], limit=1)
                    if srch_lot:
                        lot_id = srch_lot.id
            else:
                lot_id = line.serial_lot_ids[0].id if line.serial_lot_ids else False

            scrap_id = self.env['stock.scrap'].create({
                'product_id': line.product_id.id,
                'scrap_qty': line.quantity,
                'lot_id': lot_id,
                'location_id': self.picking_id.location_id.id if not self.is_legacy_order else self.location_id.id,
                'scrap_location_id': self.get_rma_scrap_location().id,
                'claim_id': self.id
            })
            picking_id = self.env['stock.picking'].search([('claim_id', '=', line.claim_id.id)], limit=1)
            if picking_id:
                move_line_id = self.env['stock.move.line'].search([
                    ('picking_id', '=', picking_id.id), ('product_id', '=', line.product_id.id)
                ], limit=1)
                if move_line_id:
                    scrap_id.lot_id = move_line_id.lot_id.id
                    scrap_id.action_validate()

    def create_refund(self, claim_lines):
        """This method used to create a refund."""
        if self.is_legacy_order:
            if claim_lines:
                refund_invoice_ids = self.create_refund_invoice_for_legacy_order(claim_lines)
                if refund_invoice_ids:
                    self.write({'refund_invoice_ids': [(6, 0, refund_invoice_ids.ids)]})
        else:
            return super().create_refund(claim_lines)

    def create_refund_invoice_for_legacy_order(self, claim_lines):
        invoice_obj = self.env['account.move']
        invoice_line_obj = self.env['account.move.line']
        journal_id = self.env['account.journal'].search(
            ['|', ('company_id', '=', False), ('company_id', 'in', self.env.company.ids), ('type', 'in', ['sale'])], limit=1
        )

        refund_invoice = invoice_obj.create({
            'partner_id': self.partner_id.id,
            'partner_shipping_id': self.partner_delivery_id.id,
            'move_type': 'out_refund',
            'ref': 'Refund Process of Claim - ' + self.name,
            'journal_id': journal_id.id,
            'claim_id': self.id
        })

        if refund_invoice:
            for line in claim_lines:
                invoice_line_obj.create({
                    'product_id': line.product_id.id,
                    'quantity': line.quantity,
                    'move_id': refund_invoice.id
                })

        return refund_invoice

    def process_claim(self):
        """This method used to process a claim."""
        simple_receipt = self.env['stock.picking'].search_count([
            ('claim_id', '=', self.id), ('picking_type_code', '=', 'incoming'), ('state', '!=', 'cancel')
        ])
        done_receipt = self.env['stock.picking'].search_count([
            ('claim_id', '=', self.id), ('picking_type_code', '=', 'incoming'), ('state', '=', 'done')
        ])
        if simple_receipt != done_receipt:
            raise ValidationError(
                "You cannot process the RMA ticket until the warehouse receives the products and clears any pending receiving."
            )
        self.check_validate_claim()

        refund_lines, do_lines, so_lines, ro_lines,\
            scrap_lines = self.prepare_list_based_on_line_operations()

        if refund_lines:
            self.create_refund(refund_lines)
        if do_lines:
            self.create_do(do_lines)
        if so_lines:
            self.create_so(so_lines)
        if ro_lines:
            self.create_ro(ro_lines)
        if scrap_lines:
            self.create_scrap(scrap_lines)

        self.write({'state': 'close'})
        self.sudo().action_rma_send_email()

    def prepare_list_based_on_line_operations(self):
        """
        This method is used prepare list of all related operations
        Return: refund_lines, do_lines, so_lines, ro_lines
        """
        refund_lines = []
        do_lines = []
        so_lines = []
        ro_lines = []
        scrap_lines = []

        for line in self.claim_line_ids:
            self.check_validate_claim_lines(line)
            if line.claim_type == 'repair':
                ro_lines.append(line)
            if line.claim_type == 'refund':
                refund_lines.append(line)
            if line.claim_type == 'refund_scrap':
                scrap_lines.append(line)
                refund_lines.append(line)
            if line.claim_type in ['replace_same_scrap_product', 'replace_other_scrap_product']:
                scrap_lines.append(line)
            if line.claim_type == 'replace_other_product':
                if line.is_create_invoice:
                    so_lines.append(line)
                    refund_lines.append(line)

        return refund_lines, do_lines, so_lines, ro_lines, scrap_lines

    def prepare_list_replace_other_product(self):
        refund_lines = []
        do_lines = []
        so_lines = []
        for line in self.claim_line_ids:
            self.check_validate_claim_lines(line)
            if line.claim_type == 'replace_same_product' or line.claim_type == 'replace_same_scrap_product':
                do_lines.append(line)
            if line.claim_type in ['replace_other_product', 'replace_other_scrap_product']:
                do_lines.append(line)

        return refund_lines, do_lines, so_lines

    def check_validate_claim_lines(self, line):
        """This method is used to check claim Lines is validate or not"""

        if self.return_picking_id and self.return_picking_id.state == 'done' \
                and not line.claim_type:
            raise UserError(
                _("Please set RMA Workflow Action for all rma lines."))
        if self.is_rma_without_incoming and not line.claim_type:
            raise UserError(
                _("Please set RMA Workflow Action for all rma lines."))
        if line.claim_type in ['replace_other_product', 'replace_other_scrap_product'] and (
                not line.to_be_replace_product_id or line.to_be_replace_quantity <= 0):
            raise UserError(_(
                "Claim line with product %s has Replace product or "
                "Replace quantity or both not set.") % (line.product_id.name))

    def _prepare_procurement_group_vals(self):
        vals = super()._prepare_procurement_group_vals()
        if self.is_legacy_order:
            vals['move_type'] = 'direct'
        return vals

    def _prepare_procurement_values(self, group_id):
        vals = super()._prepare_procurement_values(group_id)
        if self.is_legacy_order:
            vals['warehouse_id'] = self.location_id.warehouse_id
        return vals

    def prepare_sale_order_values(self):
        vals = super().prepare_sale_order_values()
        if self.is_legacy_order:
            vals['warehouse_id'] = self.location_id.warehouse_id.id
        return vals

    def create_ro(self, claim_lines):
        """This method is used to create repair order"""
        repair_order_obj = self.env["repair.order"]
        for line in claim_lines:
            repair_order_list = []
            default_lot_id = False
            if line.product_id.tracking == 'serial':
                for lot_id in line.serial_lot_ids:
                    repair_order_dict = self.prepare_repair_order_dis(
                        claim_line=line, qty=1)
                    sale_order_id = self.sale_id.id if self.sale_id else False
                    repair_order_dict.update({
                        'lot_id': lot_id.id,
                        'sale_order_id': sale_order_id,
                        'picking_type_id': self._default_picking_type_id().id
                    })
                    repair_order_list.append(repair_order_dict)
            else:
                qty = line.done_qty if line.return_qty == 0.0 else line.return_qty
                repair_order_dict = self.prepare_repair_order_dis(
                    claim_line=line, qty=qty)
                if line.claim_id.is_legacy_order:
                    default_lot_id = self.env['stock.lot'].search(
                        [('product_id',  '=', line.product_id.id)], limit=1)
                if line.product_id.tracking == 'lot':
                    repair_order_dict.update({
                        'lot_id': line.serial_lot_ids[0].id if not default_lot_id else default_lot_id.id,
                        'picking_type_id': self._default_picking_type_id().id
                    })
                repair_order_list.append(repair_order_dict)
            repair_order_obj.create(repair_order_list)

    def create_do(self, claim_lines):
        """based on this method to create a picking one..two or three step."""
        procurements = []

        vals = self._prepare_procurement_group_vals()

        group_id = self.env['procurement.group'].create(vals)

        for line in claim_lines:
            values = self._prepare_procurement_values(group_id)
            values['delivery_note'] = line.delivery_note
            values['route_ids'] = line.route_id
            qty = line.to_be_replace_quantity or line.quantity
            product_id = line.to_be_replace_product_id or line.product_id
            procurements.append(self.env['procurement.group'].Procurement(
                product_id, qty, product_id.uom_id,
                self.partner_delivery_id.property_stock_customer, self.name,
                self.code, self.company_id, values))

        if procurements:
            ctx = dict(self.env.context,
                       rma_out_sale_warehouse=self.sale_id.warehouse_id if not self.is_legacy_order else self.location_id.warehouse_id)
            self.env['procurement.group'].with_context(clean_context(ctx)).run(
                procurements)

        pickings = self.env['stock.picking'].search(
            [('group_id', '=', group_id.id)])
        self.write({'to_return_picking_ids': [(6, 0, pickings.ids)]})
        pickings[-1].action_assign()

    @staticmethod
    def prepare_values_for_return_picking_line(line, return_picking_wizard, move_id):
        """This method Used to prepare values for return picking line."""
        return {
            'product_id': line.product_id.id,
            'quantity': line.quantity,
            'wizard_id': return_picking_wizard.id,
            'move_id': move_id[0].id if move_id else False,
            'claim_line_id': line.id,
        }

    def scrap_create_return_picking(self, claim_lines=False):
        """
        This method used to create a scrap return picking, when the approve button clicks on the RMA.
        """
        if self.claim_line_ids.filtered(lambda l: l.claim_type in ['refund_scrap', 'replace_other_scrap_product', 'replace_same_scrap_product']):
            return_picking_id = True
            location_id = self.location_id.id
            vals = {
                'picking_id': self.return_picking_id.id if claim_lines else self.picking_id.id}
            active_id = self.return_picking_id.id if claim_lines else self.picking_id.id
            return_picking_wizard = self.env['stock.return.picking'].with_context(
                active_id=active_id, rma_sale_warehouse=self.sale_id.warehouse_id).create(vals)
            return_picking_wizard._compute_moves_locations()
            if location_id and not claim_lines:
                return_picking_wizard.write({'location_id': location_id})
            return_lines = self.create_scrap_return_picking_lines(
                claim_lines, return_picking_wizard)
            return_picking_wizard.write(
                {'product_return_moves': [(6, 0, return_lines)]})
            if self.is_legacy_order:
                new_picking_id, pick_type_id = return_picking_wizard.with_context(
                    rma_id=self, rma_scrap_pciking=True)._create_returns()
            else:
                new_picking_id, pick_type_id = return_picking_wizard.with_context(no_legacy_order=True, rma_scrap_pciking=True)._create_returns()
            if claim_lines:
                self.write({'to_return_picking_ids': [(4, new_picking_id)]})
            else:
                return_picking_id = self.create_scrap_move_lines(new_picking_id)
            return return_picking_id

    def create_return_picking(self, claim_lines=False):
        """
        This method used to create a return picking, when the approve button clicks on the RMA.
        """
        if self.claim_line_ids.filtered(lambda l: l.claim_type not in ['refund_scrap', 'replace_other_scrap_product', 'replace_same_scrap_product']):
            return_picking_id = True
            location_id = self.location_id.id
            vals = {
                'picking_id': self.return_picking_id.id if claim_lines else self.picking_id.id}
            active_id = self.return_picking_id.id if claim_lines else self.picking_id.id
            return_picking_wizard = self.env['stock.return.picking'].with_context(
                active_id=active_id, rma_sale_warehouse=self.sale_id.warehouse_id).create(vals)
            return_picking_wizard._compute_moves_locations()
            if location_id and not claim_lines:
                return_picking_wizard.write({'location_id': location_id})
            return_lines = self.create_return_picking_lines(
                claim_lines, return_picking_wizard)
            return_picking_wizard.write(
                {'product_return_moves': [(6, 0, return_lines)]})
            if self.is_legacy_order:
                new_picking_id, pick_type_id = return_picking_wizard.with_context(
                    rma_id=self, claim_lines=claim_lines)._create_returns()
            else:
                new_picking_id, pick_type_id = return_picking_wizard.with_context(no_legacy_order=True)._create_returns()
            if claim_lines:
                self.write({'to_return_picking_ids': [(4, new_picking_id)]})
            else:
                return_picking_id = self.create_move_lines(new_picking_id)
            return return_picking_id

    def create_scrap_return_picking_lines(self, claim_lines, return_picking_wizard):
        """This method is used to create scrap return picking"""
        return_lines = []
        lines = claim_lines or self.claim_line_ids
        for line in lines.filtered(lambda l: l.claim_type in ['refund_scrap', 'replace_other_scrap_product', 'replace_same_scrap_product']):
            move_id = self.env['stock.move'].search([
                ('product_id', '=', line.product_id.id),
                ('picking_id', '=',
                 self.return_picking_id.id if claim_lines else self.picking_id.id),
                ('sale_line_id', '=', line.move_id.sale_line_id.id), ('state', '!=', 'cancel')])
            return_line_values = self.prepare_values_for_return_picking_line(
                line, return_picking_wizard, move_id)

            return_line = self.env['stock.return.picking.line'].create(
                return_line_values)
            return_lines.append(return_line.id)
        return return_lines

    def create_return_picking_lines(self, claim_lines, return_picking_wizard):
        """This method is used to create return picking"""
        return_lines = []
        lines = claim_lines or self.claim_line_ids
        for line in lines.filtered(lambda l: l.claim_type not in ['refund_scrap', 'replace_other_scrap_product', 'replace_same_scrap_product']):
            move_id = self.env['stock.move'].search([
                ('product_id', '=', line.product_id.id),
                ('picking_id', '=',
                 self.return_picking_id.id if claim_lines else self.picking_id.id),
                ('sale_line_id', '=', line.move_id.sale_line_id.id), ('state', '!=', 'cancel')])
            return_line_values = self.prepare_values_for_return_picking_line(
                line, return_picking_wizard, move_id)

            return_line = self.env['stock.return.picking.line'].create(
                return_line_values)
            return_lines.append(return_line.id)
        return return_lines

    def create_move_lines(self, new_picking_id):
        """This method is used to create stock move lines."""
        self.write({'return_picking_id': new_picking_id})
        for claim_line in self.claim_line_ids.filtered(lambda l: l.claim_type != 'refund_scrap'):
            return_quantity = claim_line.quantity
            if claim_line.serial_lot_ids:
                # prepare lot wise dict of that processed move lines
                claim_line_by_lots = {}
                done_move_lines = claim_line.move_id.mapped('move_line_ids').filtered(
                    lambda l, claim_line=claim_line: l.product_id.id == claim_line.product_id.id)
                for done_move in done_move_lines:
                    move_line_lot = done_move.lot_id
                    done_qty = done_move.quantity
                    if not claim_line_by_lots.get(move_line_lot, False):
                        claim_line_by_lots.update({move_line_lot: done_qty})
                    else:
                        existing_amount = claim_line_by_lots.get(
                            move_line_lot, {})
                        claim_line_by_lots.update(
                            {move_line_lot: existing_amount + done_qty})

                # Will prepare an total processed quantity with selected lot/serial numbers into the claim line
                # to check is selected lot/number can fulfill the quantity to process for return
                processed_qty_by_lots = 0.0
                for serial_lot_id in claim_line.serial_lot_ids:
                    lot_quantity = claim_line_by_lots.get(serial_lot_id, 0.0)
                    processed_qty_by_lots += lot_quantity
                if not self.is_legacy_order:
                    if return_quantity > processed_qty_by_lots:
                        raise UserError(_("Please select proper Lots/Serial Numbers %s to process return for product %s. "
                                          "Selected Lots/Serial numbers not able to process for return because it is mismatch"
                                          " with return quantity" % (
                                              claim_line.serial_lot_ids.mapped('name'), claim_line.product_id.name)))

                # odoo create move lines with sequence so required to update move based on selected serial/lot numbers
                update_number_lines = self.return_picking_id.move_ids.mapped('move_line_ids').filtered(
                    lambda l,
                    claim_line=claim_line: l.product_id.id == claim_line.product_id.id and l.lot_id.id not in claim_line.serial_lot_ids.ids and l.quantity == 0.0)

                # process an return move lines
                return_move_lines = self.return_picking_id.move_ids.mapped('move_line_ids').filtered(
                    lambda l, claim_line=claim_line: l.product_id.id == claim_line.product_id.id)
                for serial_lot_id in claim_line.serial_lot_ids:
                    return_lot_move_lines = return_move_lines.filtered(
                        lambda l, serial_lot_id=serial_lot_id: l.lot_id.id == serial_lot_id.id)
                    if not return_lot_move_lines and update_number_lines:
                        update_number_lines = update_number_lines.filtered(
                            lambda l: l.quantity == 0.0)
                        return_move_line = update_number_lines[0]
                        return_move_line.write({'lot_id': serial_lot_id.id})
                    # else:
                    #     return_move_line = return_lot_move_lines[0]
                    # quantity = claim_line.done_qty if self.is_legacy_order else claim_line_by_lots.get(
                    #     return_move_line.lot_id)
                    # if quantity >= return_quantity:
                    #     return_move_line.write({'quantity': return_quantity})
                    #     break
                    # return_quantity -= quantity
                    # return_move_line.write({'quantity': quantity})
            else:
                return_move_lines = self.return_picking_id.move_ids.mapped('move_line_ids').filtered(
                    lambda l, claim_line=claim_line: l.product_id.id == claim_line.product_id.id)
                # return_move_line = return_move_lines[0]
                # return_move_line.write({'quantity': return_quantity})
        return self.return_picking_id

    def create_scrap_move_lines(self, new_picking_id):
        """This method is used to create stock move lines."""
        self.write({'return_picking_id': new_picking_id})
        for claim_line in self.claim_line_ids.filtered(lambda l: l.claim_type == 'refund_scrap'):
            return_quantity = claim_line.quantity
            if claim_line.serial_lot_ids:
                # prepare lot wise dict of that processed move lines
                claim_line_by_lots = {}
                done_move_lines = claim_line.move_id.mapped('move_line_ids').filtered(
                    lambda l, claim_line=claim_line: l.product_id.id == claim_line.product_id.id)
                for done_move in done_move_lines:
                    move_line_lot = done_move.lot_id
                    done_qty = done_move.quantity
                    if not claim_line_by_lots.get(move_line_lot, False):
                        claim_line_by_lots.update({move_line_lot: done_qty})
                    else:
                        existing_amount = claim_line_by_lots.get(
                            move_line_lot, {})
                        claim_line_by_lots.update(
                            {move_line_lot: existing_amount + done_qty})

                # Will prepare an total processed quantity with selected lot/serial numbers into the claim line
                # to check is selected lot/number can fulfill the quantity to process for return
                processed_qty_by_lots = 0.0
                for serial_lot_id in claim_line.serial_lot_ids:
                    lot_quantity = claim_line_by_lots.get(serial_lot_id, 0.0)
                    processed_qty_by_lots += lot_quantity
                if not self.is_legacy_order:
                    if return_quantity > processed_qty_by_lots:
                        raise UserError(_("Please select proper Lots/Serial Numbers %s to process return for product %s. "
                                          "Selected Lots/Serial numbers not able to process for return because it is mismatch"
                                          " with return quantity" % (
                                              claim_line.serial_lot_ids.mapped('name'), claim_line.product_id.name)))

                # odoo create move lines with sequence so required to update move based on selected serial/lot numbers
                update_number_lines = self.return_picking_id.move_ids.mapped('move_line_ids').filtered(
                    lambda l,
                    claim_line=claim_line: l.product_id.id == claim_line.product_id.id and l.lot_id.id not in claim_line.serial_lot_ids.ids and l.quantity == 0.0)

                # process an return move lines
                return_move_lines = self.return_picking_id.move_ids.mapped('move_line_ids').filtered(
                    lambda l, claim_line=claim_line: l.product_id.id == claim_line.product_id.id)
                for serial_lot_id in claim_line.serial_lot_ids:
                    return_lot_move_lines = return_move_lines.filtered(
                        lambda l, serial_lot_id=serial_lot_id: l.lot_id.id == serial_lot_id.id)
                    if not return_lot_move_lines and update_number_lines:
                        update_number_lines = update_number_lines.filtered(
                            lambda l: l.quantity == 0.0)
                        return_move_line = update_number_lines[0]
                        return_move_line.write({'lot_id': serial_lot_id.id})
                    # else:
                    #     return_move_line = return_lot_move_lines[0]
                    # quantity = claim_line.done_qty if self.is_legacy_order else claim_line_by_lots.get(
                    #     return_move_line.lot_id)
                    # if quantity >= return_quantity:
                    #     return_move_line.write({'quantity': return_quantity})
                    #     break
                    # return_quantity -= quantity
                    # return_move_line.write({'quantity': quantity})
            else:
                return_move_lines = self.return_picking_id.move_ids.mapped('move_line_ids').filtered(
                    lambda l, claim_line=claim_line: l.product_id.id == claim_line.product_id.id)
                # return_move_line = return_move_lines[0]
                # return_move_line.write({'quantity': return_quantity})
        return self.return_picking_id

    def show_return_picking(self):
        """
        This action used to display the receipt on the RMA.
        """
        receipt_pickings = self.env['stock.picking'].search([('claim_id', '=', self.id)])
        if len(receipt_pickings) == 1:
            receipt_picking_action = {
                'name': "Receipt",
                'view_mode': 'form',
                'res_model': 'stock.picking',
                'type': 'ir.actions.act_window',
                'res_id': receipt_pickings.id
            }
        else:
            receipt_picking_action = {
                'name': "Receipt",
                'view_mode': 'tree,form',
                'res_model': 'stock.picking',
                'type': 'ir.actions.act_window',
                'domain': [('id', 'in', receipt_pickings.ids)]
            }
        return receipt_picking_action

    def set_to_draft(self):
        """This method used to set claim into the draft state."""
        return_picking_ids = self.env['stock.picking'].search(
            [('claim_id', '=', self.id)])
        if return_picking_ids:
            for rec in return_picking_ids:
                if rec.state != 'draft':
                    if rec.state == 'done':
                        raise UserError(_("Claim cannot be move draft state once "
                                          "it Receipt is done or cancel."))
                    rec.action_cancel()
                else:
                    rec.unlink()

        if self.internal_picking_id and self.internal_picking_id.state != 'draft':
            self.internal_picking_id.action_cancel()
        self.write({'state': 'draft'})


class RmaClaimLine(models.Model):
    _inherit = 'claim.line.ept'

    claim_type = fields.Selection(
        selection_add=[
            ('refund_scrap', 'Refund with Scrap'),
            ('replace_same_scrap_product', 'Replace with Same Product and Scrap (No Refund)'),
            ('replace_other_scrap_product', 'Replace with Other Product and Scrap (No Refund)')
        ],
        ondelete={
            'refund_scrap': 'cascade',
            'replace_same_scrap_product': 'cascade',
            'replace_other_scrap_product': 'cascade',
        }
    )
    rma_reason_id = fields.Many2one(
        'rma.reason.ept', string="RMA Workflow"
    )
    legacy_done_qty = fields.Float('Delivered Quantity')
    legacy_claim_id = fields.Many2one(
        'crm.claim.ept', string='Related claim', copy=False, ondelete='cascade'
    )
    receipt_note = fields.Text(string="Receipt Note")
    delivery_note = fields.Text(string="Delivery Note")
    route_id = fields.Many2one(
        'stock.route', string='Route', domain=[('sale_selectable', '=', True)], ondelete='restrict'
    )

    sale_line_id = fields.Many2one('sale.order.line')

    def _compute_get_done_quantity(self):
        """
        This method used to set done qty in claim line base on the delivered picking qty.
        """
        for record in self:
            if not record.claim_id.is_legacy_order:
                record.done_qty = record.move_id.quantity
            else:
                record.done_qty = record.legacy_done_qty

    @api.constrains('quantity')
    def check_qty(self):
        if self.claim_id and not self.claim_id.is_legacy_order:
            return super().check_qty()

    @api.onchange('legacy_done_qty')
    def _onchange_done_qty(self):
        self.quantity = self.legacy_done_qty

    def action_claim_refund_process_ept(self):
        """
        This action used to return the product from the claim line base on return action.
        """
        return {
            'name': 'Return Products',
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'claim.process.wizard',
            'src_model': 'claim.line.ept',
            'target': 'new',
            'context': {
                'product_id': self.product_id.id,
                'hide': True,
                'hide_create_invoice': True if self.claim_type == 'replace_other_scrap_product' else False,
                'claim_line_id': self.id
            }
        }


class RmaReason(models.Model):
    _inherit = 'rma.reason.ept'

    action = fields.Selection(
        selection_add=[
            ('refund_scrap', 'Refund with Scrap'),
            ('replace_same_scrap_product', 'Replace with Same Product and Scrap (No Refund)'),
            ('replace_other_scrap_product', 'Replace with Other Product and Scrap (No Refund)')
        ],
        ondelete={
            'refund_scrap': 'cascade',
            'replace_same_scrap_product': 'cascade',
            'replace_other_scrap_product': 'cascade',
        }
    )
    active = fields.Boolean(string="Active", default=True)

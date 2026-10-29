# -*- coding: utf-8 -*-

from odoo import fields, models, api, _
from odoo.exceptions import ValidationError
from odoo.fields import Command, Domain


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    is_rma_in = fields.Boolean(string="RMA In", related="picking_type_id.is_rma_in")
    is_rma_out = fields.Boolean(string="RMA Out", related="picking_type_id.is_rma_out")
    quality_check_id = fields.Many2one('quality.check', string="Quality Check")
    internal_delivery_note = fields.Html('Internal Delivery Note')

    @api.model_create_multi
    def create(self, vals_list):
        rma_out_sale_warehouse = self.env.context.get('rma_out_sale_warehouse')
        claim_id = self.env.context.get('claim_id')
        for vals in vals_list:
            if rma_out_sale_warehouse:
                vals['picking_type_id'] = rma_out_sale_warehouse.rma_out_type_id.id
            if claim_id:
                vals['claim_id'] = claim_id.id
        return super().create(vals_list)

    def _change_location(self):
        picking = self.with_company(self.company_id)
        if picking.picking_type_id:
            if picking.picking_type_id.default_location_src_id:
                location_id = picking.picking_type_id.default_location_src_id.id
            elif picking.partner_id:
                location_id = picking.partner_id.property_stock_supplier.id
            else:
                _customerloc, location_id = self.env['stock.warehouse']._get_partner_locations()

            if picking.picking_type_id.default_location_dest_id:
                location_dest_id = picking.picking_type_id.default_location_dest_id.id
            elif picking.partner_id:
                location_dest_id = picking.partner_id.property_stock_customer.id
            else:
                location_dest_id, _supplierloc = self.env['stock.warehouse']._get_partner_locations()
            picking.location_id = location_id
            picking.location_dest_id = location_dest_id

    def _prepare_return_picking_default_values(self):
        """Apply RMA In operation type / claim when creating returns (Odoo 20 API)."""
        if self.env.context.get('rma_id'):
            rma_id = self.env.context.get('rma_id')
            claim_lines = self.env.context.get('claim_lines')
            rma_in_type = rma_id.location_id.warehouse_id.rma_in_type_id
            location_src = rma_in_type.default_location_src_id
            return {
                'move_ids': [],
                'picking_type_id': rma_in_type.id,
                'state': 'draft',
                'claim_id': rma_id.id if not claim_lines else False,
                'partner_id': rma_id.partner_id.id,
                'origin': _("Return of %s", rma_id.name),
                'location_dest_id': rma_id.location_id.id,
                'location_id': location_src.id if location_src else self.location_dest_id.id,
                'user_id': False,
            }
        vals = super()._prepare_return_picking_default_values()
        if self.env.context.get('rma_sale_warehouse'):
            rma_in_type_id = self.env.context.get('rma_sale_warehouse').rma_in_type_id
            vals['picking_type_id'] = rma_in_type_id.id
            if rma_in_type_id.default_location_src_id:
                vals['location_id'] = rma_in_type_id.default_location_src_id.id
            if rma_in_type_id.default_location_dest_id:
                vals['location_dest_id'] = rma_in_type_id.default_location_dest_id.id
        claim_id = self.env.context.get('claim_id')
        if claim_id:
            vals['claim_id'] = claim_id.id if not isinstance(claim_id, int) else claim_id
        return vals

    def _prepare_return_move_default_values(self, move):
        vals = super()._prepare_return_move_default_values(move)
        claim = self.env.context.get('claim_id') or self.claim_id
        if claim and not isinstance(claim, models.BaseModel):
            claim = self.env['crm.claim.ept'].browse(claim)
        if claim:
            claim_line = claim.claim_line_ids.filtered(
                lambda l: l.move_id.id == move.id or (
                    l.product_id.id == move.product_id.id
                    and l.move_id.sale_line_id.id == move.sale_line_id.id)
            )[:1]
            if claim_line and claim_line.receipt_note:
                vals['receipt_note'] = claim_line.receipt_note
        return vals


class StockPickingType(models.Model):
    _inherit = 'stock.picking.type'

    is_rma_in = fields.Boolean(string="RMA In")
    is_rma_out = fields.Boolean(string="RMA Out")

    @api.constrains('is_rma_in', 'is_rma_out')
    def _check_rma_in_out(self):
        if self.is_rma_in and self.is_rma_out:
            raise ValidationError(_(
                "It is not possible to enable both RMA IN and RMA OUT simultaneously."
            ))


class StockWarehouse(models.Model):
    _inherit = 'stock.warehouse'

    rma_in_type_id = fields.Many2one(
        'stock.picking.type', string='RMA In Type', check_company=True
    )
    rma_out_type_id = fields.Many2one(
        'stock.picking.type', string='RMA Out Type', check_company=True
    )
    manu_refurbish_type_id = fields.Many2one(
        'stock.picking.type', string='Manufacturing Refurbish Type', check_company=True
    )

    def _get_sequence_values(self, name=False, code=False):

        values = super(StockWarehouse, self)._get_sequence_values(name=name, code=code)
        name = name if name else self.name
        code = code if code else self.code

        values.update({
            'rma_in_type_id': {
                'name': name + ' ' + _('Sequence rma in'),
                'prefix': code + '/RMA/IN/', 'padding': 5,
                'company_id': self.company_id.id,
            },
            'rma_out_type_id': {
                'name': name + ' ' + _('Sequence rma out'),
                'prefix': code + '/RMA/OUT/', 'padding': 5,
                'company_id': self.company_id.id,
            },
            'manu_refurbish_type_id': {
                'name': name + ' ' + _('Sequence rma out'),
                'prefix': code + '/MO/', 'padding': 5,
                'company_id': self.company_id.id,
            },
        })

        return values

    def _get_picking_type_create_values(self, max_sequence):

        data, next_sequence = super(
            StockWarehouse, self
        )._get_picking_type_create_values(max_sequence)

        # Do not set location fields to False here: in Odoo 20 both
        # default_location_src_id and default_location_dest_id are required,
        # and create values overwrite update values when creating types.
        data.update({
            'rma_in_type_id': {
                'name': _('RMA Receipts'),
                'code': 'incoming',
                'use_existing_lots': False,
                'sequence': max_sequence + 1,
                'sequence_code': 'RMA/IN',
                'is_rma_in': True,
                'company_id': self.company_id.id,
            }, 'rma_out_type_id': {
                'name': _('RMA Delivery Orders'),
                'code': 'outgoing',
                'use_create_lots': False,
                'sequence': max_sequence + 5,
                'sequence_code': 'RMA/OUT',
                'print_label': True,
                'is_rma_out': True,
                'company_id': self.company_id.id,
            }, 'manu_refurbish_type_id': {
                'name': _('Manufacturing Refurbish'),
                'code': 'mrp_operation',
                'use_create_lots': False,
                'use_existing_lots': True,
                'sequence': max_sequence + 5,
                'sequence_code': 'MO/REF',
                'print_label': True,
                'company_id': self.company_id.id,
            }
        })

        return data, max_sequence + 5

    def _get_picking_type_update_values(self):

        data = super(StockWarehouse, self)._get_picking_type_update_values()

        input_loc, output_loc = self._get_input_output_locations(
            self.reception_steps, self.delivery_steps
        )
        customer_loc, supplier_loc = self._get_partner_locations()

        data.update({
            'rma_in_type_id': {
                'default_location_src_id': supplier_loc.id,
                'default_location_dest_id': input_loc.id,
                'barcode': self.code.replace(" ", "").upper() + "-RMA RECEIPTS",
            },
            'rma_out_type_id': {
                'default_location_src_id': output_loc.id,
                'default_location_dest_id': customer_loc.id,
                'barcode': self.code.replace(" ", "").upper() + "-RMA DELIVERY",
            },
            'manu_refurbish_type_id': {
                'barcode': self.code.replace(" ", "").upper() + "-MANUFACTURING",
                'default_location_src_id': self.manufacture_steps in ('pbm', 'pbm_sam') and self.pbm_loc_id.id or self.lot_stock_id.id,
                'default_location_dest_id': self.manufacture_steps == 'pbm_sam' and self.sam_loc_id.id or self.lot_stock_id.id
            },
        })
        return data


class StockLocation(models.Model):
    _inherit = 'stock.location'

    rma_scrap_location = fields.Boolean(string="Is a RMA Scrap Location?")

    @api.constrains('rma_scrap_location', 'usage')
    def _check_rma_scrap_location(self):
        for loc in self:
            if loc.rma_scrap_location and loc.usage != 'inventory':
                raise ValidationError(_(
                    "RMA scrap location must have Location Type 'Inventory Loss'."
                ))
        rma_scrap_location = self.search_count([
            ('company_id', '=', self.env.company.id), ('rma_scrap_location', '=', True)
        ])
        if rma_scrap_location > 1:
            raise ValidationError(_(
                "A single company can have only one RMA scrap location enabled."
            ))


class StockMove(models.Model):
    _inherit = 'stock.move'

    receipt_note = fields.Text(string="Receipt Note")
    delivery_note = fields.Text(string="Delivery Note")
    claim_id = fields.Many2one('crm.claim.ept', string="RMA Claim", copy=False)
    quality_check_id = fields.Many2one('quality.check', string="Quality Check")
    rma_count = fields.Integer('RMA Claims', compute='_compute_rma_count')

    def _compute_rma_count(self):
        for rec in self:
            rec.rma_count = self.env['crm.claim.ept'].search_count(
                [('id', '=', rec.claim_id.id)]) if rec.claim_id else 0

    def action_view_rma(self):
        rma = self.env['crm.claim.ept'].search([('id', '=', self.claim_id.id)])

        if len(rma) == 1:
            claim_action = {
                'name': "RMA",
                'view_mode': 'form',
                'res_model': 'crm.claim.ept',
                'type': 'ir.actions.act_window',
                'res_id': rma.id,
                'context': {
                    'create': False
                }
            }
        else:
            claim_action = {
                'name': "RMA",
                'view_mode': 'list,form',
                'res_model': 'crm.claim.ept',
                'type': 'ir.actions.act_window',
                'domain': [('id', 'in', rma.ids)],
                'context': {
                    'create': False
                }
            }

        return claim_action

    def _prepare_move_line_vals(self, quantity=None, reserved_quant=None):
        vals = super()._prepare_move_line_vals(quantity, reserved_quant)
        mrp_id = self.raw_material_production_id
        if mrp_id.quality_check_id:
            move_line_id = mrp_id.quality_check_id.move_line_id
            vals['lot_id'] = move_line_id.lot_id.id
        if self.picking_id.quality_check_id:
            move_line_id = self.picking_id.quality_check_id.move_line_id
            vals['lot_id'] = move_line_id.lot_id.id
        return vals

    def _push_apply(self):
        if not self.env.context.get('scrap_receipt'):
            return super()._push_apply()
        else:
            return False


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    def _filter_move_lines_applicable_for_quality_check(self):
        move_line = super()._filter_move_lines_applicable_for_quality_check()
        StockMoveLine = self.env['stock.move.line']
        if move_line:
            for line in move_line:
                claim_id = line.move_id.picking_id.claim_id
                if claim_id:
                    claim_line_id = self.env['claim.line.ept'].search([
                        ('claim_id', '=', claim_id.id), ('product_id', '=', line.product_id.id),
                        ('claim_type', '=', 'refund_scrap')], limit=1
                    )
                    if claim_line_id:
                        StockMoveLine += line
        return move_line - StockMoveLine


class StockRule(models.Model):
    _inherit = 'stock.rule'

    def _push_prepare_move_copy_values(self, move_to_copy):
        vals = super()._push_prepare_move_copy_values(move_to_copy)
        if self.env.context.get('rma_reference'):
            vals['reference_ids'] = [Command.set(self.env.context.get('rma_reference').ids)]
            vals['receipt_note'] = move_to_copy.receipt_note
        return vals

    def _get_stock_move_values(self, product_id, product_qty, uom_id, location_dest_id, name, origin, company_id, values):
        move_values = super()._get_stock_move_values(
            product_id, product_qty, uom_id, location_dest_id, name, origin, company_id, values)
        if values.get('delivery_note'):
            move_values['delivery_note'] = values.get('delivery_note', False)
        return move_values

    def _search_rule(self, route_ids, packaging_uom_id, product_id, warehouse_id, domain):
        """Prefer warehouse routes when creating RMA-linked transfers."""
        if self.env.context.get('rma_reference') and warehouse_id:
            warehouse_routes = warehouse_id.route_ids
            if warehouse_routes:
                return self.env['stock.rule'].search(
                    Domain('route_id', 'in', warehouse_routes.ids) & Domain(domain),
                    order='route_sequence, sequence',
                    limit=1,
                )
            return self.env['stock.rule']
        return super()._search_rule(route_ids, packaging_uom_id, product_id, warehouse_id, domain)

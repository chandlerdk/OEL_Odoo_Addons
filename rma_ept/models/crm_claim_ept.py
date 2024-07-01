# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

from odoo import fields, models, api, _
from odoo.exceptions import UserError
from odoo.tools.misc import clean_context
from odoo.tools.misc import groupby


RES_PARTNER = 'res.partner'
SALE_ORDER = 'sale.order'
STOCK_PICKING = 'stock.picking'
ACCOUNT_MOVE = 'account.move'
CRM_CLAIM_EPT = 'crm.claim.ept'
IR_WINDOW_ACTION = 'ir.actions.act_window'
PROCUREMENT_GROUP = 'procurement.group'


class CrmClaimEpt(models.Model):
    _name = "crm.claim.ept"
    _description = 'RMA CRM Claim'
    _order = "priority,date desc"
    _inherit = ['mail.thread']

    active = fields.Boolean(default=True)
    rma_send = fields.Boolean(string="RMA Send")
    is_rma_without_incoming = fields.Boolean(string="Is RMA Without Incoming")

    code = fields.Char(string='RMA Number', default="New",
                       readonly=True, copy=False)
    name = fields.Char(string='Subject', required=True)
    action_next = fields.Char(string='Next Action', copy=False)
    user_fault = fields.Char(string='Trouble Responsible')
    email_from = fields.Char(string='Email', size=128,
                             help="Destination email for email gateway.")
    partner_phone = fields.Char(string='Phone')

    description = fields.Text()
    resolution = fields.Text(copy=False)
    cause = fields.Text(string='Root Cause')
    email_cc = fields.Text(string='Watchers Emails', help="These email addresses will be added to\
                                                           the CC field of all inbound and outbound\
                                                           emails for this record before being sent.\
                                                           Separate multiple email addresses with \
                                                           a comma")

    date_deadline = fields.Date(string='Deadline', copy=False)
    date_action_next = fields.Datetime(string='Next Action Date', copy=False)
    create_date = fields.Datetime(
        string='Creation Date', readonly=True, copy=False)
    write_date = fields.Datetime(
        string='Update Date', readonly=True, copy=False)
    date_closed = fields.Datetime(string='Closed', readonly=True, copy=False)
    date = fields.Datetime(default=fields.Datetime.now, copy=False)
    priority = fields.Selection(
        [('0', 'Low'), ('1', 'Normal'), ('2', 'High')], default='1')
    state = fields.Selection([
        ('draft', 'Draft'), ('approve', 'Approved'), ('process', 'Processing'),
        ('close', 'Closed'), ('reject', 'Rejected')], default='draft', copy=False, tracking=True)
    type_action = fields.Selection([('correction', 'Corrective Action'),
                                    ('prevention', 'Preventive Action')],
                                   string='Action Type')

    user_id = fields.Many2one('res.users', string='Responsible', tracking=True,
                              default=lambda self: self.env.user)
    section_id = fields.Many2one('crm.team', string='Sales Channel', index=True,
                                 help="Responsible sales channel. Define Responsible"
                                      " user and Email account for\
                                       mail gateway.")
    company_id = fields.Many2one('res.company', string='Company',
                                 default=lambda self: self.env.company)

    partner_id = fields.Many2one(RES_PARTNER, string='Partner')
    parent_partner_id = fields.Many2one(RES_PARTNER, string='Parent Partner',
                                        related='partner_id.parent_id')
    invoice_id = fields.Many2one("account.move", string="Invoice", copy=False)
    sale_id = fields.Many2one(SALE_ORDER, string="Sale Order", copy=False)
    reject_message_id = fields.Many2one(
        "claim.reject.message", string="Reject Reason", copy=False)
    new_sale_id = fields.Many2one(
        SALE_ORDER, string='New Sale Order', copy=False)

    location_id = fields.Many2one('stock.location', string='Return Location',
                                  domain=[('usage', '=', 'internal'), ('return_location', '=', True)])
    internal_picking_id = fields.Many2one(
        STOCK_PICKING, string='Internal Delivery Order', copy=False)
    picking_id = fields.Many2one(STOCK_PICKING, string='Delivery Order')
    return_picking_id = fields.Many2one(
        STOCK_PICKING, string='Return Delivery Order', copy=False)
    rma_support_person_id = fields.Many2one(
        RES_PARTNER, string='Contact Person')
    partner_delivery_id = fields.Many2one(
        RES_PARTNER, string='Delivery Address')
    move_product_ids = fields.Many2many('product.product', string="Products",
                                        compute='_compute_move_product_ids')
    to_return_picking_ids = fields.Many2many(STOCK_PICKING, string='Return Delivery Orders',
                                             default=False, copy=False)
    refund_invoice_ids = fields.Many2many(
        ACCOUNT_MOVE, string='Refund Invoices', copy=False)
    claim_lot_ids = fields.Many2many('stock.lot', compute='_compute_lot_ids')
    repair_order_ids = fields.One2many(
        'repair.order', 'claim_id', string='Repairs')
    claim_line_ids = fields.One2many(
        "claim.line.ept", "claim_id", string="Return Line")
    repairs_count = fields.Integer(compute='_compute_repairs_count')

    @api.depends('picking_id')
    def _compute_move_product_ids(self):
        """This method is used to select product id in claim line its compute
         based on picking's move line"""
        move_products = []
        for record in self:
            record.move_product_ids = [(6, 0, move_products)]
            if record.picking_id:
                products = record.picking_id.mapped(
                    'move_ids').mapped('product_id')
                move_products = products.ids
            record.move_product_ids = [(6, 0, move_products)]

    @api.onchange('is_rma_without_incoming')
    def onchange_is_rma_without_incoming(self):
        """This method will notify about return transfer will not process and product needs to return to rapair it"""
        warning = False
        repair_lines = self.claim_line_ids.filtered(
            lambda l: l.rma_reason_id and l.rma_reason_id.action == 'repair')
        if repair_lines and self.is_rma_without_incoming:
            warning_msg = {
                'title': _('Recommendation'),
                'message': "We recommend if you select repair action then we will need "
                           "return shipment."
                           "It will not create a return delivery of the repair order."
            }
            warning = {'warning': warning_msg}
        return warning

    @api.onchange('picking_id')
    def onchange_picking_id(self):
        """This method is used to set default values in the RMA base on delivery changes."""
        claim_lines = []
        if self.picking_id:
            returned_qty = 0
            self.change_field_based_on_picking()
            for move_id in self.picking_id.move_ids:
                # This previous_claimline_ids is checking that already claim is created for this product based
                # on previous claim we can add returned_qty on created claim
                previous_claimline_ids = self.env['claim.line.ept'].search([
                    ('move_id', '=', move_id.id), ('product_id', '=', move_id.product_id.id)])
                if previous_claimline_ids:
                    returned_qty = 0
                    for line_id in previous_claimline_ids:
                        returned_qty += line_id.quantity
                    claim_line_data = self.check_retutn_qty(
                        returned_qty, move_id, claim_lines)
                else:
                    claim_line_data = self.check_move_qty(move_id, claim_lines)

            self.claim_line_ids = [(5, 0, 0)] + claim_line_data

    @api.depends('claim_line_ids')
    def _compute_lot_ids(self):
        """select lot ids based on picking's move line"""
        for claim_id in self:
            move_ids = claim_id.picking_id.move_ids
            if move_ids and move_ids.move_line_ids.lot_id:
                claim_id.claim_lot_ids = move_ids.move_line_ids.lot_id
            else:
                claim_id.claim_lot_ids = [(6, 0, [])]

    @api.depends('repair_order_ids')
    def _compute_repairs_count(self):
        """This method used to display the repair orders for related claim."""
        for record in self:
            record.repairs_count = len(record.repair_order_ids)

    @api.model_create_multi
    def create(self, vals_list):
        """Need to add claim's Partner and Responsible person in to followers"""
        context = dict(self._context or {})
        for vals in vals_list:
            if vals.get('code', 'New') == 'New':
                vals['code'] = self.env['ir.sequence'].next_by_code(CRM_CLAIM_EPT)
            if vals.get('section_id') and not context.get('default_section_id'):
                context['default_section_id'] = vals.get('section_id')

        result = super().create(vals_list)
        self.add_email_in_partner(result)
        partner_id = result.rma_support_person_id.id if result.rma_support_person_id \
            else result.partner_id.id
        self.sudo().add_followers_in_claim(result, partner_id)
        return result

    def copy(self, default=None):
        """This method sets a rma name + (copy) in name."""
        claim = self.browse(self.id)
        default = dict(default or {}, name=_('%s (copy)') % claim.name)
        result = super().copy(default)
        result.onchange_picking_id()
        return result

    def write(self, vals):
        """This method sets a follower on the RMA on write method."""
        result = super().write(vals)
        partner_id = vals.get('rma_support_person_id')
        if partner_id:
            self.sudo().add_followers_in_claim(result=False, partner_id=partner_id)
        return result

    def unlink(self):
        """Claim can be only delete on draft stage"""
        if self.filtered(lambda l: l.state != 'draft'):
            raise UserError(_("Claim cannot be delete once it Approved."))

        return super().unlink()

    @staticmethod
    def add_email_in_partner(result):
        """This method is used to write email into partner"""
        for record in result.filtered(lambda l: not l.partner_id.email):
            record.partner_id.write({'email': record.email_from})

    def change_field_based_on_picking(self):
        """This method is used to fill claim values based on picking_id"""
        sale_id = self.picking_id.sale_id
        self.partner_id = self.picking_id.partner_id.id
        self.partner_phone = self.picking_id.partner_id.phone
        self.email_from = self.picking_id.partner_id.email if self.picking_id.partner_id.email else sale_id.partner_id.email
        self.sale_id = sale_id.id
        self.section_id = sale_id and sale_id.team_id.id
        self.partner_delivery_id = sale_id and sale_id.partner_shipping_id and \
            sale_id.partner_shipping_id.id \
            or self.picking_id.rma_sale_id \
            and self.picking_id.rma_sale_id.partner_shipping_id \
            and self.picking_id.rma_sale_id.partner_shipping_id.id \
            or False

    @staticmethod
    def check_move_qty(move_id, claim_lines):
        """
        This method is used to check move quantity based on claim line.
        If previous claim is not created then move qty count based on move's done qty
        """
        if move_id.quantity > 0:
            claim_lines.append((0, 0, {'product_id': move_id.product_id.id,
                                       'quantity': move_id.quantity, 'move_id': move_id.id}))
        return claim_lines

    @staticmethod
    def check_retutn_qty(returned_qty, move_id, claim_lines):
        """
        This method is used to check return quantity based on claim line.
        If previous claim is created then return qty count based on previous claim's return qty
        """
        if returned_qty < move_id.quantity:
            qty = move_id.quantity - returned_qty
            if qty > 0:
                claim_lines.append((0, 0, {
                    'product_id': move_id.product_id.id, 'quantity': qty, 'move_id': move_id.id}))
        return claim_lines

    def add_followers_in_claim(self, result, partner_id):
        """Add claim's Partner and Responsible person in to followers"""
        mail_obj = self.env['mail.followers']
        mail_data = self.search_mail_data(mail_obj, result, partner_id)
        if not mail_data:
            mail_vals = self.prepare_mail_values(result, partner_id)
            mail_obj.create(mail_vals)

    def search_mail_data(self, mail_obj, result, partner_id):
        """

        This method is used to search that current claim's partner is already
        added in followers or not
        """
        res_id = result.id if result else self.id
        return mail_obj.search([
            ('res_id', '=', res_id), ('res_model', '=', CRM_CLAIM_EPT),
            ('partner_id', '=', partner_id)])

    def prepare_mail_values(self, result, partner_id):
        """prepare values for mail followers"""
        return {
            'res_id': result and result.id or self.id,
            'res_model': CRM_CLAIM_EPT,
            'partner_id': partner_id,
        }

    def create_contact_partner(self):
        """
        This method used to redirect the wizard for create a contact partner from RMA.
        """
        context = self._context.copy()
        context.update({
            'current_partner_id': self.partner_id.parent_id.id or self.partner_id.id,
            'record': self.id or False,
            'is_create_contact_person': True
        })
        return {
            'name': 'Add New Contact Person',
            'view_mode': 'form',
            'res_model': 'create.partner.delivery.address.ept',
            'type': IR_WINDOW_ACTION,
            'context': context,
            'target': 'new'
        }

    def add_delivery_address(self):
        """
        This method used to redirect the wizard for create a delivery partner from RMA.
        """
        context = self._context.copy()
        context.update({
            'current_partner_id': self.partner_id.parent_id.id or self.partner_id.id,
            'record': self.id or False
        })
        return {
            'name': 'Add New Delivery Address',
            'view_mode': 'form',
            'res_model': 'create.partner.delivery.address.ept',
            'type': IR_WINDOW_ACTION,
            'context': context,
            'target': 'new'
        }

    def action_view_repair_orders(self):
        """This action used to redirect repair orders from the RMA."""
        return {
            'type': IR_WINDOW_ACTION,
            'name': _('Repairs'),
            'res_model': 'repair.order',
            'view_mode': 'tree,form',
            'domain': [('claim_id', '=', self.id)],
            'context': dict(self._context),
        }

    def create_return_picking(self, claim_lines=False):
        """
        This method used to create a return picking, when the approve button clicks on the RMA.
        """
        return_picking_id = True
        location_id = self.location_id.id
        vals = {
            'picking_id': self.return_picking_id.id if claim_lines else self.picking_id.id}
        active_id = self.return_picking_id.id if claim_lines else self.picking_id.id
        return_picking_wizard = self.env['stock.return.picking'].with_context(
            active_id=active_id).create(vals)
        return_picking_wizard._compute_moves_locations()
        if location_id and not claim_lines:
            return_picking_wizard.write({'location_id': location_id})
        return_lines = self.create_return_picking_lines(
            claim_lines, return_picking_wizard)
        return_picking_wizard.write(
            {'product_return_moves': [(6, 0, return_lines)]})
        new_picking_id, pick_type_id = return_picking_wizard._create_returns()
        if claim_lines:
            self.write({'to_return_picking_ids': [(4, new_picking_id)]})
        else:
            return_picking_id = self.create_move_lines(new_picking_id)
        return return_picking_id

    def create_return_picking_lines(self, claim_lines, return_picking_wizard):
        """This method is used to create return picking"""
        return_lines = []
        lines = claim_lines or self.claim_line_ids
        for line in lines:
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
        for claim_line in self.claim_line_ids:
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
                    else:
                        return_move_line = return_lot_move_lines[0]
                    quantity = claim_line_by_lots.get(return_move_line.lot_id)
                    if quantity >= return_quantity:
                        return_move_line.write({'quantity': return_quantity})
                        break
                    return_quantity -= quantity
                    return_move_line.write({'quantity': quantity})
            else:
                return_move_lines = self.return_picking_id.move_ids.mapped('move_line_ids').filtered(
                    lambda l, claim_line=claim_line: l.product_id.id == claim_line.product_id.id)
                return_move_line = return_move_lines[0]
                return_move_line.write({'quantity': return_quantity})
        return self.return_picking_id

    def prepare_move_line_values(self, stock_move):
        """This method is used to prepare values for stock move lines."""
        return {
            'move_id': stock_move.id,
            'location_id': stock_move.location_id.id,
            'location_dest_id': stock_move.location_dest_id.id,
            'product_uom_id': stock_move.product_id.uom_id.id,
            'product_id': stock_move.product_id.id,
            'picking_id': self.return_picking_id.id,
        }

    @staticmethod
    def prepare_values_for_return_picking_line(line, return_picking_wizard, move_id):
        """This method Used to prepare values for return picking line."""
        return {
            'product_id': line.product_id.id,
            'quantity': line.quantity,
            'wizard_id': return_picking_wizard.id,
            'move_id': move_id[0].id if move_id else False
        }

    @staticmethod
    def check_claim_line_validate(line):
        """This method is used to check claim line is proper or not."""
        if line.quantity <= 0 or not line.rma_reason_id:
            raise UserError(
                _("Please set Return Quantity and Reason for all products."))

        if line.product_id.tracking == 'serial' and len(line.serial_lot_ids) != line.quantity:
            raise UserError(_("Either Serial number is not set for product: '%s' or"
                              " It is mismatch with return quantity") % (line.product_id.name))

        if line.product_id.tracking == 'lot':
            if len(line.serial_lot_ids) == 0:
                raise UserError(
                    _("Please set Lot number for the product: '%s'.") % (line.product_id.name))

    def approve_claim(self):
        """
        This method used to approve the RMA. It will create a return
         picking base on the RMA configuration.

        """
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
        self.write({'state': 'approve'})

        if self.is_rma_without_incoming:
            self.write({'state': 'process'})
        else:
            return_picking_id = self.create_return_picking()
            if return_picking_id:
                return_picking_id.write({'claim_id': self.id})

        self.sudo().action_rma_send_email()

    def check_previous_claim_qty(self, line):
        """
        This method is used check already claim is created or not
        based on previous claim we can add product qty on current claim
        """
        for move_id in self.picking_id.move_ids.filtered(lambda r: r.product_id.id == line.product_id.id):
            previous_claimline_ids = self.env['claim.line.ept'].search([
                ('move_id', '=', move_id.id), ('product_id',
                                               '=', move_id.product_id.id),
                ('claim_id.state', '=', 'close')])
            if previous_claimline_ids:
                returned_qty = 0
                for line_id in previous_claimline_ids:
                    returned_qty += line_id.quantity
                if returned_qty < move_id.quantity:
                    qty = move_id.quantity - returned_qty
                    if line.quantity > qty:
                        raise UserError(_("You have already one time process RMA. "
                                          "So You need to check Product Qty"))

    def action_rma_send_email(self):
        """This method used to send RMA to customer."""
        company_id = self.env.user.company_id
        if company_id.rma_template:
            email_template = company_id.rma_template_id
            if not email_template:
                email_template = self.env.ref(
                    'rma_ept.mail_rma_details_notification_ept', False)
            mail_mail = email_template.send_mail(
                self.id) if email_template else False
            if mail_mail:
                self.env['mail.mail'].browse(mail_mail).send()

    def reject_claim(self):
        """
        This method used to reject a claim and it will
        display the wizard for which reason did you reject.
        """
        return {
            'name': "Reject Claim",
            'view_mode': 'form',
            'res_model': 'claim.process.wizard',
            'view_id': self.env.ref('rma_ept.view_claim_reject_ept').id,
            'type': IR_WINDOW_ACTION,
            'context': {'claim_lines': self.claim_line_ids.ids},
            'target': 'new'
        }

    def set_to_draft(self):
        """This method used to set claim into the draft state."""
        if self.return_picking_id and self.return_picking_id.state != 'draft':
            if self.return_picking_id.state in ['cancel', 'done']:
                raise UserError(_("Claim cannot be move draft state once "
                                  "it Receipt is done or cancel."))
            self.return_picking_id.action_cancel()

        if self.internal_picking_id and self.internal_picking_id.state != 'draft':
            self.internal_picking_id.action_cancel()
        self.write({'state': 'draft'})

    def show_return_picking(self):
        """This action used to display the receipt on the RMA."""
        return {
            'name': "Receipt",
            'view_mode': 'form',
            'res_model': STOCK_PICKING,
            'type': IR_WINDOW_ACTION,
            'res_id': self.return_picking_id.id
        }

    def show_delivery_picking(self):
        """
        This method used to display the delivery orders on RMA.
        """
        if len(self.to_return_picking_ids.ids) == 1:
            delivery_picking_action = {
                'name': "Delivery",
                'view_mode': 'form',
                'res_model': STOCK_PICKING,
                'type': IR_WINDOW_ACTION,
                'res_id': self.to_return_picking_ids.id
            }
        else:
            delivery_picking_action = {
                'name': "Deliveries",
                'view_mode': 'tree,form',
                'res_model': STOCK_PICKING,
                'type': IR_WINDOW_ACTION,
                'domain': [('id', 'in', self.to_return_picking_ids.ids)]
            }
        return delivery_picking_action

    def show_internal_transfer(self):
        """open tree,from view for internal transfer."""
        return {
            'name': "Internal Transfer",
            'view_mode': 'tree,form',
            'res_model': STOCK_PICKING,
            'type': IR_WINDOW_ACTION,
            'domain': [('id', 'in', self.internal_picking_id.ids)]
        }

    def action_claim_reject_process_ept(self):
        """This method action used to reject claim."""
        return {
            'name': "Reject Claim",
            'view_mode': 'form',
            'res_model': 'claim.process.wizard',
            'view_id': self.env.ref('rma_ept.view_claim_reject_ept').id,
            'type': IR_WINDOW_ACTION,
            'context': {'claim_lines': self.claim_line_ids.ids},
            'target': 'new'
        }

    def act_supplier_invoice_refund_ept(self):
        """
        This method action used to redirect from RMA to credit note.
        """
        if len(self.refund_invoice_ids) == 1:
            refun_invoice_action = {
                'name': "Customer Invoices",
                'view_mode': 'form',
                'res_model': ACCOUNT_MOVE,
                'type': IR_WINDOW_ACTION,
                'view_id': self.env.ref('account.view_move_form').id,
                'res_id': self.refund_invoice_ids.id
            }
        else:
            refun_invoice_action = {
                'name': "Customer Invoices",
                'view_mode': 'tree,form',
                'res_model': ACCOUNT_MOVE,
                'type': IR_WINDOW_ACTION,
                'views': [(self.env.ref('account.view_invoice_tree').id, 'tree'),
                          (self.env.ref('account.view_move_form').id, 'form')],
                'domain': [('id', 'in', self.refund_invoice_ids.ids), ('type', '=', 'out_refund')]
            }
        return refun_invoice_action

    def act_new_so_ept(self):
        """
        This method action used to redirect from RMA to Sale Order.
        """
        return {
            'name': "Sale Order",
            'view_mode': 'form',
            'res_model': SALE_ORDER,
            'type': IR_WINDOW_ACTION,
            'res_id': self.new_sale_id.id
        }

    def check_validate_claim(self):
        """This method is used to check claim is validate or not"""
        if self.state != 'process':
            raise UserError(_("Claim can't process."))
        if self.return_picking_id.state != 'done' and not self.is_rma_without_incoming:
            raise UserError(_("Please first validate Return Picking Order."))
        if self.internal_picking_id and self.internal_picking_id.state != 'done':
            raise UserError(
                _("Please first validate Internal Transfer Picking Order."))

    def check_validate_claim_lines(self, line):
        """This method is used to check claim Lines is validate or not"""

        if self.return_picking_id and self.return_picking_id.state == 'done' \
                and not line.claim_type:
            raise UserError(
                _("Please set RMA Workflow Action for all rma lines."))
        if self.is_rma_without_incoming and not line.claim_type:
            raise UserError(
                _("Please set RMA Workflow Action for all rma lines."))
        if line.claim_type == 'replace_other_product' and (
                not line.to_be_replace_product_id or line.to_be_replace_quantity <= 0):
            raise UserError(_(
                "Claim line with product %s has Replace product or "
                "Replace quantity or both not set.") % (line.product_id.name))

    def process_claim(self):
        """This method used to process a claim."""
        self.check_validate_claim()

        refund_lines, do_lines, so_lines, ro_lines = self.prepare_list_based_on_line_operations()

        if refund_lines:
            self.create_refund(refund_lines)
        if do_lines:
            self.create_do(do_lines)
        if so_lines:
            self.create_so(so_lines)
        if ro_lines:
            self.create_ro(ro_lines)

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

        for line in self.claim_line_ids:
            self.check_validate_claim_lines(line)
            if line.claim_type == 'repair':
                ro_lines.append(line)
            if line.claim_type == 'refund':
                refund_lines.append(line)
            if line.claim_type == 'replace_same_product':
                do_lines.append(line)
            if line.claim_type == 'replace_other_product':
                if not line.is_create_invoice:
                    # no need to check if else in else
                    do_lines.append(line)
                else:
                    if line.is_create_invoice:
                        so_lines.append(line)
                        refund_lines.append(line)
                    else:
                        so_lines.append(line)

        return refund_lines, do_lines, so_lines, ro_lines

    def _default_picking_type_id(self):
        return self._get_picking_type().get((self.env.company, self.env.user))

    def _get_picking_type(self):
        companies = self.company_id or self.env.company
        if not self:
            # default case
            default_warehouse = self.env.user.with_company(companies.id)._get_default_warehouse_id()
            if default_warehouse and default_warehouse.repair_type_id:
                return {(companies, self.env.user): default_warehouse.repair_type_id}

        picking_type_by_company_user = {}
        without_default_warehouse_companies = set()
        for (company, user), dummy in groupby(self, lambda r: (r.company_id, r.user_id)):
            default_warehouse = user.with_company(company.id)._get_default_warehouse_id()
            if default_warehouse and default_warehouse.repair_type_id:
                picking_type_by_company_user[(company, user)] = default_warehouse.repair_type_id
            else:
                without_default_warehouse_companies.add(company.id)

        if not without_default_warehouse_companies:
            return picking_type_by_company_user

        domain = [
            ('code', '=', 'repair_operation'),
            ('warehouse_id.company_id', 'in', list(without_default_warehouse_companies)),
        ]

        picking_types = self.env['stock.picking.type'].search_read(domain, ['company_id'], load=False)
        for picking_type in picking_types:
            if (picking_type.company_id, False) not in picking_type_by_company_user:
                picking_type_by_company_user[(picking_type.company_id, False)] = picking_type
        return picking_type_by_company_user

    def create_ro(self, claim_lines):
        """This method is used to create repair order"""
        repair_order_obj = self.env["repair.order"]
        for line in claim_lines:
            repair_order_list = []
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
                if line.product_id.tracking == 'lot':
                    repair_order_dict.update({
                        'lot_id': line.serial_lot_ids[0].id,
                        'picking_type_id': self._default_picking_type_id().id
                    })
                repair_order_list.append(repair_order_dict)
            repair_order_obj.create(repair_order_list)

    def prepare_repair_order_dis(self, claim_line, qty):
        """Prepare a dictionary for repair orders."""
        location = self.location_id or self.env['stock.warehouse'].search([
            ('company_id', '=', self.company_id.id)], limit=1).lot_stock_id
        sale_order_id = self.sale_id.id if self.sale_id else False
        return {
            'product_id': claim_line.product_id.id,
            'product_qty': qty,
            'claim_id': self.id,
            'partner_id': self.partner_id.id,
            'product_uom': claim_line.product_id.uom_id.id,
            'company_id': self.company_id.id,
            'location_id': location.id,
            'sale_order_id': sale_order_id

        }

    def create_so(self, claim_lines):
        """This method used to create a sale order."""
        order_vals = self.prepare_sale_order_values()
        sale_order = self.env[SALE_ORDER].create(order_vals)

        # sale_order.onchange_partner_id()
        # sale_order.onchange_partner_shipping_id()

        self.create_sale_order_lines(sale_order, claim_lines)
        self.write({'new_sale_id': sale_order.id})

    def create_sale_order_lines(self, sale_order, lines):
        """This method used to create a sale order line."""
        for line in lines:
            order_line_vals = self.prepare_sale_order_line_values(
                sale_order, line)
            order_line = self.env['sale.order.line'].create(order_line_vals)
            order_line._compute_price_unit()

    def prepare_sale_order_values(self):
        """prepare values for sale order"""
        return {
            'company_id': self.company_id.id,
            'partner_id': self.partner_id.id,
            'warehouse_id': self.sale_id.warehouse_id.id,
            'client_order_ref': self.name,
        }

    @staticmethod
    def prepare_sale_order_line_values(sale_order, line):
        """prepare values for sale order line."""
        return {
            'order_id': sale_order.id,
            'product_id': line.to_be_replace_product_id.id,
            'product_uom_qty': line.to_be_replace_quantity,
        }

    def create_do(self, claim_lines):
        """based on this method to create a picking one..two or three step."""
        procurements = []

        vals = self._prepare_procurement_group_vals()
        group_id = self.env[PROCUREMENT_GROUP].create(vals)
        values = self._prepare_procurement_values(group_id)

        for line in claim_lines:
            qty = line.to_be_replace_quantity or line.quantity
            product_id = line.to_be_replace_product_id or line.product_id
            procurements.append(self.env[PROCUREMENT_GROUP].Procurement(
                product_id, qty, product_id.uom_id,
                self.partner_delivery_id.property_stock_customer, self.name,
                self.code, self.company_id, values))

        if procurements:
            self.env[PROCUREMENT_GROUP].with_context(clean_context(self.env.context)).run(
                procurements)

        pickings = self.env[STOCK_PICKING].search(
            [('group_id', '=', group_id.id)])
        self.write({'to_return_picking_ids': [(6, 0, pickings.ids)]})
        pickings[-1].action_assign()

    def _prepare_procurement_group_vals(self):
        """prepare a procurement group vals."""
        return {
            'name': self.code,
            'partner_id': self.partner_delivery_id.id,
            'sale_id': self.sale_id.id,
            'move_type': self.sale_id.picking_policy,
        }

    def _prepare_procurement_values(self, group_id):
        """prepare values for procurement"""
        return {
            'group_id': group_id,
            'warehouse_id': self.sale_id.warehouse_id or False,
            'partner_id': self.partner_delivery_id.id,
            'company_id': self.company_id,
            'rma_sale_id': self.sale_id.id,
        }

    def create_refund(self, claim_lines):
        """This method used to create a refund."""
        if not self.sale_id.invoice_ids:
            message = _(
                "The invoice was not created for Order : "
                "<a href=# data-oe-model=sale.order data-oe-id=%d>%s</a>") % (self.sale_id.id, self.sale_id.name)
            self.message_post(body=message)
            return False
        refund_invoice_ids_rec = []

        refund_invoice_ids = self.check_and_create_refund_invoice(claim_lines)
        if not refund_invoice_ids:
            return False
        refund_invoice_ids_rec = self.prepare_and_create_refund_invoice(
            refund_invoice_ids, refund_invoice_ids_rec)

        if refund_invoice_ids_rec:
            self.write({'refund_invoice_ids': [
                       (6, 0, refund_invoice_ids_rec)]})
        return True

    def prepare_and_create_refund_invoice(self, refund_invoice_ids, refund_invoice_ids_rec):
        """prepare values for refund invoice and create refund invoice."""
        for invoice_id, lines in refund_invoice_ids.items():
            refund_invoice = self.create_reverse_move_for_invoice(invoice_id)
            if not refund_invoice:
                continue

            if refund_invoice.invoice_line_ids:
                refund_invoice.invoice_line_ids.with_context(
                    check_move_validity=False).unlink()

            for line in lines:
                if not list(line.keys()) or not list(line.values()):
                    continue

                product_id = self.env['product.product'].browse(
                    list(line.keys())[0])
                if not product_id:
                    continue
                move_line_vals = self.prepare_move_line_vals(
                    product_id, refund_invoice, line)
                line_vals = self.env['account.move.line'].new(move_line_vals)

                line_vals = line_vals._convert_to_write(
                    {name: line_vals[name] for name in line_vals._cache})
                sale_line_list = [line.get('sale_line_id')]
                line_vals.update({
                    'sale_line_ids': [(6, 0, sale_line_list or [])],
                    'tax_ids': [(6, 0, line.get('tax_id') or [])],
                    'quantity': list(line.values())[0],
                    'price_unit': line.get('price'),
                })
                self.env['account.move.line'].with_context(
                    check_move_validity=False).create(line_vals)

            # refund_invoice.with_context(check_move_validity=False)._recompute_dynamic_lines(
            #     recompute_all_taxes=True)
            refund_invoice_ids_rec.append(refund_invoice.id)
        return refund_invoice_ids_rec

    def create_reverse_move_for_invoice(self, invoice_id):
        """create refund invoice based on invoice."""
        refund_obj = self.env['account.move.reversal']
        invoice_obj = self.env[ACCOUNT_MOVE]

        invoice = invoice_obj.browse(invoice_id)

        context = {'active_ids': [invoice.id], 'active_model': ACCOUNT_MOVE}
        refund_process = refund_obj.with_context(**context).create({
            'reason': 'Refund Process of Claim - ' + self.name,
            'journal_id': invoice.journal_id.id,
        })

        refund = refund_process.reverse_moves()
        refund_invoice = refund and refund.get('res_id') and \
            invoice_obj.browse(refund.get('res_id'))

        refund_invoice.write({
            'invoice_origin': invoice.name,
            'claim_id': self.id
        })
        return refund_invoice

    @staticmethod
    def prepare_move_line_vals(product_id, refund_invoice, line):
        """prepare move lines."""
        return {
            'product_id': product_id.id,
            'name': product_id.name,
            'move_id': refund_invoice.id,
            'discount': line.get('discount') or 0
        }

    def check_and_create_refund_invoice(self, claim_lines):
        """
        This method is used to check invoice is posted or not and
        according to invoice it'll create refund invoice

        """
        product_process_dict = {}
        refund_invoice_ids = {}

        for line in claim_lines:
            if self.is_rma_without_incoming and line.id not in product_process_dict:
                qty = line.quantity if line.to_be_replace_quantity <= 0 else \
                    line.to_be_replace_quantity
                product_process_dict.update(
                    {line.id: {'total_qty': qty, 'invoice_line_ids': {}}})

            if line.id not in product_process_dict:
                product_process_dict.update({line.id: {'total_qty': line.return_qty,
                                                       'invoice_line_ids': {}}})

            invoice_lines = line.move_id.sale_line_id.invoice_lines
            for invoice_line in invoice_lines.filtered(
                    lambda l: l.move_id.move_type == 'out_invoice'):
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

                    refund_invoice_ids = self.prepare_refund_invoice_dict(
                        line, refund_invoice_ids, invoice_line, process_qty)

        return refund_invoice_ids

    def prepare_refund_invoice_dict(self, line, refund_invoice_ids, invoice_line, process_qty):
        """prepare refund invoice values based on invoice"""
        sale_line = line.move_id.sale_line_id
        refund_invoice_vals = self.add_dict_values_for_refund_invoice(
            sale_line, invoice_line, process_qty)

        if refund_invoice_ids.get(invoice_line.move_id.id):
            refund_invoice_ids.get(invoice_line.move_id.id).append(
                refund_invoice_vals)
        else:
            refund_invoice_ids.update(
                {invoice_line.move_id.id: [refund_invoice_vals]})

        return refund_invoice_ids

    @staticmethod
    def add_dict_values_for_refund_invoice(sale_line, invoice_line, process_qty):
        """add dictionary values on refund invoice"""
        return {
            invoice_line.product_id.id: process_qty,
            'price': sale_line.price_unit,
            'tax_id': sale_line.tax_id.ids,
            'discount': sale_line.discount,
            'sale_line_id': sale_line.id,
        }

    @staticmethod
    def prepare_product_qty_dict(product_line, invoice_line):
        """prepare dictionary based on invoice qty"""
        if product_line.get('process_qty', 0) + invoice_line.quantity < \
                product_line.get('total_qty', 0):
            process_qty = invoice_line.quantity
            product_line.update({'process_qty': product_line.get('process_qty', 0)
                                 + invoice_line.quantity})
        else:
            process_qty = product_line.get(
                'total_qty', 0) - product_line.get('process_qty', 0)
            product_line.update(
                {'process_qty': product_line.get('total_qty', 0)})

        return product_line, process_qty

    def action_rma_send(self):
        """open email template wizard to send mail."""
        self.ensure_one()
        self.rma_send = True
        template = self.env.ref(
            'rma_ept.mail_rma_details_notification_ept', False)
        compose_form = self.env.ref(
            'mail.email_compose_message_wizard_form', False)
        ctx = {
            'default_model': CRM_CLAIM_EPT,
            'default_res_ids': self.ids,
            'default_use_template': bool(template),
            'default_template_id': template.id,
            'default_composition_mode': 'comment',
            'force_email': True
        }
        return {
            'type': IR_WINDOW_ACTION,
            'view_mode': 'form',
            'res_model': 'mail.compose.message',
            'views': [(compose_form.id, 'form')],
            'view_id': compose_form.id,
            'target': 'new',
            'context': ctx,
        }

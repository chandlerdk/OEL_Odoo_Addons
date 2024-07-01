# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class CRMClaimLine(models.Model):
    _name = 'claim.line.ept'
    _description = 'CRM Claim Line'

    is_create_invoice = fields.Boolean("Create Invoice", copy=False)
    quantity = fields.Float('Return Quantity', copy=False)
    done_qty = fields.Float('Delivered Quantity',
                            compute='_compute_get_done_quantity')
    return_qty = fields.Float(
        'Received Quantity', compute='_compute_return_quantity')
    to_be_replace_quantity = fields.Float("Replace Quantity", copy=False)

    claim_type = fields.Selection([
        ('refund', 'Refund'),
        ('replace_same_product', 'Replace With Same Product'),
        ('replace_other_product', 'Replace With Other Product'),
        ('repair', 'Repair')], string="Action", copy=False, compute='_compute_claim_type', store=True)

    product_id = fields.Many2one('product.product', string='Product')
    claim_id = fields.Many2one('crm.claim.ept', string='Related claim', copy=False,
                               ondelete='cascade')
    to_be_replace_product_id = fields.Many2one('product.product', string="Product to be Replace",
                                               copy=False)
    move_id = fields.Many2one('stock.move')
    rma_reason_id = fields.Many2one('rma.reason.ept', string="Customer Reason")
    serial_lot_ids = fields.Many2many('stock.lot', string="Lot/Serial Number")

    def _compute_return_quantity(self):
        """
        This method used to set a return quantity in the claim line base on the return move.
        """
        for record in self:
            record.return_qty = 0
            if record.claim_id.return_picking_id:
                picking_ids = self.env['stock.picking'].search([('claim_id', '=', record.claim_id.id)])
                move_line = picking_ids.mapped('move_ids') \
                    .filtered(lambda r, record=record: r.sale_line_id.id == record.move_id.sale_line_id.id and
                              r.product_id.id == record.product_id.id and
                              r.origin_returned_move_id.id == record.move_id.id and r.state != 'cancel')
                record.return_qty = move_line[0].quantity if move_line else 0.0

    def _compute_get_done_quantity(self):
        """
        This method used to set done qty in claim line base on the delivered picking qty.
        """
        for record in self:
            record.done_qty = record.move_id.quantity

    @api.depends('rma_reason_id')
    def _compute_claim_type(self):
        """
        This method used to set action based on customer's reason.
        """
        for record in self:
            record.claim_type = record.rma_reason_id.action

    @api.onchange('serial_lot_ids')
    def onchange_serial_lot_id(self):
        """
        This method used for validation.
        """
        if self.claim_id and self.quantity < len(self.serial_lot_ids.ids):
            raise UserError(
                _("Lenth of Lot/Serial number are greater then the Return Quantity !"
                  "\n Please set the proper Lot/Serial Number"))

    @api.onchange('rma_reason_id')
    def onchange_product_id(self):
        """
        This method used recommendation to users.
        """
        warning = False
        if self.rma_reason_id and self.rma_reason_id.action == 'repair' \
                and self.claim_id.is_rma_without_incoming:
            warning_msg = {
                'title': _('Recommendation'),
                'message': "We recommend if you select repair action then we will need "
                           "return shipment."
                           "It will not create a return delivery of the repair order."
            }

            warning = {'warning': warning_msg}

        return warning

    def unlink(self):
        """
        This method used to delete the claim line when clam state in draft
        otherwise it will give a warning message.
        """
        if self.filtered(lambda l: l.claim_id and l.claim_id.state != 'draft'):
            raise UserError(_("Claim Line cannot be delete once it Approved."))
        return super().unlink()

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
            'context': {'product_id': self.product_id.id, 'hide': True, 'claim_line_id': self.id}
        }

    @api.constrains('quantity')
    def check_qty(self):
        """
        This method is used to check claim line's quantity
        """
        for line in self:
            if line.quantity < 0:
                raise UserError(_('Quantity must be positive number'))
            if line.quantity > line.move_id.quantity:
                raise UserError(
                    _('Quantity must be less than or equal to the delivered quantity'))

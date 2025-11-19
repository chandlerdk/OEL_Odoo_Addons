from odoo import models, fields, api

class AccountMove(models.Model):
    _inherit = 'account.move'

    delivery_reference = fields.Char(string='Delivery Reference', compute='_compute_delivery_info', store=True)
    tracking_reference = fields.Char(string='Tracking Reference', compute='_compute_delivery_info', store=True)

    @api.depends('invoice_line_ids.sale_line_ids.order_id')
    def _compute_delivery_info(self):
        for invoice in self:
            delivery_ref = False
            tracking_ref = False
            sale_orders = invoice.invoice_line_ids.mapped('sale_line_ids.order_id')
            if sale_orders:
                pickings = self.env['stock.picking'].search([
                    ('origin', 'in', sale_orders.mapped('name'))
                ], limit=1)
                if pickings:
                    picking = pickings[0]
                    delivery_ref = picking.name
                    tracking_ref = getattr(picking, 'carrier_tracking_ref', False) or False
            invoice.delivery_reference = delivery_ref
            invoice.tracking_reference = tracking_ref

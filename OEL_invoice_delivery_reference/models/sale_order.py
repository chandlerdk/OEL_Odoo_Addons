from odoo import models, fields, api

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    delivery_reference = fields.Char(string='Delivery Reference', compute='_compute_delivery_info', store=True)
    tracking_reference = fields.Char(string='Tracking Reference', compute='_compute_delivery_info', store=True)

    @api.depends()
    def _compute_delivery_info(self):
        for order in self:
            delivery_ref = False
            tracking_ref = False
            pickings = self.env['stock.picking'].search([
                ('origin', '=', order.name)
            ], limit=1)
            if pickings:
                picking = pickings[0]
                delivery_ref = picking.name
                tracking_ref = getattr(picking, 'carrier_tracking_ref', False) or False
            order.delivery_reference = delivery_ref
            order.tracking_reference = tracking_ref

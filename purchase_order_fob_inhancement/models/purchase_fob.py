# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class PurchaseFob(models.Model):
    _name = 'purchase.fob'
    _description = 'Purchase FOB Number'
    _order = 'is_favorite desc, sequence, name, id'

    name = fields.Char(string='FOB Number', required=True, index=True)
    sequence = fields.Integer(string='Sequence', default=10)
    active = fields.Boolean(string='Active', default=True)
    is_favorite = fields.Boolean(
        string='Favorite',
        default=False,
        help='Favorite FOB numbers appear at the top of the list on purchase orders.',
    )
    purchase_order_count = fields.Integer(
        string='Purchase Orders',
        compute='_compute_purchase_order_count',
    )


    def _compute_purchase_order_count(self):
        PurchaseOrder = self.env['purchase.order']
        for fob in self:
            fob.purchase_order_count = PurchaseOrder.search_count([('fob', '=', fob.id)])

    def action_view_purchase_orders(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Purchase Orders',
            'res_model': 'purchase.order',
            'view_mode': 'tree,form',
            'domain': [('fob', '=', self.id)],
            'context': {'default_fob': self.id},
        }

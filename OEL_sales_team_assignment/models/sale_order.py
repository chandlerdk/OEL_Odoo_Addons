# -*- coding: utf-8 -*-
from odoo import api, models

class SaleOrderAssignment(models.Model):
    _inherit = 'sale.order'

    @api.model_create_multi
    def create(self, vals_list):
        # 1. Create orders normally
        orders = super(SaleOrderAssignment, self).create(vals_list)
        # 2. Assign (or clear) team for each
        for order in orders:
            addr = order.partner_shipping_id
            team = addr.team_id or order.partner_id.team_id
            order.sudo().write({'team_id': team.id if team else False})
        return orders

    def write(self, vals):
        # 1. Perform the normal write
        res = super(SaleOrderAssignment, self).write(vals)
        # 2. If shipping address changed on drafts, reassign (or clear) team
        if 'partner_shipping_id' in vals:
            drafts = self.filtered(lambda o: o.state == 'draft')
            for order in drafts:
                addr = order.partner_shipping_id
                team = addr.team_id or order.partner_id.team_id
                order.sudo().write({'team_id': team.id if team else False})
        return res

    def action_confirm(self):
        # 1. Confirm the order
        res = super(SaleOrderAssignment, self).action_confirm()
        # 2. After confirmation, reassign (or clear) team based on final addresses
        for order in self:
            addr = order.partner_shipping_id
            team = addr.team_id or order.partner_id.team_id
            order.sudo().write({'team_id': team.id if team else False})
        return res


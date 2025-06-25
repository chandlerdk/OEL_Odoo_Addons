#  -- coding: utf-8 --
#
#
###########################################
#
#

from odoo import models, api

class SaleOrderAssignment(models.Model):
    _inherit = 'sale.order'


    @api.model_create_multi
    def create(self, vals_list):
        # First, Create the records normally
        orders = super(SaleOrderAssignment, self).create(vals_list)
        # Assign team after creation (using sudo to bypass restrictions)
        for order in orders:
            addr = order.partner_shipping_id
            team = addr.team_id if addr and addr.team_id else order.partner_id.team_id
            if team:
                order.sudo().write({'team_id': team.id})
        return orders


    def write(self, vals):
        # If the delivery address is changed on a draft, reassign team
        res = super(SaleOrderAssignment, self).write(vals)
        if 'partner_shipping_id' in vals:
            for order in self.filtered(lambda o: o.state == 'draft'):
                addr = order.partner_shipping_id
                team = addr.team_id if addr and addr.team_id else order.partner_id.team_id
                if team:
                    order.sudo().write({'team_id': team.id})
        return res

    def action_confirm(self):
        res = super(SaleOrderAssignment, self).action_confirm()
        # Reassign team on confirmation using current shipping/fallback logic
        for order in self:
            addr = order.partner_shipping_id
            team = addr.team_id if addr and addr.team_id else order.partner_id.team_id
            if team:
                order.sudo().write({'team_id': team.id})
        return res

# -*- coding: utf-8 -*-
####
#    Cybrosys Technologies Pvt. Ltd.
#    Copyright (C) 2024
####
from odoo import models


class SaleOrder(models.Model):
    """Inherits the sale order to disable external followers"""
    _inherit = 'sale.order'

    def action_confirm(self):
        """If setting is enabled, unsubscribe only external followers 
        (customers / portal users) and keep internal team members."""
        result = super(SaleOrder, self).action_confirm()

        if self.env['ir.config_parameter'].sudo().get_param(
                "follower_restrict.disable_sale_followers"):

            unsubscribe_followers = []
            for follower in self.message_follower_ids:
                partner = follower.partner_id
                # Keep employees (system users with group_user)
                if partner.user_ids and partner.user_ids[0].has_group('base.group_user'):
                    continue
                unsubscribe_followers.append(partner.id)

            if unsubscribe_followers:
                self.message_unsubscribe(unsubscribe_followers)
                self.invalidate_recordset(['message_follower_ids'])

        return result

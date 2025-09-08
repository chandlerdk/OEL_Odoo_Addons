# -*- coding: utf-8 -*-
####
#
#    Cybrosys Technologies Pvt. Ltd.
#    Copyright (C) 2024-TODAY Cybrosys Technologies(<https://www.cybrosys.com>).
#    Author: Mruthul Raj (odoo@cybrosys.com)
#
#    This program is free software: you can modify
#    it under the terms of the GNU LESSER GENERAL PUBLIC LICENSE (LGPL v3).
#
####
from odoo import models


class AccountMove(models.Model):
    """Inherits the account move (invoice) to disable external followers"""
    _inherit = 'account.move'

    def action_post(self):
        """If setting is enabled, unsubscribe only external followers
        (customers / portal users) and keep internal team members."""
        result = super(AccountMove, self).action_post()

        if self.env['ir.config_parameter'].get_param(
                "follower_restrict.disable_invoice_followers"):

            unsubscribe_followers = []
            for follower in self.message_follower_ids:
                partner = follower.partner_id
                # Keep only employees (internal users linked to group_user)
                if partner.user_ids and partner.user_ids[0].has_group('base.group_user'):
                    continue  # internal user -> keep them
                else:
                    unsubscribe_followers.append(partner.id)  # external -> remove

            if unsubscribe_followers:
                self.message_unsubscribe(unsubscribe_followers)
                self.invalidate_recordset(['message_follower_ids'])

        return result

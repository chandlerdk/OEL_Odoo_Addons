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
    """Inherits account.move (invoice) to manage followers:
       - On create: remove the salesperson (invoice_user_id) from followers
         to avoid internal draft/assignment notifications, while keeping them
         as the invoice's salesperson.
       - On post: keep your existing logic to remove external followers, and
         also ensure salesperson isn't re-added as follower."""
    _inherit = 'account.move'

    def create(self, vals_list):
        records = super().create(vals_list)
        ICP = self.env['ir.config_parameter'].sudo()

        # If you want this behavior always, remove the if-check
        if ICP.get_param("follower_restrict.disable_invoice_followers"):
            for move in records:
                sp_user = move.invoice_user_id
                if sp_user and sp_user.partner_id:
                    # Explicitly remove salesperson partner from followers on draft creation
                    move.message_unsubscribe([sp_user.partner_id.id])
                    move.invalidate_recordset(['message_follower_ids'])
        return records

    def action_post(self):
        """If setting is enabled, unsubscribe external followers and
        make sure the salesperson is not following even after posting."""
        result = super(AccountMove, self).action_post()

        ICP = self.env['ir.config_parameter'].sudo()
        if ICP.get_param("follower_restrict.disable_invoice_followers"):

            for move in self:
                unsubscribe_partner_ids = set()

                # 1) Remove external followers (customers / portal users)
                for follower in move.message_follower_ids:
                    partner = follower.partner_id
                    # Keep only employees (internal users linked to group_user)
                    if partner.user_ids and partner.user_ids[0].has_group('base.group_user'):
                        continue
                    unsubscribe_partner_ids.add(partner.id)

                # 2) Ensure salesperson is not following (belt-and-suspenders)
                sp_user = move.invoice_user_id
                if sp_user and sp_user.partner_id:
                    unsubscribe_partner_ids.add(sp_user.partner_id.id)

                if unsubscribe_partner_ids:
                    move.message_unsubscribe(list(unsubscribe_partner_ids))
                    move.invalidate_recordset(['message_follower_ids'])

        return result

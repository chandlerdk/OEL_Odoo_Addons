# -*- coding: utf-8 -*-
####
#    Cybrosys Technologies Pvt. Ltd.
#    Copyright (C) 2024
####
from odoo import models


class MailThread(models.AbstractModel):
    """Override mail.thread to prevent auto-adding followers on email"""
    _inherit = 'mail.thread'

    def message_subscribe(self, partner_ids=None, subtype_ids=None, *args, **kwargs):
        """Prevent auto-adding followers when sending emails,
        unless explicitly disabled in settings."""
        # Handle empty partner_ids gracefully
        partner_ids = partner_ids or []

        # Check global disable flag
        if self.env['ir.config_parameter'].sudo().get_param(
                "follower_restrict.disable_global_email_subscribe"):
            return

        return super().message_subscribe(
            partner_ids=partner_ids,
            subtype_ids=subtype_ids,
            *args,
            **kwargs
        )

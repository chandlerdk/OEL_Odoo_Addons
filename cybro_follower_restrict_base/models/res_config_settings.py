# -*- coding: utf-8 -*-
####
#    Cybrosys Technologies Pvt. Ltd.
#    Copyright (C) 2024
####
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    """Inherits the config settings for adding disable options per module"""
    _inherit = 'res.config.settings'

    disable_sale_followers = fields.Boolean(
        "Disable Followers for Sales",
        config_parameter="follower_restrict.disable_sale_followers",
        help="Automatically remove customers as followers when confirming sale orders."
    )

    disable_purchase_followers = fields.Boolean(
        "Disable Followers for Purchase",
        config_parameter="follower_restrict.disable_purchase_followers",
        help="Automatically remove vendors as followers when confirming purchase orders."
    )

    disable_invoice_followers = fields.Boolean(
        "Disable Followers for Invoices",
        config_parameter="follower_restrict.disable_invoice_followers",
        help="Automatically remove customers as followers when confirming invoices."
    )

    disable_global_email_subscribe = fields.Boolean(
        "Disable Auto Followers on Email",
        config_parameter="follower_restrict.disable_global_email_subscribe",
        help="Prevents auto-adding recipients as followers when sending emails from any document."
    )

# -*- encoding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd.
# Copyright (C) 2024 (https://www.bistasolutions.com)
#
##############################################################################

{
    "name": "Email Modification - CC and BCC field",
    "summary": "Add CC and BCC field",
    "version": "17.0.1.0.0",
    "description": """ Add CC and BCC field """,
    "category": "Uncategorized",
    "website": "https://www.bistasolutions.com",
    "author": "Bista Solutions Pvt. Ltd.",
    "license": "AGPL-3",
    "depends": ["sale"],
    "data": [
        "views/res_config_setting_view.xml",
        "wizard/account_move_send_view.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}

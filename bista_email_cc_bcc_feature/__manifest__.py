# -*- encoding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd.
# Copyright (C) 2021 (https://www.bistasolutions.com)
#
##############################################################################

{
    "name": "Email Modification - CC and BCC field",
    "summary": "Add CC and BCC field",
    "version": "17.0.1.0.0",
    "description": """
        Add CC and BCC field
    """,
    "category": "Uncategorized",
    "website": "https://www.bistasolutions.com",
    "author": "Bista Solutions Pvt. Ltd.",
    "depends": ['sale', 'purchase', 'account'],
    "data": [
        'wizard/mail_compose_message_view.xml',
        'wizard/account_invoice_send_view.xml',
        'views/res_config_setting_view.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,

}

# -*- encoding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2024 (https://www.bistasolutions.com)
# Part of Bista. See LICENSE file for full copyright and licensing details.
#
##############################################################################

{
    'name': "Bista Message Search",
    'author': "Bista Solutions Pvt. Ltd",
    'website': "https://bistasolutions.com",

    'description': """
        this app helps to search messages from chatter of any model
    """,

    'category': 'Mail',
    'version': '17.0.1.0',
    'license': 'AGPL-3',
    'depends': [
        'mail', 'calendar', 'crm', 'account', 'sale'

    ],
    
    'data': [
        'views/user_message_view.xml',
        'views/partner_view.xml',
    ],
    
    'installable': True,
}

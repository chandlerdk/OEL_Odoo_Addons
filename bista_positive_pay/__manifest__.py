# -*- coding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2021 (https://www.bistasolutions.com)
#
##############################################################################
{
    'name': 'Bista Positive Pay Report',
    'version': '17.0',
    'category': 'Reports',
    'license': 'LGPL-3',
    'description': '''Positive Pay Report''',
    'author': 'Bista Solutions Pvt. Ltd',
    'maintainer': 'Bista Solutions Pvt. Ltd.',
    'website': 'http://www.bistasolutions.com',
    'depends': ['account', 'account_check_printing'],
    'data': [
        'views/account_payment.xml'
    ],
    'installable': True,
    'auto_install': False,
}

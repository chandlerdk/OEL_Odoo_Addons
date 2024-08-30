# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name': 'Bista Mail From Filter',
    'version': '17.0',
    'description': """Overwrite sender email address.""",
    'category': 'Accounting',
    'depends': ['account'],
    "license": "LGPL-3",
    'data': [
        'views/mail_server.xml'
    ],
    'installable': True,
    'auto_install': False

}

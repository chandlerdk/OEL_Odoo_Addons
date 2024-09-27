# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.
{

    # App information
    'name': 'Sale Order Report Enhancement',
    'version': '17.0.1.0',
    'category': 'Sales',
    'license': 'OPL-1',
    'summary': "manage Sale Order Report Enhancement",

    # Author
    'author': 'Bista Solutions Pvt. Ltd.',
    'maintainer': 'Bista Solutions Pvt. Ltd.',
    'website': "https://www.bistasolutions.com/",

    # Dependencies
    'depends': ['base', 'sale', 'stock'],

    'data': [
            'reports/sale_order_report.xml',
    ],

    'installable': True,
    'auto_install': False,
    'application': True,
    'active': False,
}

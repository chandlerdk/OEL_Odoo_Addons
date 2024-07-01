# -*- coding: utf-8 -*-
##############################################################################
#
# Cron QuoTech
# Copyright (C) 2021 (https://cronquotech.odoo.com)
#
##############################################################################
{
    'name': 'Disable Quick Create | Disable Create and Edit on Many2one',
    'version': '17.0.0.0.0',
    "description": """
            Disable Quick Create for many2one field in odoo, With this app, 
            You can prevent user create and edit on Many2one field.
    """,
    'author': 'Cronquotech',
    'support': 'cronquotech@gmail.com',                                                             
    'website': "https://cronquotech.odoo.com",
    'category': 'Web',
    'summary': 'Disable "quick create" for all and "create and edit" '
               '| Disable quick create'
               '| Disable Quick create and edit for specific models'
               '| DISABLE QUICK CREATE EDIT'
               '| Disable Quick Create Product'
               '| Disable Quick Create Customer'
               '| Disable Quick Create Product and User Restriction For Creating Product'
               '| Disabled Quick Create Product on Sales'
               '| Disabled Quick Create Product on Purchase'
               '| Disabled Quick Create Product on Inventory'
               '| User Restriction For Creating Product'
               '| Disable Quick Create for many2one field in odoo'
               '| Product Disable Quick Create'
               '| Disable Create and Edit on Many2one',
    'depends': ['web', 'base'],
    'license': 'OPL-1',
    'price': 7.00,
    'currency': "USD",
    'data': [
        'views/ir_model.xml',
    ],
    'assets': {
        'web.assets_backend': [
            '/cqt_disable_quick_create/static/src/js/disable_quick_create.js'],
    },
    'images': [
        'static/description/banner.png',
    ],
    'installable': True,
    'application': True,
}
# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

# -*- coding: utf-8 -*-
{
    "name": "Bista Sales Commission",
    "version": "17.0",
    'category': "Sales",
    "description": """""",
    "license": "LGPL-3",
    "author": "Omid Totakhel",
    "maintainer": "Bista Solutiones",
    "website": "https://www.bistasolutions.com/",
    "depends": ['base', 'sale_management', 'account', 'sale'],
    "data": [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'views/sale_commission.xml',
        'views/account_move_line.xml',
        'views/sale_order.xml',
        'views/res_partner.xml',
        'views/account_move.xml',
        'views/account_move_invoice_commision.xml',
        'wizard/commission_bill_wizard.xml',
    ],
    "auto_install": False,
    "installable": True,
}


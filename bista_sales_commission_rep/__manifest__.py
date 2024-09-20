# -*- coding: utf-8 -*-
{
    "name": "Bista Sales Commission Rep Extension",
    "version": "17.0",
    'category': "Sales",
    "description": """
                    This module extends Bista Sales Commission module and allows users to select res.partner 
                    as sales person. This eliminates the need of creating portal users if the agents are external.
                    """,
    "license": "LGPL-3",
    "author": "Omid Totakhel",
    "maintainer": "Bista Solutiones",
    "website": "https://www.bistasolutions.com/",
    "depends": ["sale", "bista_sales_commission"],
    "data": [
        'views/sale_commission.xml',
        'views/account_move_line.xml',
        'views/sale_order.xml',
        'views/res_partner.xml',
        'views/account_move.xml',
        'reports/sale_report.xml',
        'reports/account_invoice_report.xml',
        'wizard/commission_bill_wizard.xml',
    ],
    "auto_install": False,
    "installable": True,
}

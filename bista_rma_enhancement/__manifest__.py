# -*- coding: utf-8 -*-

{
    "name": "Bista : RMA Enhancement.",
    "version": "17.0",
    "author": "Bista Solutions",
    "website": "https://www.bistasolutions.com",
    "category": "Inventory/Inventory",
    "license": "LGPL-3",
    "support": "  ",
    "summary": "RMA Enhancement",
    "description": """ RMA Enhancement """,
    "depends": [
        'rma_ept',
        'helpdesk',
        'quality_control',
        'stock',
        'mrp',
        'sale_stock',
        'stock_account'
    ],
    "data": [
        'data/data.xml',
        'wizard/claim_process_wizard.xml',
        'wizard/quality_check_wizard_views.xml',
        'views/stock.xml',
        'views/rma.xml',
        'views/helpdesk.xml',
        'views/quality.xml',
        'views/mrp.xml',
        'views/repair.xml',
        'views/account.xml',
        'views/res_partner.xml',
    ],
    "application": True,
    "installable": True,
    "auto_install": False,
}

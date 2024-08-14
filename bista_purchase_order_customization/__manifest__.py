# -*- coding: utf-8 -*-

{
    "name": "Bista : Purchase order Customization.",
    "version": "17.0",
    "author": "Bista Solutions",
    "website": "https://www.bistasolutions.com",
    "category": "purchase",
    "license": "LGPL-3",
    "support": "  ",
    "summary": " Purchase Order Customization Enhancement",
    "description": """ Purchase Order Customization """,
    "depends": [
        'base',
        'purchase',
        'purchase_stock',
        'stock',
        'mrp_subcontracting',
    ],
    "data": [
        'views/purchase_order_view.xml',
        'views/stock_picking_type_view.xml',
    ],
    "application": True,
    "installable": True,
    "auto_install": False,
}

# -*- encoding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2024 (http://www.bistasolutions.com)
#
##############################################################################
{
    'name': 'Bista Delivery Slip',
    'category': 'stock',
    'summary': 'This module Offers customize Delivery Slip',
    'version': '20.0.0.0.0',
    'author': 'Bista Solutions',
    'website': 'http://www.bistasolutions.com',
    'license': 'AGPL-3',
    'description': """The Delivery Slip Report
    """,
    'depends': ['sale', 'purchase', 'stock', 'web', 'bista_shipstation'],
    'data': [
        'views/sale_order_view.xml',
        'views/account_move_view.xml',
        'reports/report_delivery_slip.xml',
        'reports/report_stock_picking_operations.xml',
        'reports/internal_picking.xml',
        'reports/invoice_report.xml',
],

    'installable': True,
    'application': True,
    'auto_install': False,
}

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
    'version': '17.0.1.0.1',
    'author': 'Bista Solutions',
    'website': 'http://www.bistasolutions.com',
    'license': 'AGPL-3',
    'description': """The Delivery Slip Report
    """,
    'depends': ['sale', 'stock','web','bista_shipstation'],
    'data': [
        'views/account_move_view.xml',
        'reports/report_delivery_slip.xml',
        'reports/report_stock_picking_operations.xml',
        'reports/internal_picking.xml',
        'reports/invoice_report.xml',
],

    'installable': True,
    'application': True,

}

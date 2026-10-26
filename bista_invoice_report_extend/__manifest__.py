# -*- encoding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2024 (http://www.bistasolutions.com)
#
##############################################################################
{
    'name': 'Bista Invoice Report',
    'category': 'stock',
    'summary': 'This module Offers customize Invoice Report',
    'version': '20.0.0.0.0',
    'author': 'Bista Solutions',
    'website': 'http://www.bistasolutions.com',
    'license': 'AGPL-3',
    'description': """The Invoice Report
    """,
    'depends': ['sale', 'stock', 'sale_stock', 'web', 'account', 'bista_shipstation'],
    'data': [
            'reports/invoice_report_enhance.xml',
],

    'installable': True,
    'application': True,

}

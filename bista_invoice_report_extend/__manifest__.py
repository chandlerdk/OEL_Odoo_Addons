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
    'version': '17.0.1.0.1',
    'author': 'Bista Solutions',
    'website': 'http://www.bistasolutions.com',
    'license': 'AGPL-3',
    'description': """The Invoice Report
    """,
    'depends': ['sale', 'stock','web','account'],
    'data': [
            'reports/invoice_report.xml',
],

    'installable': True,
    'application': True,

}

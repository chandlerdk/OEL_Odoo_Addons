# -*- encoding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2024 (http://www.bistasolutions.com)
#
##############################################################################
{
    'name': 'Bista Sale Order Report',
    'category': 'Sale',
    'summary': 'This module Offers customize Sale Report',
    'version': '17.0.1.0.1',
    'author': 'Bista Solutions',
    'website': 'http://www.bistasolutions.com',
    'license': 'AGPL-3',
    'description': """The Invoice Report
    """,
    'depends': ['sale', 'base'],
    'data': [
            'report/sale_report_enhance.xml',
],

    'installable': True,
    'application': True,

}

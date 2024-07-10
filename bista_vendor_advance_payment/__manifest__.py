# -*- encoding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (https://www.bistasolutions.com)
# Part of Bista. See LICENSE file for full copyright and licensing details.
#
##############################################################################

{
    'name': "Bista Vendor Advance Payment",
    "version": "17.0",
    "author": "Bista Solutions",
    "website": "http://www.bistasolutions.com",
    'category': 'Purchase',
    'depends': ['base', 'purchase', 'account', 'stock'],
    'data': [
        'security/ir.model.access.csv',
        'wizard/purchase_make_invoice_advance_views.xml',
        'views/purchase_view.xml',
    ],
    'demo': [
    ],
    # Mark module as 'App'
    "application": True,
    "auto_install": False,
    "installable": True,
    "license": "LGPL-3"
}

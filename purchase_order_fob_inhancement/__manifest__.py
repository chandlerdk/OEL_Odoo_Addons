# -*- encoding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2024 (http://www.bistasolutions.com)
#
##############################################################################
{
    'name': 'Bista purchase order Fob Inhacement',
    'category': 'Purchases',
    'summary': 'Manage FOB numbers for purchase orders with favorites and sequencing',
    'version': '17.0.1.1.0',
    'author': 'Bista Solutions',
    'website': 'http://www.bistasolutions.com',
    'license': 'AGPL-3',
    'description': """
Purchase Order FOB Enhancement
==============================
* FOB Numbers configuration menu under Purchase > Configuration
* Create, edit, archive, and delete FOB numbers
* Identify duplicate FOB numbers
* Favorite / sequence ordering for the PO FOB dropdown
    """,
    'depends': ['purchase', 'stock', 'web'],
    'data': [
        'security/ir.model.access.csv',
        'views/purchase_fob_views.xml',
        'views/purchase_order.xml',
        'views/res_config_setting.xml',
        'report/purchase_order_report.xml',
    ],

    'installable': True,
    'application': True,

}

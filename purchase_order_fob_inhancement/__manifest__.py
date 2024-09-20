# -*- encoding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2024 (http://www.bistasolutions.com)
#
##############################################################################
{
    'name': 'Bista purchase order Fob Inhacement',
    'category': 'stock',
    'summary': 'This module Offers customize purchase order Fob Inhacement',
    'version': '17.0.1.0.1',
    'author': 'Bista Solutions',
    'website': 'http://www.bistasolutions.com',
    'license': 'AGPL-3',
    'description': """The Delivery Slip Report
    """,
    'depends': ['purchase', 'stock','web',],
    'data': [
        'security/ir.model.access.csv',
        'views/purchase_order.xml',
        'views/res_config_setting.xml',
        'report/purchase_order_report.xml',


],

    'installable': True,
    'application': True,

}

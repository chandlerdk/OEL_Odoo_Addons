# -*- encoding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2024 (http://www.bistasolutions.com)
#
##############################################################################
{
    'name': 'Bista Follow Up',
    'category': 'stock',
    'summary': 'This module Offers customize Delivery Slip',
    'version': '17.0.1.0.1',
    'author': 'Bista Solutions',
    'website': 'http://www.bistasolutions.com',
    'license': 'AGPL-3',
    'description': """The Follow up Report
    """,
    'depends': ['sale', 'stock','web','account_followup','account', 'mail', 'sms', 'account_reports'],
    'data': [
            'data/bista_followup_report_changes.xml',
],

    'installable': True,
    'application': True,

}

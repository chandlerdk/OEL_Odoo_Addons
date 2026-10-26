# -*- coding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (http://www.bistasolutions.com)
#
##############################################################################
{
    'name': "bista_partner_sequence",
    'summary': "Partner Sequence",
    'description': "Partner Sequence",
    'category': 'Res Partner',
    'author': 'Bista Solutions',
    'website': 'http://www.bistasolutions.com',
    'license': 'AGPL-3',
    'version': '20.0.0.0.0',
    'depends': ['base', 'sale'],
    'data': [
        'data/sequence.xml',
        'views/res_partner_view.xml',
    ],
    'post_init_hook': '_assign_partner_reference_no',
}

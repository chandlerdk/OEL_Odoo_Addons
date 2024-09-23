# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.
{

    # App information
    'name': 'RMA Order Sync To Shipstation',
    'version': '17.0.1.0',
    'category': 'Sales',
    'license': 'OPL-1',
    'summary': "Manage RMA Order Sync To Shipstation.",

    # Author
    'author': 'Bista Solutions Pvt. Ltd.',
    'maintainer': 'Bista Solutions Pvt. Ltd.',
    'website': "https://www.bistasolutions.com/",

    # Dependencies
    'depends': ['delivery', 'rma_ept', 'repair','bista_shipstation'],

    'data': [
        'views/crm_claim_ept_view.xml',

    ],

    # Odoo Store Specific
    # 'images': ['static/description/RMA-v15.png'],

    # Technical
    'installable': True,
    'auto_install': False,
    'application': True,
    'active': False,
}

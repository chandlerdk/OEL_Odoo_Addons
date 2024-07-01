# -*- coding: utf-8 -*-
##############################################################################
#
# Handylea
# Copyright (C) 2021 (https://www.handylea.com)
#
##############################################################################
{
    'name': 'Bista Partner Address Autocomplete',
    'version': '1.0.0',
    'category': 'MRP',
    'license': 'LGPL-3',
    'description': '''Autocomplete addresses using google places api. use "address_autocomplete" widget on teh street 
                        field and add google places epi in the settings. When user types a location, odoo will suggest 
                        similar address. Once the address is selected odoo will automatically fill City, State, 
                        Country and zip code''',
    'author': 'Omid Totakhel',
    'maintainer': 'Handylea Support Team',
    'website': 'http://www.handylea.com',
    'depends': ['web','base_setup'],
    'data': [
        'views/res_config_settings.xml',
        'views/partner.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'bista_partner_address_autocomplete/static/src/js/google_places.js',
            'https://polyfill.io/v3/polyfill.min.js?features=default',
            'https://fonts.googleapis.com/css?family=Roboto:400,500',
            'https://unpkg.com/@googlemaps/js-api-loader@1.x/dist/index.min.js'
        ],
    },
    'installable': True,
    'auto_install': False,
}

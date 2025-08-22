# -*- coding: utf-8 -*-
{
    'name': 'Advanced Global Search and Record Finder',
    'version': '17.0.0.0',
    'category': 'Extra Addons',
    "license": "OPL-1",
    'summary': 'Advance Global Search and Record Finder is a powerful and user-friendly Odoo app designed to simplify record search and navigation.',
    'description': """
    The Advance Global Search and Record Finder is a powerful and intuitive Odoo application designed to streamline record management and navigation. It allows users to perform global searches across all configured models and fields directly from the dashboard. By providing a unified search experience with results grouped by models, this app simplifies access to records, enhances productivity, and reduces the time spent locating critical information.
    """,
    "price": 10,
    "currency": 'EUR',
    'author': 'Sitaram',
    'depends': ['web', 'base'],
    'data': [
        'security/ir.model.access.csv',
        'views/global_search_views.xml',
        'data/global_search_data.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'sr_global_search/static/src/js/global_search.js',
            'sr_global_search/static/src/xml/global_search.xml',
            'sr_global_search/static/src/js/command_palette.js',
        ],
    },
    'website': 'https://www.sitaramsolutions.in',
    'application': True,
    'installable': True,
    'auto_install': False,
    'live_test_url': 'https://youtu.be/Q4KqighrBMw',
    "images": ['static/description/banner.jpg'],
    'post_init_hook': 'post_init_hook',
}

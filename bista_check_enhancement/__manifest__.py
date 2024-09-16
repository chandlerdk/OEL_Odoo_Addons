# -*- coding: utf-8 -*-

{
    'name': 'Bista Check Enhancement',
    'version': '17.0',
    'category': 'Accounting',
    'summary': 'Check History for Payments',
    'description': """
    This module use for vendor check payment management.
- Management Check history
- Check Void/Reversal Entry
- Partial Payment Void & Reverse
- Check Logs Management
""",
    'author': 'Bista Solutions Inc',
    'website': 'http://www.bistasolutions.com',
    'images': [],
    'depends': ['account', 'l10n_us_check_printing', ],
    'data': [
        'views/print_check.xml',
        'views/account_report.xml',
        ],
    'demo': [],
    'test': [],
    'qweb': [
    ],
    'installable': True,
    'auto_install': False,
    'application': True,
    'license': 'AGPL-3',
    'assets': {
            'web.report_assets_common': [
                'bista_check_enhancement/static/**/*',
            ],
        },
}

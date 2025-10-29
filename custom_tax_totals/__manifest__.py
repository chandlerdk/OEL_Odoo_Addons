{
    'name': 'Custom Tax Totals',
    'version': '17.0.1.0.0',
    'category': 'Accounting',
    'summary': 'Simplified tax totals display (Untaxed, Taxes, Total)',
    'description': '''
        Replaces the detailed tax breakdown with a clean 3-line format:
        - Untaxed Amount
        - Taxes (summary)
        - Total

        Applies to Sales Orders, Purchase Orders, and Invoices.
    ''',
    'author': 'OEL WorldWide',
    'depends': ['account', 'sale'],
    'data': [
        'views/tax_totals_template.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}

{
    'name': 'Invoice Delivery Reference',
    'version': '1.0',
    'category': 'Accounting',
    'summary': 'Show delivery and tracking references on invoices from sale orders',
    'author': 'Your Name',
    'depends': ['account', 'sale', 'stock', 'delivery'],  # Added 'delivery' dependency
    'data': [
        'views/account_move_view_inherit.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}

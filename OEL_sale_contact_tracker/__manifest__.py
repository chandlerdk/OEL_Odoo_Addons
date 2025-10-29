# -*- coding: utf-8 -*-
{
    'name': "Sales Contact Tracker",
    'version': '17.0.1.0.0',
    'category': 'Sales',
    'description': 'Track all contacts that have been touched by sales orders and quotes, '
                   'including email recipients and manual entries for follow-up purposes.',
    'summary': 'Track touched contacts on sales orders for better follow-up management.',
    'author': 'Your Company',
    'website': 'https://www.yourcompany.com',
    'depends': ['sale_management', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'views/sale_order_views.xml',
    ],
    'license': 'LGPL-3',
    'installable': True,
    'application': False,
    'auto_install': False,
}

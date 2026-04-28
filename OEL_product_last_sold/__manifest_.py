{
    'name': 'OEL Product Last Sold',
    'version': '17.0.1.0.0',
    'category': 'Custom',
    'summary': 'Adds a Last Sold date field to products',
    'depends': ['sale', 'stock'],
    'data': [
        'views/product_template_views.xml',
    ],
    'installable': True,
    'auto_install': False,
    'license': 'LGPL-3',
}

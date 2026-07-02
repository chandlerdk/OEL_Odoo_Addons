{
    'name': 'Enable Customer Visibility Rules',
    'version': '1.0',
    'category': 'Sales',
    'summary': 'Restrict product visibility on quotes based on customer lists',
    'depends': ['sale', 'product'],
    'data': [
        'views/product_template_views.xml',
        'views/sale_order_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}

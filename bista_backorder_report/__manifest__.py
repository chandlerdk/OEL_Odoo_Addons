{
    'name': "Bista - Back Order Report",
    'description': "Back Order Report",
    'category': 'stock',
    'license': 'LGPL-3',
    'version': '17.0.0.0.0',
    'application': True,
    'depends': ['purchase', 'sale', 'base', 'stock'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/backorder_view.xml',
    ],
}
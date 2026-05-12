{
    'name': 'Stock Picking Tags',
    'version': '17.0.1.0.0',
    'summary': 'Add tags to Receipts and Deliveries',
    'category': 'Inventory',
    'depends': ['stock'],
    'data': [
        'security/ir.model.access.csv',
        'views/stock_picking_tag_views.xml',
        'views/stock_picking_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}

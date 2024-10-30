{
    'name': "Bista - Back Order Report",
    'description': "Back Order Report",
    'category': 'stock',
    'version': '17.0.0.0.0',
    'website': "https://www.bistasolutions.com",
    'license': 'AGPL-3',
    'category': 'Back Order report',
    'images': ['static/description/icon.png'],
    'application': True,
    'depends': ['purchase','sale','base','stock'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/backorder_view.xml',
    ],
}
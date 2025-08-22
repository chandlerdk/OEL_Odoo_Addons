{
    'name': 'Sale Team Assignment by Shipping Address',
    'version': '17.0.1.0.0',
    'summary': 'Auto-assign Sales Team on Sales Orders based on delivery address',
    'description': """
    Automatically assigns the Sales Team (team_id) on Sales Orders based on the
    Partner Shipping Address (partner_shipping_id). Falls back to the main
    customer (partner_id) if the shipping address has no team assigned.
    """,
    'category': 'Sales',
    'author': 'OEL Worldwide',
    'website': 'https://www.oelsales.com',
    'license': 'LGPL-3',

    'depends': [
        'sale','base'
    ],

    'data': [
#        'views/res_partner_view.xml'   
        ],

    'installable': True,
    'application': False,
    'auto_install': False,
}

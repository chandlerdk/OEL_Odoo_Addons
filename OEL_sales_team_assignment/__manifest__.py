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
    'website': 'https://www.oelworldwide.com',
    'license': 'LGPL-3',

    'depends': [
        'sale','OEL_sales_team_restriction',  # ensure restriction logic is available
    ],

    'data': [],  # no XML views or security needed for auto-assignment

    'installable': True,
    'application': False,
    'auto_install': False,
}

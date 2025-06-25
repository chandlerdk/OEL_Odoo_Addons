{
    'name': 'Sale Team Change Restriction',
    'version': '17.0.1.0.0',
    'summary': 'Restrict editing of the Sales Team field to users in the Sales Team Manager group',
    'description': """This module restricts the ability to set or change the Sales Team (team_id) field on Sales Orders
    only to users who belong to the 'Sales Team Manager' group.""",
    'category': 'Sales',
    'author': 'OEL Worldwide',
    'website': 'https://www.oelworldwide.com',
    'license': 'LGPL-3',

    'depends': [
        'sale'],

    'data': [
        'security/group_data.xml',
        'security/ir.model.access.csv',
        'views/sale_order_view.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}

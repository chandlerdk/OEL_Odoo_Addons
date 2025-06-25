{
    'name': 'Sale Order Confirm Date Wizard',
    'version': '17.0.1.0.0',
    'summary': 'Prompt users to confirm or update the order date when reconfirming a cancelled sales order',
    'description': """This module adds a wizard that prompts users to either keep the original order date or update it to the current confirmation date when reconfirming a cancelled sales order. It ensures that daily sales order counts remain accurate by preventing unintended date changes.""",
    'category': 'Sales',
    'author': 'OEL Worldwide',
    'website': 'https://www.oelsales.com',
    'license': 'LGPL-3',

    'depends': [
        'sale',
    ],

    'data': [
        'security/ir.model.access.csv',
        'views/sale_order_date_wizard_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}

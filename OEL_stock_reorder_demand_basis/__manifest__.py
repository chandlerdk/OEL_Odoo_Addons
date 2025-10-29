# -*- coding: utf-8 -*-
{
    'name': 'Reorder Demand Basis (Forecast vs Forecasted)',
    'summary': 'Per reordering rule: choose Forecast (On Hand + Incoming) or Forecasted (On Hand + Incoming - Outgoing).',
    'description': 'Adds a selection on stock.warehouse.orderpoint and uses it in qty computation for replenishment.',
    'version': '17.0.1.0.0',
    'author': 'OEL Worldwide',
    'license': 'LGPL-3',
    'category': 'Inventory/Inventory',
    'depends': ['stock'],
    'data': [
        'views/orderpoint_views.xml',
#        'data/ir_default.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}

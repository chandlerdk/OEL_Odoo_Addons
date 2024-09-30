# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name': "Negative Forecast Report",
    'description': "Negative Forecast Report",
    'category': 'stock',
    'license': 'LGPL-3',
    'version': '17.0.0.0.0',
    'application': True,
    'depends': ['purchase', 'sale', 'base', 'stock'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/negative_forecasts_view.xml',
    ],
}

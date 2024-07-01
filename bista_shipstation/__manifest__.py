# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name': "ShipStation Odoo Integration",
    'description': "ShipStation Odoo Integration",
    'category': 'Operations/Inventory/Delivery',
    'license': 'LGPL-3',
    'version': '17.0.0.0.0',
    'application': True,
    'depends': ['delivery','stock_delivery','mail', 'sale', 'base','stock'],
    'data': [
        'security/ir.model.access.csv',
        'security/group_data.xml',
        'views/delivery_carrier_views.xml',
        'views/product_product.xml',
        'wizards/choose_delivery_carrier.xml',
        'views/cron.xml',
        'views/stock.xml',
        'views/shipstation_store.xml',
        'views/res_partner.xml',
        'views/sale_order.xml',
        'views/stock_picking.xml',
        'views/shipstation_carrier_view.xml',
        'views/shipstation_service_view.xml',
        'views/res_config_settings_view.xml',
    ],
}

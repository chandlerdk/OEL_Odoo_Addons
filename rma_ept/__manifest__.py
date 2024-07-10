# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.
{

    # App information
    'name': 'RMA (Return Merchandise Authorization) in Odoo',
    'version': '17.0.1.0',
    'category': 'Sales',
    'license': 'OPL-1',
    'summary': "Manage Return Merchandize Authorization (RMA) in Odoo. Allow users to manage Return Orders, "
               "Replacement, Refund & Repair in Odoo.",

    # Author
    'author': 'Emipro Technologies Pvt. Ltd.',
    'maintainer': 'Emipro Technologies Pvt. Ltd.',
    'website': "https://www.emiprotechnologies.com/",

    # Dependencies
    'depends': ['delivery', 'crm', 'repair'],

    'data': [
        'report/rma_report.xml',
        'data/rma_reason_ept.xml',
        'data/mail_template_data.xml',
        'data/crm_claim_ept_sequence.xml',
        'security/res_groups.xml',
        'security/ir.model.access.csv',
        'report/rma_report_template.xml',
        'views/view_account_invoice.xml',
        'views/crm_claim_ept_view.xml',
        'views/view_stock_picking.xml',
        'views/rma_reason_ept.xml',
        'views/view_stock_warehouse.xml',
        'views/sale_order_view.xml',
        'views/repair_order_view.xml',
        'views/claim_reject_message.xml',
        'wizard/view_claim_process_wizard.xml',
        'wizard/create_partner_delivery_address_view.xml',
        'wizard/res_config_settings.xml'
    ],

    # Odoo Store Specific
    'images': ['static/description/RMA-v15.png'],

    # Technical
    'installable': True,
    'auto_install': False,
    'application': True,
    'active': False,
}

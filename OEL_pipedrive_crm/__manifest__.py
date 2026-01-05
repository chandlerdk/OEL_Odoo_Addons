# -*- coding: utf-8 -*-
{
    'name': 'OEL Sales Hub',
    'version': '1.0.0',
    'category': 'Sales/CRM',
    'summary': 'Simplified CRM module similar to Pipedrive for organization profiles',
    'description': """
OEL Sales Hub - Pipedrive-style CRM
===================================

A simplified CRM module focused on organization profiles with:
- Lead management and conversion to prospects
- Customer management with organization-specific fields
- Activity tracking
- Quote management
- Order management
- Integrated email communication
- Streamlined interface similar to Pipedrive

Key Features:
- Lead inbox with convert-to-prospect action
- Organization-focused customer profiles
- Send email directly from contact profile
- Mail History tracking (incoming/outgoing)
- Custom fields for industry, priority, and status
- Integrated activities, quotes, and orders
- Clean, simple interface
    """,

    'author': 'OEL Worldwide',
    'website': 'https://www.oelsales.com',
    'depends': [
        'base',
        'sale',
        'crm',
        'mail',
        'calendar',
    ],

    'data': [
        'security/ir.model.access.csv',
#        'data/activity_server_actions.xml',
        'data/recalculate_prospects.xml',
        'data/subcontact_server_actions.xml',
        'data/lead_server_actions.xml',  # <-- NEW: server action for lead conversion
#        'wizards/oel_mail_compose_wizard_views.xml',
#        'views/oel_mail_history_views.xml',
        'views/res_partner_views.xml',
        'views/activity_views.xml',
        'views/lead_views.xml',  # <-- NEW: leads action & tree view
        'views/quote_views.xml',
        'views/order_views.xml',
        'views/sale_views.xml',
        'views/menu_views.xml',
    ],

    'installable': True,
    'auto_install': False,
    'application': True,
    'sequence': 10,
}

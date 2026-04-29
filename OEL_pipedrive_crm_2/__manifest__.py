# -*- coding: utf-8 -*-
{
    'name': 'OEL Sales Hub v2 (Opportunities)',
    'version': '17.0.2.0',
    'category': 'Sales/CRM',
    'summary': 'Opportunity-based CRM: Prospects & Customers on crm.lead, with partner-based customers/contacts lists',
    'description': """
OEL Sales Hub v2 - Opportunity-based CRM
- Prospects/Customers menus in Acquisition are based on crm.lead (type='opportunity').
- Separate Customers / Contacts / Companies menus are based on res.partner.
- Does NOT override the standard base res.partner form/list; only adds helper field and custom actions.
- Keeps CRM logic isolated from generic contacts as much as possible.
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
        'views/activity_views.xml',
        'views/crm_lead_views.xml',
        'views/lead_views.xml',
        'views/quote_views.xml',
        'views/order_views.xml',
        'views/sale_views.xml',
        'views/menu_views.xml',
        'data/sync_contacts_cron.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': True,
    'sequence': 20,
}

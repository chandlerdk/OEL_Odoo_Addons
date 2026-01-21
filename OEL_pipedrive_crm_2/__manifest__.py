# -*- coding: utf-8 -*-
{
    'name': 'OEL Sales Hub v2 (Opportunities)',
    'version': '2.0.0',
    'category': 'Sales/CRM',
    'summary': 'Opportunity-based CRM: Prospects & Customers on crm.lead, with partner-based customers/contacts lists',
    'description': """
OEL Sales Hub v2 - Opportunity-based CRM
========================================

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

        # Models / helpers
        'views/activity_views.xml',        # My Activities + My Calendar on mail.activity

        # CRM Lead (opportunity) forms, trees, search, actions
        'views/crm_lead_views.xml',
        # Lead inbox (reusing your simplified lead list concept)
        'views/lead_views.xml',

        # Sales related actions (quotes, orders) under this app
        'views/quote_views.xml',
        'views/order_views.xml',
        'views/sale_views.xml',

        # Main menus for v2 app
        'views/menu_views.xml',

        'data/sync_contacts_cron.xml',
    ],

    'installable': True,
    'auto_install': False,
    'application': True,
    'sequence': 20,
}

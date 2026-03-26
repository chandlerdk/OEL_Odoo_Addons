# -*- coding: utf-8 -*-
{
    'name': 'Disable Invoice Assignment Notifications',
    'version': '17.0.1.0.0',
    'category': 'Accounting',
    'summary': 'Disable automatic email notifications when users are assigned to draft invoices',
    'description': """
        Disable Invoice Assignment Notifications
        =========================================
        
        This module prevents Odoo from sending automatic email notifications 
        when a user is assigned to a draft invoice.
        
        Features:
        ---------
        * Suppresses "You have been assigned a draft invoice" emails
        * Keeps the assignment functionality intact
        * Only affects draft invoices
        * Reduces email clutter for users
    """,
    'author': 'Your Company',
    'website': 'https://www.yourcompany.com',
    'license': 'LGPL-3',
    'depends': [
        'account',
        'mail',
    ],
    'data': [],
    'installable': True,
    'application': False,
    'auto_install': False,
}

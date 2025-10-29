{
    'name': 'Lead Assignment Notifications',
    'version': '1.0.0',
    'category': 'CRM',
    'summary': 'Automatic email notifications for lead assignments and welcome emails',
    'description': """
        This module provides automatic email notifications when:
        - A salesperson is assigned to a lead (including during imports)
        - A new lead is created (welcome email to the customer)
        
        Features:
        - Works during bulk imports (unlike native Odoo notifications)
        - Prevents duplicate emails
        - Customizable email templates
    """,
    'author': 'OEL Worldwide',
    'depends': ['crm', 'mail', 'base_import'],
    'data': [
    #    'security/ir.model.access.csv',
        'data/mail_templates.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}

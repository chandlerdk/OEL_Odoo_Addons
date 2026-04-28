{
    'name': 'Sales Commission Review',
    'version': '17.0.1.0.4',
    'category': 'Accounting',
    'summary': 'Review sales commissions by payment date with freight deduction',
    'description': """
Sales Commission Review Report
===============================
This module provides a wizard-based report to review sales commissions:
- Select a month/year to analyze
- Automatically computes payment dates from customer payments
- Deducts freight charges from commissionable totals
- Groups by sales team and salesperson
    """,
    'depends': ['account'],
    'data': [
        'security/ir.model.access.csv',
        'views/commission_dashboard_views.xml',
        'views/commission_prepare_wizard_views.xml',
        'views/account_move_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}

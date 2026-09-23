# -*- coding: utf-8 -*-
{
    'name': 'OEL Credit Memo Report',
    'version': '17.0.1.0.0',
    'category': 'Accounting',
    'summary': 'Custom Credit Memo PDF report with correct negative sign display',
    'author': 'OEL',
    'depends': ['account'],
    'data': [
        'data/email_template_credit_memo.xml',
        'report/ir_actions_report.xml',
        'report/report_credit_memo_templates.xml',
    ],
    'installable': True,
    'auto_install': False,
    'license': 'LGPL-3',
}

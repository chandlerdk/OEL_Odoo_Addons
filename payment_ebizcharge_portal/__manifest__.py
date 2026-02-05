# -*- coding: utf-8 -*-
{
    'name': "EBizCharge Payment Acquirer Website",

    'summary': "Payment Acquirer: EBizCharge Implementation",

    'description': """EBizCharge Payment Acquirer""",
    'category': 'Website/Payment',
    'version': '17.1',

    # any module necessary for this one to work correctly
    'depends': ['base', 'website_sale', 'payment_ebizcharge_crm'],

    # always loaded
    'data': [
        'views/ebizcharge_instance_config_view.xml',
        'views/payment_ebizcharge_templates.xml',
        'views/ebiz_payment_portal_templates.xml',
        'views/transaction_portal_template.xml',
    ],
    'external_dependencies': {'python': ['zeep']},
    'assets': {
        'web.assets_frontend': [
            # 'payment_ebizcharge_portal/static/src/scss/portal_payment.scss',
            'payment_ebizcharge_portal/static/src/js/payment_form.js',
            'payment_ebizcharge_portal/static/src/js/ebiz_manage_form.js',
            # 'payment_ebizcharge_portal/static/src/js/manage_form.js',
            # 'payment_ebizcharge_portal/static/src/js/checkout_form.js',
            'payment_ebizcharge_portal/static/lib/jquery.payment/jquery.payment.js',
            'payment_ebizcharge_portal/static/src/js/payment_portal.js',
            # 'payment_ebizcharge_portal/static/src/xml/post_process.xml',
        ],
        'web.assets_backend': [
            'payment_ebizcharge_portal/static/src/scss/backend.scss',
        ],
    },
    'license': 'LGPL-3',

}


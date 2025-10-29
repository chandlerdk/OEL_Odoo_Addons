{
    "name": "OEL Sales & Accounting Email Templates",
    "summary": "Email templates for quotations, order confirmations, and invoices",
    "version": "17.0.1.0.0",
    "author": "OEL",
    "website": "https://www.oelsales.com",
    "license": "LGPL-3",
    "category": "Sales",
    "depends": ["sale_management", "account", "portal", "mail"],
    "data": [
        "data/mail_template_quotation.xml",
        "data/mail_template_order_confirmation.xml",
        "data/mail_template_invoice.xml",
    ],
    "installable": True,
    "application": False,
}

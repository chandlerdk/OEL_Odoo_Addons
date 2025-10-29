{
    "name": "Partner Global Invoice Discount",
    "version": "17.0.1.0.0",
    "summary": "Customer-specific global discount injected at invoice level (covers backorders)",
    "category": "Accounting/Accounting",
    "depends": ["account", "sale"],
    "data": [
#        "security/ir.model.access.csv",
        "data/product_data.xml",
        "views/res_partner_views.xml",
#        "views/account_move_views.xml",
    ],
    "application": False,
    "installable": True,
    "license": "LGPL-3",
}

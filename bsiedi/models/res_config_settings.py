from odoo import api, fields, models

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    add_viewlinks_chatter = fields.Boolean(string='Add EDI Viewing Links to Chatter?',related="company_id.add_viewlinks_chatter", readonly=False)
    add_edierrors_chatter = fields.Boolean(string='Add EDI Errors to Chatter?',related="company_id.add_edierrors_chatter", readonly=False)
    add_trxupds_chatter = fields.Boolean(string='Add EDI Updates from Transactions to Chatter?',related="company_id.add_trxupds_chatter", readonly=False)
    add_ediack_chatter = fields.Boolean(string='Add EDI Acknowledgements to Chatter?',related="company_id.add_ediack_chatter", readonly=False)
    inv_feed_locs = fields.Many2many('stock.location',string='Warehouse Locations for EDI Inventory Feed',related="company_id.inv_feed_locs", readonly=False)
    inv_feed_intransit_locs = fields.Many2many('stock.location',string='Stock In=Transit Locations for EDI Inventory Feed',related="company_id.inv_feed_intransit_locs", readonly=False)
    purch_ord_tags = fields.Many2many('crm.tag',string='Purchase order tags to select for EDI?',related="company_id.purch_ord_tags", readonly=False,relation='purchordtags_crmtag')
    sales_ord_tags = fields.Many2many('crm.tag',string='Sales order tags to apply for EDI?',related="company_id.sales_ord_tags", readonly=False)
    invoice_line_accounts = fields.Many2many('account.account',string='Outbound Invoice Sales Line Accounts',related="company_id.invoice_line_accounts",readonly=False,relation='invoicelineacct_acct')
    freight_line_accounts = fields.Many2many('account.account',string='Outbound Invoice Freight Accounts',related="company_id.freight_line_accounts", readonly=False,relation='freightlineacct_acct')
    misc_line_accounts = fields.Many2many('account.account',string='Outbound Invoice Miscellaneous Charge Accounts',related="company_id.misc_line_accounts", readonly=False,relation='misclineacct_acct')
    tax_line_accounts = fields.Many2many('account.account',string='Outbound Invoice Tax Accounts',related="company_id.tax_line_accounts", readonly=False,relation='taxlineacct_acct')

    gln = fields.Char(string='GLN', related="company_id.gln",readonly=False)
    duns_number = fields.Char(string='Duns Number', related="company_id.duns_number",readonly=False)
    ucc_company_code = fields.Char(string='UCC Company Code', related="company_id.ucc_company_code",readonly=False)

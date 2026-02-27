# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class Company(models.Model):
    _inherit = 'res.company'

    add_viewlinks_chatter = fields.Boolean(string='Add EDI Viewing Links to Chatter?', readonly=False,default=False)
    add_edierrors_chatter = fields.Boolean(string='Add EDI Errors to Chatter?', readonly=False,default=False)
    add_trxupds_chatter = fields.Boolean(string='Add EDI Updates from Transactions to Chatter?', readonly=False,default=False)
    add_ediack_chatter = fields.Boolean(string='Add EDI Acknowledgements to Chatter?', readonly=False,default=False)
    inv_feed_locs = fields.Many2many('stock.location',string='Locations for EDI Inventory Feed')
    inv_feed_intransit_locs = fields.Many2many('stock.location',string='Stock In-Transit Locations for EDI Inventory Feed',relation='sett_invfeed_intran_warehouse')
    purch_ord_tags = fields.Many2many('crm.tag',string='Purchase order tags to select for EDI?',relation='purchordtags_crmtag')
    sales_ord_tags = fields.Many2many('crm.tag',string='Sales order tags to apply for EDI?')
    invoice_line_accounts = fields.Many2many('account.account',string='Outbound Invoice Sales Line Accounts',relation='invoicelineacct_acct')
    freight_line_accounts = fields.Many2many('account.account',string='Outbound Invoice Freight Accounts',relation='freightlineacct_acct')
    misc_line_accounts = fields.Many2many('account.account',string='Outbound Invoice Miscellaneous Charge Accounts',relation='misclineacct_acct')
    tax_line_accounts = fields.Many2many('account.account',string='Outbound Invoice Tax Accounts',relation='taxlineacct_acct')

    gln = fields.Char(string='GLN', required=False,readonly=False)
    duns_number = fields.Char(string='Duns Number', required=False,readonly=False)
    ucc_company_code = fields.Char(string='UCC Company Code', required=False,readonly=False)

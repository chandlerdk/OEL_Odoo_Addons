# -*- coding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (https://www.bistasolutions.com)
#
##############################################################################

from odoo import api, fields, models,_
from dateutil import relativedelta

from odoo.exceptions import UserError


class SaleCommission(models.Model):
    _name = 'sale.commission'
    _description = 'Sale Commission'

    name = fields.Char(required=True)
    expense_account_id = fields.Many2one('account.account',
                                         required=True,
                                         domain=[('account_type', 'in',
                                                  ['expense', 'expense_depreciation', 'expense_direct_cost'])])
    payout_account_id = fields.Many2one('account.account',
                                        required=True)

    ttype = fields.Selection([
        ('product', 'Based on Product'),
        ('product_category', 'Based on Product Category')
    ], string="Type")

    partner_ids = fields.Many2many('res.partner')
    related_partners = fields.Boolean(string="Include Related Partners")
    related_partner_ids = fields.One2many('res.partner', related="partner_ids.child_ids")

    product_ids = fields.Many2many('product.product')
    product_category_ids = fields.Many2many('product.category')

    sale_partner_type = fields.Selection([
        ('user', 'Sales Person'),
        ('sale_team', 'Sales Team')
    ], required=True)
    user_ids = fields.Many2many('res.users')
    sale_team_ids = fields.Many2many('crm.team')
    sale_team_rep = fields.Many2one('res.users')

    percentage = fields.Float()
    amount = fields.Float(string="Minimum Amount")
    discount_rule = fields.Selection([
        ('disqualify', 'Disqualify'),
        ('adjust', 'Adjust Commission'),
    ])

    terms = fields.Html()
    active = fields.Boolean(default=True)
    priority = fields.Integer()

    profit_margin = fields.Boolean()
    share_loss = fields.Boolean()

    payout_policy = fields.Selection([
        ('invoice', 'Invoice Generated'),
        ('payment', 'Invoice Fully Paid')
    ], required=True, default="payment")

    tax_policy = fields.Selection([('price_subtotal', 'Before Tax'), ('price_total', 'After Tax')],
                                  default="price_subtotal")

    @api.depends("sale_team_ids")
    def _onchange_sale_team_ids(self):
        user_ids = self.mapped("sale_team_ids").mapped("member_ids")
        if not self.sale_team_ids or not user_ids:
            return
        self.user_ids = [(6, 0, user_ids.ids)]

    def calculate_amount(self, data):
        """
        @Example
        data = {
            'product_id': product_id,
            'partner_id': partner_id,
            'amount_before_tax': 100,
            'amount_after_tax', 120,
            'quantity': 2,
            'percentage': 10
        }"""

        if not data:
            return

        required_fields = ['product_id',
                           'partner_id',
                           'amount_before_tax',
                           'amount_after_tax',
                           'quantity',
                           'percentage']

        missing_field = [field for field in required_fields if field not in data.keys()]
        if missing_field:
            raise UserError("Missing required fields: %s to calculate Commission" % missing_field)

        data['policy'] = {
            'price_subtotal': data.get("amount_before_tax", 0),
            'price_total': data.get("amount_after_tax", 0),
        }

        # value = data['policy'][self.tax_policy]
        # if not value:
        #     return 0

        tax_policy = self.tax_policy or 'price_subtotal'  # default fallback
        value = data['policy'].get(tax_policy)
        if not value:
            return None

        if not self.validate_rules(data):
            return None
        # validation_result = self.validate_rules(data)
        # if validation_result is not True:
        #     raise UserError(_("Commission cannot be calculated: %s") % validation_result)

        product_id = data['product_id']
        percentage = data['percentage']
        quantity = data['quantity']

        if self.profit_margin and product_id and product_id.standard_price:
            margin = value - (product_id.standard_price * quantity)
            if margin >= 0 or margin < 0 and self.share_loss:
                value = margin
        return (percentage * value) / 100

    def validate_rules(self, data):
        product_rule = self.validate_product_rule(data)
        partner_rule = self.validate_partner_rule(data)
        validate_amount_rule = self.validate_amount_rule(data)
        return product_rule and partner_rule and validate_amount_rule

    # def validate_rules(self, data):
    #     """Return True if all rules pass, else return message of failed rule."""
    #
    #     if not self.validate_product_rule(data):
    #         return "Product Rule Failed"
    #
    #     if not self.validate_partner_rule(data):
    #         return "Partner Rule Failed"
    #
    #     if not self.validate_amount_rule(data):
    #         return "Amount Rule Failed"
    #
    #     return True

    def validate_product_rule(self, data):
        product = data.get("product_id")
        if not product or not self.ttype:
            return True
        if self.product_category_ids and product.categ_id.id in self.product_category_ids.ids:
            return True
        elif self.product_ids and product.id in self.product_ids.ids:
            return True
        return False

    def validate_partner_rule(self, data):
        partner = data.get("partner_id", False)
        if (not self.partner_ids or partner.id in self.partner_ids.ids
                or self.related_partners
                and self.related_partner_ids
                and partner.id in self.related_partner_ids.ids):
            return True
        return False

    def validate_amount_rule(self, data):
        policy = data.get("policy")
        if not self.amount or self.amount < policy[self.tax_policy]:
            return True
        return False

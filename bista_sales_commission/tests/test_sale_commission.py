# Copyright 2021 Tecnativa - Víctor Martínez
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.tests import common, Form, TransactionCase
from odoo import fields
import pandas as pd


class TestSaleCommission(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.categoryA = cls.env['product.category'].create(
            {'name': 'Category B'}
        )
        cls.categoryB = cls.env['product.category'].create(
            {'name': 'Category A'}
        )
        cls.product = cls.env["product.template"].create(
            {'name': 'Product',
             'invoice_policy': 'order',
             'categ_id': cls.categoryA.id,
             'standard_price': 100,
             'list_price': 200,
             })

        cls.service = cls.env['product.template'].create({
            'name': 'Service',
            'categ_id': cls.categoryB.id,
            'standard_price': 500,
            'list_price': 1000
        })

        cls.customer = cls.env['res.partner'].create({'name': 'Customer'})
        cls.customer_child = cls.env['res.partner'].create({'name': 'Customer Child',
                                                            'parent_id': cls.customer.id})

        cls.sale_person = cls.env["res.users"].create({
            "name": "Sale Person",
            "login": "sale_person",
        })

        cls.sale_team = cls.env["crm.team"].create({"name": "Sale Team", "member_ids":
            [(4, cls.sale_person.id)]})

        cls.expense_account = cls.env['account.account'].create({
            'name': 'Expense Account',
            'code': 'EXP001',
            'account_type': 'expense'
        })

        cls.payout_account = cls.env['account.account'].create({
            'name': 'Payout Account',
            'code': 'PAY001',
            'account_type': 'liability_payable'
        })

        cls.sale = cls.env['sale.order'].create({
            'partner_id': cls.customer.id,
            'user_id': cls.sale_person.id,
        })

    def test_profit_margin(self):
        sale_commission = self.env['sale.commission'].create({
            'name': '10% Fixed Commission',
            'expense_account_id': self.expense_account.id,
            'payout_account_id': self.payout_account.id,
            'ttype': 'product_category',
            'partner_ids': [(6, 0, [self.customer.id])],
            'product_category_ids': [(6, 0, [self.categoryA.id])],
            'sale_partner_type': 'user',
            'user_ids': [(6, 0, [self.sale_person.id])],
            'percentage': 10.0,
            'amount': 0,
            'profit_margin': True,
            'terms': '<p>Commission Terms</p>',
            'priority': 1
        })

        line = self.env['sale.order.line'].create({
            'name': self.product.name,
            'product_id': self.product.id,
            'product_uom_qty': 2,
            'product_uom': self.product.uom_id.id,
            'price_unit': self.product.list_price,
            'order_id': self.sale.id
        })



    def log_message(self, sale_commission, line):
        columns = ['name',
                   'expense_account_id',
                   'payout_account_id',
                   'ttype',
                   'partner_ids',
                   'related_partners',
                   'related_partner_ids',
                   'product_ids',
                   'product_category_ids',
                   'sale_partner_type',
                   'user_ids',
                   'sale_team_ids',
                   'percentage',
                   'amount',
                   'profit_margin',
                   'terms',
                   'priority']

        column_values = "\n".join([f"{column}: {sale_commission[column]}" for column in columns])
        return f"""
        Sales Commission
        {column_values}
        Product Price: {line.price_unit}
        Product Cost: {line.product_id.standard_price}
        Commission: {line.commission_percent}
        Commission ID: {line.commission_id}
        """

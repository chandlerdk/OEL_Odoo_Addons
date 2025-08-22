from odoo import fields,models
from odoo.tools.float_utils import float_compare, float_is_zero, float_round

from collections import defaultdict
from odoo import _, api, Command, fields, models
from odoo.exceptions import UserError
from odoo.osv import expression
from odoo.osv.expression import OR
from odoo.tools.float_utils import float_compare, float_is_zero, float_round
from odoo.tools.misc import clean_context, OrderedSet, groupby

class StockPicking(models.Model):
    _inherit = 'stock.picking'

    @api.model
    def _create_account_move_for_receipt(self, move_line):
        print("&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&")
        product = move_line.product_id
        qty = move_line.quantity
        cost = product.standard_price * qty

        if cost <= 0:
            return

        move_vals = {
            'move_type': 'entry',
            'journal_id': self.env.ref('account.1_inventory_valuation').id,
            'date': fields.Date.today(),
            'line_ids': [
                (0, 0, {
                    'name': f'Receipt of {product.name}',
                    'account_id': product.categ_id.property_stock_valuation_account_id.id,
                    'debit': cost,
                    'credit': 0.0,
                }),
                (0, 0, {
                    'name': f'Receipt of {product.name}',
                    'account_id': product.categ_id.property_stock_account_input_categ_id.id,
                    'debit': 0.0,
                    'credit': cost,
                }),
            ]
        }

        move = self.env['account.move'].create(move_vals)
        print("qqqqqqqqqqqqqqqqqqqqqqq",move)

    def button_validate_custom(self):
        for move in self:
            print("++++++++++++++++++++",move)
            if move.picking_type_id.code == 'incoming':
                print("RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR")
                for line in move.move_ids_without_package:
                    print("@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@",line)
                    self._create_account_move_for_receipt(line)
        return True









from odoo import fields, models
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

    def update_src_loaction_account_entry(self):
        for picking in self:
            if picking.picking_type_id.id == 1 and picking.location_id.id == 13 and picking.state == 'done':
                picking.move_ids.write({'is_force_accounting': True})
                picking.move_line_ids.write({'is_force_accounting': True, 'location_id': 4})
                picking.write({'location_id': 4})
                svl = picking.move_ids._create_in_svl()
                svl._validate_accounting_entries()
            if picking.picking_type_id.id == 2 and picking.location_dest_id.id == 13 and picking.state == 'done':
                picking.move_ids.write({'is_force_accounting': True})
                picking.move_line_ids.write({'is_force_accounting': True, 'location_dest_id': 5})
                picking.write({'location_dest_id': 5})
                picking.move_ids.stock_valuation_layer_ids.sudo().unlink()
                svl = picking.move_ids._create_out_svl()
                svl._validate_accounting_entries()


class StockMove(models.Model):
    _inherit = 'stock.move'

    is_force_accounting = fields.Boolean()

class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    is_force_accounting = fields.Boolean()

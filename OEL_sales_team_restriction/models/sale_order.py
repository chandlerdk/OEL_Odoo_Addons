# -- coding: utf-8 --
#
#########################################

from odoo import models, api 
from odoo.exceptions import AccessError

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.model
    def create(self, vals):
        if 'team_id' in vals and not self.env.user.has_group('OEL_sales_team_restriction.group_sale_team_manager'):
            raise AccessError("You are not allowed to set the Sales Team.")
        return super(SaleOrder, self).create(vals)

    def write(self, vals):
        if 'team_id' in vals and not self.env.user.has_group('OEL_sales_team_restriction.group_sale_team_manager'):
            raise AccessError("You are not allowed to change the Sales Team.")
        return super(SaleOrder, self).write(vals)

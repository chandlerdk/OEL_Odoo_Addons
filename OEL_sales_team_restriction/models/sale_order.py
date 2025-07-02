# -*- coding: utf-8 -*-
from odoo import models, api
from odoo.exceptions import AccessError

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.model
    def create(self, vals):
        # if they try to set a team on create and aren't in the manager group, block
        team_id = vals.get('team_id')
        if team_id and not self.env.user.has_group(
            'OEL_sales_team_restriction.group_sale_team_manager'
        ):
            raise AccessError("You are not allowed to set the Sales Team.")
        return super().create(vals)

    def write(self, vals):
        # only enforce on actual team changes
        if 'team_id' in vals:
            new_id = vals.get('team_id')
            # skip if nobody is really changing it
            for order in self:
                if order.team_id.id != new_id:
                    if not self.env.user.has_group(
                        'OEL_sales_team_restriction.group_sale_team_manager'
                    ):
                        raise AccessError("You are not allowed to change the Sales Team.")
        return super().write(vals)


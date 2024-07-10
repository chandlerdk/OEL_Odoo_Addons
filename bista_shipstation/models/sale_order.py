# -*- coding: utf-8 -*-
###############################################################################
#    License, author and contributors information in:                         #
#    __manifest__.py file at the root folder of this module.                  #
###############################################################################

from odoo import models, fields, api, _
from datetime import timedelta


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    delivery_pay = fields.Boolean('Delivery ShipStation Pay', store=True, copy=False, default=False)
    ss_quotation_carrier = fields.Char(string='Shipstation carrier code',copy=False)
    ss_quotation_service = fields.Char(string='Shipstation service code',copy=False)
    ship_via = fields.Char('Ship Via', store=True,copy=False)
    is_synced = fields.Boolean(copy=False, string="Add Shipping Cost")
    no_ship_cost_synced = fields.Boolean(copy=False, string="No Shipping Cost Sync")
    add_ship_no_delivery_line = fields.Boolean(string="shipping cost",copy=False)


    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        for line in self:
            if line.partner_id:
                line.ship_via = line.partner_id.ship_via

    def action_confirm(self):
        res = super(SaleOrder, self).action_confirm()
        order_confirm_hour = int(
            self.env['ir.config_parameter'].sudo().get_param('bista_shipstation.order_confirm_hour'))
        if order_confirm_hour and not self.commitment_date:
            for order in self:
                date_order = order.date_order + timedelta(hours=order_confirm_hour)
                order.write({'commitment_date': date_order})
        return res

    def get_tracking_ref(self):
        for x in self.order_line:
            x._get_tracking_ref()

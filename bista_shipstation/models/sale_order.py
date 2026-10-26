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
    bill_account = fields.Char('Account No')
    bill_postal_code = fields.Char('Postal Code')
    bill_country_code = fields.Many2one('res.country', string="Country")
    ship_carrier_id = fields.Many2one('shipstation.delivery.carrier')
    service_id = fields.Many2one('shipstation.carrier.service')
    ups_bill_my_account = fields.Boolean(related='ship_carrier_id.ups_bill_my_account', readonly=True)

    @api.onchange('partner_id')
    def _onchange_shipstation_third_acc(self):
        for rec in self:
            if rec.partner_id:
                rec.update({
                    'bill_account': rec.partner_id.bill_account,
                    'bill_postal_code': rec.partner_id.bill_postal_code,
                    'bill_country_code': rec.partner_id.bill_country_code,
                    'ship_carrier_id': rec.partner_id.carrier_id,
                    'service_id': rec.partner_id.service_id,
                })

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        for line in self:
            if line.partner_id:
                line.ship_via = line.partner_id.ship_via

    def action_confirm(self):
        res = super().action_confirm()
        for order in self.filtered(lambda sale: not sale.commitment_date):
            raw_hour = order.company_id.order_confirm_hour
            if not raw_hour:
                continue
            try:
                hours = int(raw_hour)
            except (TypeError, ValueError):
                continue
            if hours and order.date_order:
                order.commitment_date = order.date_order + timedelta(hours=hours)
        return res

    def get_tracking_ref(self):
        for x in self.order_line:
            x._get_tracking_ref()

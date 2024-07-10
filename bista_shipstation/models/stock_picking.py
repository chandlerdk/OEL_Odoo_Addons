# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
from odoo import fields, models, api
from odoo.exceptions import UserError
from requests.auth import HTTPBasicAuth
import logging
import requests
import json

_logger = logging.getLogger("Shipstation")


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    shipstation_order_id = fields.Char("ShipStation Order Reference", copy=False)
    shipstation_order_key = fields.Char('ShipStation Order Key', copy=False)
    shipstation_service = fields.Char("ShipStation Service")
    shipstation_service_code = fields.Char("ShipStation Service Code")
    delivery_type = fields.Selection(related='carrier_id.delivery_type')
    add_service_line = fields.Boolean(string="Delivery Cost")

    # @api.model
    # def create(self, values):
    #     print(">>>valuesssss",values)
    #     if 'backorder_id' in values and values['backorder_id']:
    #         values['add_service_line'] = False
    #     else:
    #         values['add_service_line'] = True
    #     return super().create(values)

    @api.model
    def create(self, values):
        carrier = self.env['delivery.carrier'].search([('delivery_type', '=', 'shipstation')], limit=1)
        if 'backorder_id' in values and values['backorder_id']:
            if carrier and not carrier.remove_backorder_ship_line:
                values['add_service_line'] = True
            else:
                values['add_service_line'] = False
        else:
            values['add_service_line'] = True

        return super().create(values)

    def _send_confirmation_email(self):
        res = super(StockPicking, self)._send_confirmation_email()
        self.user_id = self.env.uid
        for picking in self.sudo():
            carrier_id = picking.env['delivery.carrier'].search(
                [('delivery_type', '=', 'shipstation'),
                 ('company_id', '=', picking.company_id.id)], limit=1)
            if carrier_id and picking.picking_type_id.code == 'outgoing' and not picking.carrier_id:
                url = '/orders?' + 'orderNumber=' + picking.name
                resp = carrier_id._get_shipstation_data(url)
                if resp.get('orders') and resp.get('orders')[0].get('advancedOptions').get('storeId') == int(
                        carrier_id.store_id.store_id):
                    _logger.info(f"Shipstation: Order already exists in store skipping {picking.name}")
                    pass
                else:
                    _logger.info(f"Shipstation: Creating shipstation order {picking.name}")
                    carrier_id.shipstation_send_shipping(picking)
            elif picking.carrier_id and picking.picking_type_id.code == 'outgoing':
                pass
        return res


    def get_tracking(self):
        if not self.shipstation_order_id:
            raise UserError("This order is not synced to shipstation")
        ship_station = self.carrier_id or self.env['delivery.carrier'].sudo().search(
            [('delivery_type', '=', 'shipstation'),
             ('company_id.id', '=', self.env.company.id)], limit=1)
        if not ship_station:
            raise UserError("No shipstation shipping method found!")
        ship_station._webhook_trigger(self.shipstation_order_id)


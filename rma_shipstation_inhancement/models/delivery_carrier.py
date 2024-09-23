# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
import logging
from dateutil import tz
from odoo import api, fields, models, _
from odoo.addons.bista_shipstation.models.shipstation_request import ShipStationRequest
from odoo.exceptions import UserError, ValidationError
from requests.auth import HTTPBasicAuth
from odoo.http import request
import base64
import requests
import json

UTC = tz.gettz('UTC')
PST = tz.gettz('PST')
_logger = logging.getLogger("Shipstation")


class DeliverCarrier(models.Model):
    _inherit = 'delivery.carrier'

    def process_order(self, resource_url):
        ship_station = self
        order_response = ship_station._get_shipstation_data(resource_url)

        if not order_response:
            return

        shipments = order_response.get("shipments") or order_response.get("orders") or []

        if not shipments and len(shipments) == 0:
            return

        shipment = shipments[0]
        order_number = shipment.get('orderId')
        rma_order_obj = request.env['crm.claim.ept']
        orderIds = [order_number]

        carrier_name = self._get_carrier_name(shipment['carrierCode'],
                                              shipment['serviceCode'])

        shipment_cost = float(shipment.get('shipmentCost', 0))
        tracking_number = shipment.get('trackingNumber', ' No Tracking Number')
        order_url = f"/orders/{shipment.get('orderId')}"
        order_details = ship_station._get_shipstation_data(order_url)
        if order_details:
            advance_options = order_details.get("advancedOptions", {})
            merge_ids = advance_options.get("mergedIds", [])
            orderIds += merge_ids

        picking_ids = request.env['stock.picking'].sudo().search(
            [('shipstation_order_id', 'in', orderIds)])

        for picking_id in picking_ids:
            picking_id.sudo().write({
                'carrier_price': shipment_cost,
                'carrier_tracking_ref': tracking_number,
                'carrier_id': ship_station.id,
                'shipstation_service': carrier_name,
                'shipstation_service_code': shipment.get('serviceCode', 'No Service Code')
            })
            rma_order = self.env['repair.order'].search([('name','=',picking_id.group_id.name)])
            print("rrrrr",rma_order)
            rma_id = rma_order.claim_id
            # or rma_order_obj.sudo().search(
            #     [('name', '=', rma_order.group_id.name)], limit=1)

            # if rma_id:
            #     rma_id.get_tracking_ref()
                # if ship_station.add_ship_cost:
                #     sale_id.sudo().update({
                #         'ss_quotation_carrier': shipment.get("carrierCode", "NO carrier Code"),
                #         'ss_quotation_service': carrier_name,
                #     })
            free_over_amount = False
            if ship_station.free_over:
                free_over_amount = rma_id.amount_total >= ship_station.amount
            if (not free_over_amount and picking_id.add_service_line and ship_station.add_ship_cost
                    and not rma_id.no_ship_cost_synced):
                shipment_cost = shipment['shipmentCost']

                new_rate = shipment_cost + ship_station.fixed_margin
                amount = float(new_rate * (1.0 + (ship_station.margin)))
                # sale_id.sudo().update({
                #     'ss_quotation_carrier': shipment.get('carrierCode', 'NO Service Code'),
                #     'ss_quotation_service': carrier_name,
                #     'is_synced': True
                # })

                if not (ship_station.remove_backorder_ship_line and picking_id.backorder_id):
                    rma_id._create_delivery_line(ship_station, amount)
                delivery_lines = request.env['claim.line.ept'].sudo().search(
                    [('claim_id', 'in', rma_id.ids)], order="create_date desc", limit=1)
                # if delivery_lines:
                #     delivery_lines.update({
                #         'name': rma_id.ss_quotation_service,
                #         'price_unit': amount
                #     })
            msg = _("Shipstation tracking number %s<br/>Cost: %.2f") % (tracking_number, shipment_cost)
            picking_id.sudo().message_post(body=msg)
            _logger.info(f"Shipstation Tracking code added to {picking_id.name}")

        return picking_ids

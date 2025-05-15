# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import requests
import json
from requests.exceptions import ReadTimeout
from werkzeug.urls import url_join
from odoo import _
from odoo.exceptions import UserError
import logging
import base64

API_BASE_URL = 'https://ssapi.shipstation.com/'
uom_dict = {
    'g': 'grams',
    'ozs': 'ounces',
    'lb': 'pounds'
}
TIMEOUT = 5
_logger = logging.getLogger(__name__)


class ShipStationRequest():

    def __init__(self, api_key, api_secret, debug_logger):
        self.api_key = api_key
        self.api_secret = api_secret
        self.debug_logger = debug_logger

    def _make_api_request(self, endpoint, request_type='get', data=None, timeout=False):

        json_data = json.dumps(data)
        access_url = url_join(API_BASE_URL, endpoint)
        if access_url == 'https://ssapi.shipstation.com/shipments' and data.get('pages'):
            access_url = access_url + '?page=' + str(data.get('pages'))

        try:
            self.debug_logger("%s\n%s\n%s" % (access_url, request_type, data if data else None),
                              'shipstation_request_%s' % endpoint)
            data = "%s:%s" % (self.api_key, self.api_secret)
            encode_data = base64.b64encode(data.encode("utf-8"))
            authrization_data = "Basic %s" % (encode_data.decode("utf-8"))
            headers = {"Authorization": authrization_data, "Content-Type": "application/json", }

            if request_type == 'get':
                response = requests.get(access_url, params=data, headers=headers)
            elif request_type == 'post':
                response = requests.post(access_url, data=json_data, headers=headers)
            else:
                response = requests.put(access_url, auth=(self.api_key, self.api_secret), data=json_data,
                                        headers=headers)
            self.debug_logger("%s\n%s" % (response.status_code, response.text), 'shipstation_response_%s' % endpoint)
            if response.status_code == 400:
                error_message = response.text
                raise UserError(_('ShipStation returned an error: ') + '%d\nError message: %s' % (
                response.status_code, error_message))
            result = response.json()
            if response.status_code != 200:
                error_message = result.get('ExceptionMessage', "Empty.")
                raise UserError(_('ShipStation returned an error: ') + '%d\nError message: %s' % (
                response.status_code, error_message))
            return result
        except ReadTimeout as e:
            raise UserError("It's taking too long to connect to the carrier. Please retry.")
        except UserError as e:
            raise e
        except Exception as e:
            raise UserError("Error: %s" % str(e))

    def fetch_shipstation_carrier(self):
        ##https://www.shipstation.com/docs/api/carriers/list/
        carriers = self._make_api_request('carriers')
        carriers = {c['name']: c['code'] for c in carriers}
        if carriers:
            return carriers
        else:
            raise UserError(_("You have no carrier linked to your ShipStation Account.\
                Please connect to ShipStation, link your account to carriers and then retry."))

    def _prepare_address(self, contact):
        if not contact.street:
            raise UserError(_("Can not get address for {}. Street is missing".format(contact.name)))
        return {
            "name": contact.name,
            "company": contact.parent_id.name or '',
            "street1": contact.street or '',
            "street2": contact.street2 or '',
            "city": contact.city or '',
            "state": contact.state_id.code or '',
            "postalCode": contact.zip or '',
            "country": contact.country_id.code or '',
            "phone": contact.phone or '',
            "residential": True
        }

    def rate_request(self, carrier, recipient, shipper, order=False):
        """https://www.shipstation.com/docs/api/shipments/get-rates/"""
        weight = 0
        if order:
            for line in order.order_line:
                if not line.display_type and not line.is_delivery and line.product_id.type != 'service':
                    if line.product_id.weight:
                        weight += line.product_id.weight * line.product_uom_qty
                    else:
                        raise UserError("%s items weight not set." % line.product_id.name)
        weight = carrier._shipstation_convert_weight(weight)
        if carrier.package_type_ship.base_weight:
            weight = weight + carrier.package_type_ship.base_weight
        else:
            weight = weight
        data_dict = {
            "fromPostalCode": shipper.zip,
            "serviceCode": carrier.shipstation_default_service_id.name if carrier.shipstation_default_service_id else None,
            "packageCode": None,
            "toState": recipient.state_id.code,
            "toCountry": recipient.country_id.code,
            "toPostalCode": recipient.zip,
            "toCity": recipient.city,
            "weight": {
                "value": "%f" % weight,
                "units": uom_dict[carrier.shipstation_weight_uom_id.name]

            },

            "confirmation": "delivery",
            "residential": False
        }

        if carrier.package_type_ship:
            data_dict.update({
                "dimensions": {
                    "units": uom_dict[carrier.shipstation_weight_uom_id.name],
                    "length": carrier.package_type_ship.packaging_length if carrier.package_type_ship else False,
                    "width": carrier.package_type_ship.width if carrier.package_type_ship else False,
                    "height": carrier.package_type_ship.height if carrier.package_type_ship else False
                }
            })
        rates = []
        for code in carrier.shipstation_delivery_type.split(','):
            try:
                res = self._make_api_request('/shipments/getrates', 'post', dict(data_dict, carrierCode=code))
                carrier._generate_services(code, res)
                rates.append({'code': code, 'rates': res})
            except Exception as e:
                _logger.warn('Unable to get rate from ShipStation. Carrier Code: %s' % code)
                _logger.warn(e)
                rates.append({'code': code, 'rates': []})
        return {
            'rate': {
                'rate': 0,
                'currency': 'USD',
                'rates': rates
            }
        }

    def send_shipping(self, carrier, recipient, shipper, picking, is_return=False):
        '''https://www.shipstation.com/docs/api/orders/create-update-order/'''
        if picking.move_ids_without_package:
            weight = sum([ml.product_id.weight * ml.product_uom_qty for ml in picking.move_ids_without_package])
            weight = carrier._shipstation_convert_weight(weight)
        else:
            raise UserError("Nothing to ship.")

        customer_note = picking.sale_id.name if picking.sale_id else '' + (
            ' - %s' % picking.sale_id.client_order_ref if picking.sale_id.client_order_ref else '')

        if picking.sale_id:
            third_part_account = picking.sale_id.partner_id.third_part_account
            if third_part_account:
                bill_party = 'third_party'
                bill_account = picking.sale_id.partner_id.bill_account
                bill_postal_code = picking.sale_id.partner_id.bill_postal_code
                bill_country_code = picking.sale_id.partner_id.bill_country_code.code
            else:
                bill_party = ''
                bill_account = ''
                bill_postal_code = ''
                bill_country_code = ''
        else:
            bill_party = ''
            bill_account = ''
            bill_postal_code = ''
            bill_country_code = ''

        def get_shipping_lines():
            shipping_lines = []
            processed_sale_lines = set()

            for move in picking.move_ids_without_package:
                sale_line = move.sale_line_id

                if not sale_line or not move.product_uom_qty or sale_line.id in processed_sale_lines:
                    continue

                processed_sale_lines.add(sale_line.id)
                if sale_line.product_id.bom_ids and sale_line.product_id.bom_ids[0].type == 'phantom':
                    bom = sale_line.product_id.bom_ids[0]
                    total_delivered_qty = float('inf')
                    for bom_line in bom.bom_line_ids:
                        component_moves = picking.move_ids_without_package.filtered(
                            lambda m: m.product_id == bom_line.product_id
                        )
                        component_qty_done = sum(component_moves.mapped('quantity'))
                        kit_qty_done = component_qty_done / bom_line.product_qty if bom_line.product_qty > 0 else 0
                        total_delivered_qty = min(total_delivered_qty, kit_qty_done)

                    shipping_lines.append({
                        "lineItemKey": sale_line.product_id.id,
                        "sku": sale_line.product_id.default_code,
                        "name": sale_line.product_id.name,
                        "imageUrl": None,
                        "weight": {
                            "value": carrier._shipstation_convert_weight(sale_line.product_id.weight),
                            "units": carrier.shipstation_weight_uom_id.id
                        },
                        "quantity": int(total_delivered_qty),
                        "unitPrice": sale_line.price_unit,
                        "taxAmount": sale_line.price_tax,
                        "shippingAmount": sale_line.product_id.standard_price,
                        "productId": sale_line.product_id.id,
                    })
                else:
                    shipping_lines.append({
                        "lineItemKey": move.product_id.id,
                        "sku": move.product_id.default_code,
                        "name": move.product_id.name,
                        "imageUrl": None,
                        "weight": {
                            "value": carrier._shipstation_convert_weight(move.product_id.weight),
                            "units": carrier.shipstation_weight_uom_id.id
                        },
                        "quantity": int(move.product_uom_qty),
                        "unitPrice": sale_line.price_unit,
                        "taxAmount": sale_line.price_tax,
                        "shippingAmount": move.product_id.standard_price,
                        "productId": move.product_id.id,
                    })

            return shipping_lines

        data = {
            "orderNumber": picking.name,
            "orderStatus": "awaiting_shipment",
            "orderDate": picking.scheduled_date.isoformat(sep='T', timespec='microseconds'),
            "customerUsername": recipient.email or " ",
            "customerEmail": recipient.email or " ",
            "billTo": self._prepare_address(recipient),
            "shipTo": self._prepare_address(recipient),
            "items": get_shipping_lines(),
            "internalNotes": None,
            "carrierCode": picking.partner_id.carrier_id.code if picking.partner_id.carrier_id else None,
            "serviceCode": picking.partner_id.service_id.code if picking.partner_id.service_id else None,
            "packageCode": "package",
            "confirmation": "delivery",
            "customerNotes": customer_note,
            "requestedShippingService": picking.sale_id.ship_via if picking.sale_id.ship_via else picking.partner_id.ship_via,
            "weight": {
                "value": weight,
                "units": carrier.shipstation_weight_uom_id.name
            },
            "advancedOptions": {
                'storeId': "%s" % (carrier.store_id.store_id),
                "billToParty": bill_party,
                "billToAccount": bill_account,
                "billToCountryCode": bill_country_code,
                "billToPostalCode": bill_postal_code,
                "customField1": picking.user_id.name

            },
        }

        if picking.carrier_id or picking.carrier_tracking_ref:
            result = {}
            pass
        else:
            result = self._make_api_request('/orders/createorder', 'post', data=data)
        return result
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

    delivery_type = fields.Selection(selection_add=[('shipstation', 'ShipStation')], ondelete={
        'shipstation': lambda recs: recs.write({'delivery_type': 'fixed', 'fixed_price': 0})})
    shipstation_production_api_key = fields.Char("Production API Key", groups="base.group_system",
                                                 help="Enter your API production key from ShipStation account")
    shipstation_production_api_secret = fields.Char("Production API Secret", groups="base.group_system",
                                                    help="Enter your API production secret from ShipStation account")
    shipstation_delivery_type = fields.Char('ShipStation Carrier Type')
    shipstation_default_service_id = fields.Many2one("shipstation.service", string="Default Service Level",
                                                     help="If not set, the less expensive available service level will be chosen.",
                                                     domain="[('shipstation_carrier', '=', shipstation_delivery_type)]")
    shipstation_weight_uom_id = fields.Many2one("uom.uom", domain=lambda self: [('id', 'in', [
        self.env.ref('uom.product_uom_gram').id, self.env.ref('uom.product_uom_oz').id,
        self.env.ref('uom.product_uom_lb').id])])
    add_ship_cost = fields.Boolean(string="Add Shipping Cost")
    remove_backorder_ship_line = fields.Boolean(string="Remove Backorder Shipping Cost")
    store_id = fields.Many2one('shipstation.store')
    api_url = fields.Char(string='URL', tracking=True,
                          default="https://ssapi.shipstation.com")

    package_type_ship = fields.Many2one('stock.package.type', string="Package")
    carrier_code = fields.Selection([
        ('fedex', 'Fedex'),
        ('ups', 'UPS')], 'Package Type')
    free_delivery_limit = fields.Float(
        string="Free Delivery Limit",
        help="If the sales order total reached this limit, "
             "shipstation will not add delivery cost")

    def import_package_from_ship_station(self):
        for line in self:
            url='https://ssapi.shipstation.com/carriers/listpackages?carrierCode=' + line.carrier_code
            api_key = self.shipstation_production_api_key
            api_secret = self.shipstation_production_api_secret
            resp = requests.get(url, auth=HTTPBasicAuth(api_key,api_secret))
            if resp.status_code == 200:
                rec = json.loads(resp.text)
                for line in rec:
                    carrier_code=line.get('carrierCode')
                    name=line.get('name')
                    package_type_id=self.env['stock.package.type'].search([('name','=',name)])
                    if not package_type_id:
                        self.env['stock.package.type'].create({
                            'name':name,
                            'shipper_package_code':carrier_code
                        })
        return True

    # def import_package_from_ship_station(self):
    #     for line in self:
    #         url = 'https://ssapi.shipstation.com/carriers/listpackages?carrierCode=' + line.carrier_code
    #         api_key = self.shipstation_production_api_key
    #         api_secret = self.shipstation_production_api_secret
    #         resp = requests.get(url, auth=HTTPBasicAuth(api_key, api_secret))
    #         if resp.status_code == 200:
    #             rec = json.loads(resp.text)
    #             for line in rec:
    #                 carrier_code = line.get('carrierCode')
    #                 name = line.get('name')
    #                 package_type_id = self.env['stock.package.type'].search([('name', '=', name)])
    #                 if not package_type_id:
    #                     self.env['stock.package.type'].create({
    #                         'name': name,
    #                         'shipper_package_code': carrier_code
    #                     })
    #     return True

    def get_api_function(self, url, store=False, payload=False):
        """ Fetch response data based on URL using request GET. """
        if self.delivery_type == 'shipstation':
            api_key = self.shipstation_production_api_key
            api_secret = self.shipstation_production_api_secret
            data = "%s:%s" % (api_key, api_secret)
            encode_data = base64.b64encode(data.encode("utf-8"))
            authorization_data = "Basic %s" % (encode_data.decode("utf-8"))
            headers = {"Authorization": "%s" % authorization_data}
            try:
                response_data = requests.get(url, auth=HTTPBasicAuth(api_key, api_secret))
                # response_data = request(method='GET', url=url, headers=headers, data=payload)
                return response_data
            except Exception as e:
                raise ValidationError(e)

    def import_dc_from_ship_station(self):
        """This function import delivery carriers from ShipStation"""
        url = self.api_url + "/carriers"

        response = self.get_api_function(url)
        if response.status_code != 200:
            desc = ("Error while fetching carriers - %s : %s" % (
                response.status_code, response.reason))
        shipstation_delivery_carrier = self.env['shipstation.delivery.carrier']
        try:
            responses = response.json()
            for response in responses:
                delivery_carrier = shipstation_delivery_carrier.search([('code', '=', response.get('code', False))])
                if not delivery_carrier:
                    shipstation_delivery_carrier.create(
                        {'name': response.get('name', False),
                         'code': response.get('code', False),
                         'account_number': response.get('accountNumber', False),
                         'shipping_provider_id': response.get('shippingProviderId',
                                                              False),
                         'balance': response.get('balance', False)
                         })

                else:
                    delivery_carrier.write({'balance': response.get('balance', False)})
        except Exception as e:
            desc = ("Error while fetching store - %s" % e)
        return {
            'effect': {
                'fadeout': 'slow',
                'message': 'Import Carriers Successfully!',
                'type': 'rainbow_man',
            }
        }

    # def import_dc_from_ship_station(self):
    #     """This function import delivery carriers from ShipStation"""
    #     url = "/carriers"
    #     response = self._get_shipstation_data(url)
    #     print("responseresponseresponseresponseresponse",response)
    #     if response.status_code != 200:
    #         desc = ("Error while fetching carriers - %s : %s" % (
    #             response.status_code, response.reason))
    #     shipstation_delivery_carrier = self.env['shipstation.delivery.carrier']
    #     try:
    #         responses = response.json()
    #         for response in responses:
    #             delivery_carrier = shipstation_delivery_carrier.search([('code', '=', response.get('code', False))])
    #             if not delivery_carrier:
    #                 shipstation_delivery_carrier.create(
    #                     {'name': response.get('name', False),
    #                      'code': response.get('code', False),
    #                      'account_number': response.get('accountNumber', False),
    #                      'shipping_provider_id': response.get('shippingProviderId',
    #                                                           False),
    #                      'balance': response.get('balance', False),
    #                      'company_id': self.env.company.id
    #                      })
    #
    #             else:
    #                 delivery_carrier.write({'balance': response.get('balance', False)})
    #     except Exception as e:
    #         desc = ("Error while fetching store - %s" % e)
    #     return {
    #         'effect': {
    #             'fadeout': 'slow',
    #             'message': 'Import Carriers Successfully!',
    #             'type': 'rainbow_man',
    #         }
    #     }

    def import_cs_from_ship_station(self):
        shipstation_carrier_service = self.env['shipstation.carrier.service']
        ship_log_line_dict = {'error': [], 'success': []}
        self.env.cr.execute("""SELECT distinct(code) FROM shipstation_delivery_carrier""")
        for codes in self.env.cr.fetchall():
            url = self.api_url + "/carriers/listservices?carrierCode=" + str(codes[0])
            response_data = self.get_api_function(url)
            if response_data.status_code != 200:
                desc = ("Error while fetching carriers - %s : %s" % (
                    response_data.status_code, response_data.reason))
                ship_log_line_dict['error'].append({'error_message': 'Carrier Service Import: %s' % desc})
            if response_data.status_code == 401:
                error_msg = 'Error response - ' + response_data.text + '. Please check the credentials'
                raise ValidationError(error_msg)
            try:
                for response in response_data.json():
                    carrier_id = self.env['shipstation.delivery.carrier'].search(
                        [('code', '=', response.get('carrierCode', False))]).id
                    delivery_carrier = shipstation_carrier_service.search(
                        [('code', '=', response.get('code', False)), ('delivery_carrier_id', '=', carrier_id)])
                    if not delivery_carrier:
                        shipstation_carrier_service.create(
                            {'name': response.get('name', False),
                             'code': response.get('code', False),
                             'carrier_code': response.get('carrierCode', False),
                             'domestic': response.get('domestic', False),
                             'international': response.get('international',
                                                           False),
                             'delivery_carrier_id': carrier_id
                             })

                        ship_log_line_dict['success'].append(
                            {'error_message': 'Carrier Service created: %s' % response.get('name', False)})

            except Exception as e:
                desc = ("Error while fetching Carrier Service - %s" % e)
                ship_log_line_dict['error'].append(
                    {'error_message': 'Exception error: %s' % desc})
        return {
            'effect': {
                'fadeout': 'slow',
                'message': 'Import Services Successfully!',
                'img_url': 'bista_shipstation_connector/static/description/smile.svg',
                'type': 'rainbow_man',
            }
        }

    # def import_cs_from_ship_station(self):
    #     shipstation_carrier_service = self.env['shipstation.carrier.service']
    #     ship_log_line_dict = {'error': [], 'success': []}
    #     self.env.cr.execute("""SELECT distinct(code) FROM shipstation_delivery_carrier""")
    #     for codes in self.env.cr.fetchall():
    #         url = "/carriers/listservices?carrierCode=" + str(codes[0])
    #         response_data = self._get_shipstation_data(url)
    #         print("response_dataresponse_dataresponse_dataresponse_data",response_data)
    #         if response_data.status_code != 200:
    #             desc = ("Error while fetching carriers - %s : %s" % (
    #                 response_data.status_code, response_data.reason))
    #             ship_log_line_dict['error'].append({'error_message': 'Carrier Service Import: %s' % desc})
    #         if response_data.status_code == 401:
    #             error_msg = 'Error response - ' + response_data.text + '. Please check the credentials'
    #             raise ValidationError(error_msg)
    #         try:
    #             for response in response_data.json():
    #                 carrier_id = self.env['shipstation.delivery.carrier'].search(
    #                     [('code', '=', response.get('carrierCode', False))]).id
    #                 delivery_carrier = shipstation_carrier_service.search(
    #                     [('code', '=', response.get('code', False)), ('delivery_carrier_id', '=', carrier_id)])
    #                 if not delivery_carrier:
    #                     shipstation_carrier_service.create(
    #                         {'name': response.get('name', False),
    #                          'code': response.get('code', False),
    #                          'carrier_code': response.get('carrierCode', False),
    #                          'domestic': response.get('domestic', False),
    #                          'international': response.get('international',
    #                                                        False),
    #                          'delivery_carrier_id': carrier_id,
    #                          'company_id': self.env.company.id
    #                          })
    #
    #                     ship_log_line_dict['success'].append(
    #                         {'error_message': 'Carrier Service created: %s' % response.get('name', False)})
    #
    #         except Exception as e:
    #             desc = ("Error while fetching Carrier Service - %s" % e)
    #             ship_log_line_dict['error'].append(
    #                 {'error_message': 'Exception error: %s' % desc})
    #     return {
    #         'effect': {
    #             'fadeout': 'slow',
    #             'message': 'Import Services Successfully!',
    #             'img_url': 'bista_shipstation_connector/static/description/smile.svg',
    #             'type': 'rainbow_man',
    #         }
    #     }

    def import_store_from_ship_station(self):
        """This function import store from ShipStation"""
        ir_model_obj = self.env['ir.model']
        model = ir_model_obj.search([('model', '=', 'shipstation.store')], limit=1)
        url = self.api_url + "/stores?stores?showInactive=false"
        # error_log_env = self.env['shipstation.error.log']
        shipstation_store = self.env['shipstation.store']
        state = 'error'
        # shipstation_log_id = error_log_env.create_update_log(
        #     shipstation_config_id=self,
        #     operation_type='import_store')
        ship_log_line_dict = {'error': [], 'success': []}
        response = self.get_api_function(url)
        if response.status_code != 200:
            desc = ("Error while fetching store - %s : %s" % (
                response.status_code, response.reason))
            ship_log_line_dict['error'].append(
                {'error_message': 'Import Store: %s' % desc})
        if response.status_code == 401:
            error_msg = 'Error response - ' + response.text + '. Please check the credentials'
            raise ValidationError(error_msg)
        try:
            responses = response.json()
            for res in responses:
                store = shipstation_store.search([('store_id', '=', res.get('storeId', False))])
                if not store:
                    shipstation_store.create({
                        'store_id': res.get('storeId', False),
                        'store_name': res.get('storeName', False),
                        'marketplace_id': res.get('marketplaceId', False),
                        'company_id': self.env.company.id
                    })
                    ship_log_line_dict['success'].append(
                        {'error_message': 'Store Created: %s' % res.get('storeName', False)})
                else:
                    ship_log_line_dict['success'].append(
                        {'error_message': 'Store Already crated: %s' % res.get('storeName', False)})
                state = 'success'
        except Exception as e:
            desc = ("Error while fetching store - %s" % e)
            ship_log_line_dict['error'].append(
                {'error_message': 'Import Store: %s' % desc})
        return {
            'effect': {
                'fadeout': 'slow',
                'message': 'Action Completed Successfully!',
                'img_url': 'bista_shipstation_connector/static/description/smile.svg',
                'type': 'rainbow_man',
            }
        }

    def shipstation_rate_shipment(self, order):
        sp = ShipStationRequest(self.sudo().shipstation_production_api_key,
                                self.sudo().shipstation_production_api_secret, self.log_xml)
        response = sp.rate_request(self, order.partner_shipping_id, order.warehouse_id.partner_id, order)
        # Return error message
        if response.get('error_message'):
            return {
                'success': False,
                'price': 0.0,
                'error_message': response.get('error_message'),
                'warning_message': False
            }

        # Update price with the order currency
        rate = response.get('rate')
        if order.currency_id.name == rate['currency']:
            price = float(rate['rate'])
        else:
            quote_currency = self.env['res.currency'].search([('name', '=', rate['currency'])], limit=1)
            price = quote_currency._convert(float(rate['rate']), order.currency_id, self.env.company,
                                            fields.Date.today())

        return {
            'success': True,
            'price': price,
            'error_message': False,
            'warning_message': response.get('warning_message', False),
            'rates': rate.get('rates', [])
        }

    def _shipstation_convert_weight(self, weight):
        weight_uom_id = self.env['product.template']._get_weight_uom_id_from_ir_config_parameter()
        weight = weight_uom_id._compute_quantity(weight, self.shipstation_weight_uom_id)
        if len(str(weight).split(".")[-1]) > 10:
            weight = round(weight, 2)
        return weight

    def shipstation_send_shipping(self, pickings):
        res = []
        sp = ShipStationRequest(self.sudo().shipstation_production_api_key,
                                self.sudo().shipstation_production_api_secret, self.log_xml)

        for picking in pickings.filtered(lambda l: l.state == "done" and not l.shipstation_order_id):
            result = sp.send_shipping(self, picking.partner_id, picking.picking_type_id.warehouse_id.partner_id,
                                      picking=picking)
            _logger.info("Export Result ", result)
            ss_order_id = result.get('orderId', False)
            ss_order_key = result.get('orderKey', False)
            if ss_order_id:
                picking.update({
                    'shipstation_order_id': ss_order_id,
                    'shipstation_order_key': ss_order_key
                })
            else:
                # raise UserError("Couldn't place ShipStation Order.")
                pass
            res += [{'exact_price': 0.0, 'tracking_number': False}]
        return res

    def _generate_services(self, carrier, rates):
        services_name = {rate.get('serviceCode'): rate.get('serviceCode') for rate in rates}
        existing_services = self.env['shipstation.service'].search_read(
            [('name', 'in', list(services_name.keys())), ('shipstation_carrier', '=', carrier)], ['name'])
        for service_name in set([service['name'] for service in existing_services]) ^ set(services_name.keys()):
            self.env['shipstation.service'].create({
                'name': service_name,
                'service': services_name[service_name],
                'shipstation_carrier': carrier
            })

    @api.model
    def run_product_sync(self):
        # list all product in odoo, with to_sync = True
        # - to_sync = True when customer change product info
        # for each odoo product, find product in shipstation
        # update product in shipstation
        # update odoo product to_sync = False
        sr = False
        delivery_ids = self.search([('delivery_type', '=', 'shipstation')])
        for delivery in delivery_ids:
            if delivery.shipstation_production_api_key and delivery.shipstation_production_api_secret:
                sr = ShipStationRequest(delivery.sudo().shipstation_production_api_key,
                                        delivery.sudo().shipstation_production_api_secret, delivery.log_xml)
                if not sr:
                    _logger.warning("Skip run_product_sync due to no Shipping Methods for ShipStation found")
                    return
                products = self.env["product.product"].search(
                    [
                        ("to_sync", "=", True),
                        ("barcode", "!=", False),
                        ("hs_code", "!=", False),
                    ]
                )
                for product in products:
                    product_params = {"sku": product.barcode}
                    product_response = sr._make_api_request('/products', 'get', data=product_params, timeout=-1)
                    if not product_response or product_response.get("total") != 1:
                        _logger.warning("Unable to update product {}: No matching product found on ShipStation".format(
                            product.name))
                        product.to_sync = False
                        continue
                    update_params = product_response["products"][0]
                    update_params.update({"customsTariffNo": product.hs_code})
                    _logger.info("Syncing product with name: {}".format(product.name))
                    try:
                        product.to_sync = False
                        sr._make_api_request(
                            '/products/{}'.format(update_params.get("productId")),
                            'put',
                            data=update_params,
                            timeout=-1
                        )
                        product.message_post(body="HS Code has been synced to ShipStation.")
                    except UserError as e:
                        _logger.warning("Unable to sync product {}. Error found!".format(product.name))
                        continue

        return

    def _prepare_invoice_values(self, order):
        invoice_vals = {
            'ref': order.client_order_ref,
            'move_type': 'out_invoice',
            'invoice_origin': order.name,
            'invoice_user_id': order.user_id.id,
            'narration': order.note,
            'partner_id': order.partner_invoice_id.id,
            'fiscal_position_id': order.fiscal_position_id and order.fiscal_position_id._get_fiscal_position(
                order.partner_id).id or False,
            'partner_shipping_id': order.partner_shipping_id.id,
            'currency_id': order.pricelist_id.currency_id.id,
            'payment_reference': order.reference,
            'invoice_payment_term_id': order.payment_term_id.id,
            'partner_bank_id': order.company_id.partner_id.bank_ids[:1].id,
            'team_id': order.team_id.id,
            'campaign_id': order.campaign_id.id,
            'medium_id': order.medium_id.id,
            'source_id': order.source_id.id,
            'want_to_send_email': True,
        }
        return invoice_vals

    def _get_shipstation_requester(self):
        api_key = self.sudo().shipstation_production_api_key
        api_secret = self.sudo().shipstation_production_api_secret

        if not api_key or not api_secret:
            _logger.warning("Shipstation api is not configured")
            return
        return ShipStationRequest(api_key, api_secret, self.log_xml)

    def _get_shipstation_data(self, resource_url):
        shipstation_request = self._get_shipstation_requester()

        if not shipstation_request:
            return

        if not resource_url:
            _logger.warning("Shipstation did not returned a valid resources url")
            return
        try:
            response = shipstation_request._make_api_request(resource_url, 'get')
            return response
        except Exception as e:
            _logger.warning("Shipstation request error ", e)
            return

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
        sale_order_obj = request.env['sale.order']
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
            sale_id = picking_id.sale_id or sale_order_obj.sudo().search(
                [('name', '=', picking_id.group_id.name)], limit=1)

            if sale_id:
                sale_id.get_tracking_ref()
                if ship_station.add_ship_cost:
                    sale_id.sudo().update({
                        'ss_quotation_carrier': shipment.get("carrierCode", "NO carrier Code"),
                        'ss_quotation_service': carrier_name,
                    })
            free_over_amount = False
            if ship_station.free_over:
                free_over_amount = sale_id.amount_total >= ship_station.amount
            if (not free_over_amount and picking_id.add_service_line and ship_station.add_ship_cost
                    and not sale_id.no_ship_cost_synced and not sale_id.add_ship_no_delivery_line):
                shipment_cost = shipment['shipmentCost']
                # if fixed_margin and margin_percentage:
                #     margin_amount = shipment_cost * margin_percentage
                #     amount = shipment_cost + fixed_margin + margin_amount
                # elif fixed_margin:
                #     amount = shipment_cost + fixed_margin
                # elif margin_percentage:
                #     margin_amount = shipment_cost * margin_percentage
                #     amount = shipment_cost + margin_amount
                # else:
                #     amount = shipment_cost
                new_rate = shipment_cost + ship_station.fixed_margin
                amount = float(new_rate * (1.0 + (ship_station.margin)))
                sale_id.sudo().update({
                    'ss_quotation_carrier': shipment.get('carrierCode', 'NO Service Code'),
                    'ss_quotation_service': carrier_name,
                    'is_synced': True
                })

                if not (ship_station.remove_backorder_ship_line and picking_id.backorder_id):
                    sale_id._create_delivery_line(ship_station, amount)
                delivery_lines = request.env['sale.order.line'].sudo().search(
                    [('order_id', 'in', sale_id.ids), ('is_delivery', '=', True)],order="create_date desc", limit=1)
                if delivery_lines:
                    delivery_lines.update({
                        'name': sale_id.ss_quotation_service,
                        'price_unit':amount
                    })
            msg = _("Shipstation tracking number %s<br/>Cost: %.2f") % (tracking_number, shipment_cost)
            picking_id.sudo().message_post(body=msg)
            _logger.info(f"Shipstation Tracking code added to {picking_id.name}")
        return picking_ids

    def _get_carrier_name(self, carrier_code, service_code):
        try:
            list_service_url = '/carriers/listservices?carrierCode=' + carrier_code
            carrier_name = self._get_shipstation_data(list_service_url) or {}
            carrier_name = [x.get('name') for x in carrier_name if x.get('code') == service_code]
            return carrier_name[0] if carrier_name else 'NO Carrier Name'
        except Exception as e:
            _logger.warning("Error getting carrier name ", e)

    def _webhook_trigger(self, order_id):
        url = f"/shipments?orderId={order_id}"
        self.process_order(url)


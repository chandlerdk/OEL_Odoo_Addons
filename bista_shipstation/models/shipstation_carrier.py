##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2020 (http://www.bistasolutions.com)
#
##############################################################################
from odoo import models, fields, api
import logging

_logger = logging.getLogger("Shipstation Log")


class ShipstationDeliveryCarrier(models.Model):
    _name = "shipstation.delivery.carrier"
    _description = "ShipStation Delivery Carrier"

    name = fields.Char(string='Carrier Name')
    code = fields.Char(string='Carrier Code')
    account_number = fields.Char(string='Account Number')
    shipping_provider_id = fields.Char(string='Shipping Provide Id')
    store_id = fields.Many2one('shipstation.store',
                               string='Store')
    account_id = fields.Many2one('shipstation.config', "Account")
    provider_tracking_link = fields.Char(string="Provider Tracking Link",
                                         help="Tracking link(URL) useful to track the shipment or package from this URL.",
                                         size=256)
    balance = fields.Float(string="Balance")
    company_id = fields.Many2one('res.company')


class ShipstationCarrierService(models.Model):
    _name = "shipstation.carrier.service"
    _description = "ShipStation Carrier Service"

    carrier_code = fields.Char(string='Carrier Code')
    code = fields.Char(string='Code')
    name = fields.Char(string='Name')
    delivery_carrier_id = fields.Many2one('shipstation.delivery.carrier',
                                          string='Delivery Carrier')
    domestic = fields.Boolean(string='Domestic', default="False")
    international = fields.Boolean(string='International', default="False")
    company_id = fields.Many2one('res.company')

    @api.model
    def name_search(self, name, args=None, operator='ilike', limit=100):
        if self._context.get('carrier_service_id'):
            args = [('delivery_carrier_id', '=', self._context.get('carrier_service_id'))]
        return super(ShipstationCarrierService, self).name_search(name, args=args, operator=operator, limit=limit)

    @api.model
    def search_read(self, domain=None, fields=None, offset=0, limit=None, order=None):
        if self._context.get('carrier_service_id'):
            domain = [('delivery_carrier_id', '=', self._context.get('carrier_service_id'))]
        return super(ShipstationCarrierService, self).search_read(domain, fields, offset, limit, order)

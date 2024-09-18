# -*- coding: utf-8 -*-
###############################################################################
#    License, author and contributors information in:                         #
#    __manifest__.py file at the root folder of this module.                  #
###############################################################################

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class ChooseDeliveryCarrier(models.TransientModel):
    _inherit = 'choose.delivery.carrier'

    rate_ids = fields.One2many(string='Rates', comodel_name='ship.station.rate', inverse_name='wizard_id', )
    package_type_ship = fields.Many2one('stock.package.type',related='carrier_id.package_type_ship',readonly=False)

    @api.onchange('carrier_id')
    def remove_rates(self):
        self.rate_ids = [(6, 0, [])]

    def _get_shipment_rate(self):
        vals = self.carrier_id.rate_shipment(self.order_id)
        if vals.get('success'):
            self.delivery_message = vals.get('warning_message', False)
            self.delivery_price = vals['price']
            self.display_price = vals['carrier_price']
            if self.carrier_id.delivery_type == 'shipstation':
                rate_vals = [(6, 0, [])]
                for val in vals.get('rates', []):
                    for f_rate in val.get('rates'):
                        new_rate = f_rate.get('shipmentCost') + self.carrier_id.fixed_margin
                        new_price = float(new_rate * (1.0 + (self.carrier_id.margin)))
                        f_rate.update({'shipmentCost':new_price})
                    rate_vals += [(0, 0, dict(rate, carrierCode=val['code'])) for rate in val['rates']]
                self.write({
                    'rate_ids': rate_vals
                })
            return {}
        return {'error_message': vals['error_message']}


class ShipStationRate(models.TransientModel):
    _name = 'ship.station.rate'
    _description = 'ShipStation Rate'

    wizard_id = fields.Many2one(comodel_name='choose.delivery.carrier', ondelete='cascade', )

    carrierCode = fields.Char(string="Carrier", )
    serviceName = fields.Char(string="Service", )
    serviceCode = fields.Char(string="Code", )
    shipmentCost = fields.Float(string='Shipment Cost', )
    otherCost = fields.Float(string='Other Cost', )
    totalCost = fields.Float(string='Cost', compute='get_total')

    @api.depends('shipmentCost', 'otherCost')
    def get_total(self):
        for record in self:
            record.totalCost = (record.shipmentCost + record.otherCost) * (
                        1.0 + (self.wizard_id.carrier_id.margin / 100.0))

    def set_rate(self):
        price = self.shipmentCost + self.otherCost
        carrier_price = price
        # price = float(price * (1.0 + (self.wizard_id.carrier_id.margin / 100.0)))
        self.wizard_id.delivery_price = price
        self.wizard_id.display_price = carrier_price
        self.wizard_id.order_id.ss_quotation_service = self.serviceCode
        self.wizard_id.order_id.ss_quotation_carrier = self.carrierCode
        self.wizard_id.button_confirm()
        self.wizard_id.order_id.add_ship_no_delivery_line = True
        line = self.wizard_id.order_id.order_line.filtered(lambda line: line.is_delivery)
        line.name = self.serviceName
        amount = carrier_price
        line.price_unit = amount
        line.order_id.ship_via = line.name


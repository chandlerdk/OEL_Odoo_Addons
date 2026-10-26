# -*- coding: utf-8 -*-
###############################################################################
#    License, author and contributors information in:                         #
#    __manifest__.py file at the root folder of this module.                  #
###############################################################################

from odoo import models, fields, api, Command


class ChooseDeliveryCarrier(models.TransientModel):
    _inherit = 'choose.delivery.carrier'

    rate_ids = fields.One2many(string='Rates', comodel_name='ship.station.rate', inverse_name='wizard_id')
    package_type_ship = fields.Many2one(
        'stock.package.type', related='carrier_id.package_type_ship', readonly=False,
    )

    def _get_carrier_delivery_rate(self, carrier):
        """Keep the standard rate, and store every ShipStation service so the user can pick one."""
        if carrier.delivery_type != 'shipstation':
            self.rate_ids = [Command.clear()]
            return super()._get_carrier_delivery_rate(carrier)

        self.ensure_one()
        try:
            vals = carrier.with_context(order_weight=self.total_weight).rate_shipment(self.order_id)
        except Exception as error:  # noqa: BLE001
            self.rate_ids = [Command.clear()]
            return {'error_message': error.args[0] if error.args else str(error)}

        if not vals.get('success'):
            self.rate_ids = [Command.clear()]
            return {'error_message': vals.get('error_message')}

        rate_commands = [Command.clear()]
        for carrier_rates in vals.get('rates') or []:
            for rate in carrier_rates.get('rates') or []:
                shipment_cost = float(rate.get('shipmentCost') or 0.0)
                priced = shipment_cost + carrier.fixed_margin
                priced = float(priced * (1.0 + carrier.margin))
                rate_commands.append(Command.create({
                    'carrierCode': carrier_rates.get('code'),
                    'serviceName': rate.get('serviceName'),
                    'serviceCode': rate.get('serviceCode'),
                    'shipmentCost': priced,
                    'otherCost': float(rate.get('otherCost') or 0.0),
                }))
        self.rate_ids = rate_commands
        return {
            'delivery_message': vals.get('warning_message', False),
            'delivery_price': vals['price'],
            'display_price': vals['carrier_price'],
            'no_rate': vals.get('no_rate', False),
        }


class ShipStationRate(models.TransientModel):
    _name = 'ship.station.rate'
    _description = 'ShipStation Rate'

    wizard_id = fields.Many2one(comodel_name='choose.delivery.carrier', ondelete='cascade')

    carrierCode = fields.Char(string="Carrier")
    serviceName = fields.Char(string="Service")
    serviceCode = fields.Char(string="Code")
    shipmentCost = fields.Float(string='Shipment Cost')
    otherCost = fields.Float(string='Other Cost')
    totalCost = fields.Float(string='Cost', compute='get_total')

    @api.depends('shipmentCost', 'otherCost', 'wizard_id.carrier_id.margin')
    def get_total(self):
        for record in self:
            margin = record.wizard_id.carrier_id.margin or 0.0
            record.totalCost = (record.shipmentCost + record.otherCost) * (1.0 + (margin / 100.0))

    def set_rate(self):
        price = self.shipmentCost + self.otherCost
        self.wizard_id.delivery_price = price
        self.wizard_id.display_price = price
        self.wizard_id.order_id.ss_quotation_service = self.serviceCode
        self.wizard_id.order_id.ss_quotation_carrier = self.carrierCode
        self.wizard_id.button_confirm()
        self.wizard_id.order_id.add_ship_no_delivery_line = True
        line = self.wizard_id.order_id.order_line.filtered('is_delivery')
        if line:
            line.write({'name': self.serviceName, 'price_unit': price})
            line.order_id.ship_via = self.serviceName

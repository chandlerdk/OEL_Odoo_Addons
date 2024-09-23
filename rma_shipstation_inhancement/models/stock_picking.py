# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

from odoo import fields, models
import logging
_logger = logging.getLogger("Shipstation")


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    # def _send_confirmation_email(self):
    #     res = super(StockPicking, self)._send_confirmation_email()
    #     self.user_id = self.env.uid
    #     for picking in self.sudo():
    #         carrier_id = picking.env['delivery.carrier'].search(
    #             [('delivery_type', '=', 'shipstation'),
    #              ('company_id', '=', picking.company_id.id)], limit=1)
    #         if carrier_id and picking.picking_type_id.code == 'outgoing' and not picking.carrier_id:
    #             url = '/orders?' + 'orderNumber=' + picking.name
    #             resp = carrier_id._get_shipstation_data(url)
    #             if resp.get('orders') and resp.get('orders')[0].get('advancedOptions').get('storeId') == int(
    #                     carrier_id.store_id.store_id):
    #                 _logger.info(f"Shipstation: Order already exists in store skipping {picking.name}")
    #                 pass
    #             else:
    #                 _logger.info(f"Shipstation: Creating shipstation order {picking.name}")
    #                 carrier_id.shipstation_send_shipping(picking)
    #         elif picking.carrier_id and picking.picking_type_id.code == 'outgoing':
    #             pass
    #     return res
    #

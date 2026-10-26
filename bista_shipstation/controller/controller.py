# -*- coding: utf-8 -*-

from odoo import http
from odoo.http import request
import logging

logger = logging.getLogger("Shipstation")


class Binary(http.Controller):

    def _get_carrier(self, company_id):
        try:
            company_id = int(company_id) or request.env.company.id
        except ValueError:
            company_id = request.env.company.id

        return request.env['delivery.carrier'].sudo().search(
            [('delivery_type', '=', 'shipstation'),
             ('company_id.id', '=', company_id)], limit=1)


    @http.route('/shipments/<int:company_id>', type='json2', auth='public', methods=['POST'], csrf=False)
    def post_shipments(self, company_id=0, **kwargs):
        resource_url = kwargs.get('resource_url')
        ship_station = self._get_carrier(company_id)
        if not ship_station:
            logger.warning("Shipstation delivery method was not found")
            return

        ship_station.process_order(resource_url)
        return {"message": "Api called Successfully", "status_code": 200}

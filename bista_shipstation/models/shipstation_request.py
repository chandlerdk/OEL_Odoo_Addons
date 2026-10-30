# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo.addons.delivery_shipstation.models.shipstation_request import ShipStation


class ShipStationRequest(ShipStation):
    """Odoo's ShipStation client. Only the Bista extras are implemented here.

    Rate, label, void, return and carrier list are the base methods. This class
    adds the company address, third-party billing, store, ship via, salesperson,
    order note and kit lines.
    """

    def __init__(self, carrier, debug_logger):
        super().__init__(carrier, debug_logger)
        self.api_key = carrier.sudo().shipstation_production_api_key
        self.api_secret = carrier.sudo().shipstation_production_api_secret
        self._bista_picking = False

    def _make_api_request(self, method, endpoint, data=None, params=None, timeout=False):
        """Public access to Odoo's private HTTP call.

        Accepts either ``(method, endpoint)`` or the older ``(endpoint, request_type)`` order.
        """
        if isinstance(method, str) and method.startswith('/') and endpoint in ('get', 'post', 'put', 'delete'):
            method, endpoint = endpoint.upper(), method.lstrip('/')
        return self._ShipStation__make_api_request(method, endpoint, data=data, params=params)

    def _prepare_address_values(self, partner):
        values = super()._prepare_address_values(partner)
        company = partner.parent_id.name or ''
        if company:
            values['company_name'] = company
        values['address_residential_indicator'] = 'yes'
        return values

    def _bista_advanced_options(self, picking):
        """Fields Odoo does not put on the label. Additional handling stays on the base request."""
        options = {}
        partner = picking.sale_id.partner_id if picking.sale_id else picking.partner_id
        if partner.third_part_account:
            options.update({
                'bill_to_party': 'third_party',
                'bill_to_account': partner.bill_account or '',
                'bill_to_postal_code': partner.bill_postal_code or '',
                'bill_to_country_code': partner.bill_country_code.code if partner.bill_country_code else '',
            })
        store = self.carrier.store_id
        if store and store.store_id:
            options['store_id'] = str(store.store_id)
        ship_via = (picking.sale_id.ship_via if picking.sale_id else False) or picking.partner_id.ship_via
        if ship_via:
            options['custom_field1'] = ship_via
        if picking.user_id:
            options['custom_field2'] = picking.user_id.name
        note = picking.sale_id.name if picking.sale_id else ''
        if picking.sale_id and picking.sale_id.client_order_ref:
            note = '%s - %s' % (note, picking.sale_id.client_order_ref)
        if note:
            options['custom_field3'] = note
        return options

    def _bista_kit_items(self, picking):
        """One ShipStation item per kit. Components stay on Odoo's item list."""
        if not picking:
            return []
        items = []
        seen_sale_lines = set()
        weight_uom = self.carrier.env['product.template'].sudo()._get_weight_uom_id_from_ir_config_parameter()
        pound = self.carrier.env.ref('uom.product_uom_lb')
        for move in picking.move_ids:
            sale_line = move.sale_line_id
            if not sale_line or not move.product_uom_qty or sale_line.id in seen_sale_lines:
                continue
            product = sale_line.product_id
            is_kit = 'bom_ids' in product._fields and product.bom_ids[:1].type == 'phantom'
            if not is_kit:
                continue
            seen_sale_lines.add(sale_line.id)
            bom = product.bom_ids[:1]
            delivered = float('inf')
            for bom_line in bom.bom_line_ids:
                component_moves = picking.move_ids.filtered(lambda m: m.product_id == bom_line.product_id)
                done_qty = sum(component_moves.mapped('quantity'))
                kit_qty = done_qty / bom_line.product_qty if bom_line.product_qty else 0
                delivered = min(delivered, kit_qty)
            currency = picking.sale_id.currency_id or picking.company_id.currency_id
            items.append({
                'name': product.name,
                'sku': product.default_code or '',
                'quantity': int(delivered) if delivered != float('inf') else 0,
                'weight': {
                    'value': weight_uom._compute_quantity(product.weight, pound),
                    'unit': 'pound',
                },
                'value': {
                    'currency': currency.name,
                    'amount': sale_line.price_unit,
                },
            })
        return items

    def _prepare_package_information(self, packages):
        packages_data, items_data = super()._prepare_package_information(packages)
        kit_items = self._bista_kit_items(self._bista_picking)
        if kit_items:
            kit_skus = {kit['sku'] for kit in kit_items if kit.get('sku')}
            items_data = kit_items + [item for item in items_data if item.get('sku') not in kit_skus]
        return packages_data, items_data

    def _create_shipment_label(self, recipient, shipper, picking, packages):
        """Odoo builds and sends the label. Bista only adds its advanced options to that request."""
        self._bista_picking = picking
        original = self._ShipStation__make_api_request

        def _with_bista_options(method, endpoint, data=None, params=None):
            shipment = data.get('shipment') if isinstance(data, dict) else None
            if endpoint == 'v2/labels' and isinstance(shipment, dict):
                extra = self._bista_advanced_options(picking)
                if extra:
                    options = dict(shipment.get('advanced_options') or {})
                    options.update(extra)
                    shipment['advanced_options'] = options
            return original(method, endpoint, data=data, params=params)

        self._ShipStation__make_api_request = _with_bista_options
        try:
            return super()._create_shipment_label(recipient, shipper, picking, packages)
        finally:
            del self._ShipStation__make_api_request
            self._bista_picking = False


# Odoo builds ShipStation() inside its carrier methods. Use this subclass so the extras apply.
from odoo.addons.delivery_shipstation.models import delivery_carrier as shipstation_carrier_model  # noqa: E402

shipstation_carrier_model.ShipStation = ShipStationRequest

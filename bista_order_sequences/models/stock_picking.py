from odoo import api, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def _get_order_based_name(self, order, picking_type, seq_xmlid, used_names):
        order_seq = self.env.ref(seq_xmlid, raise_if_not_found=False)
        order_prefix = order_seq.sudo()._get_prefix_suffix()[0] if order_seq else ''
        picking_prefix = picking_type.sequence_id.sudo()._get_prefix_suffix()[0] if picking_type.sequence_id else ''
        order_number = order.name.replace(order_prefix, '') if order_prefix else order.name
        company = picking_type.company_id or self.env.company

        count = len(order.picking_ids.filtered(lambda p: p.picking_type_id == picking_type))
        while True:
            suffix = f"-{count + 1}" if count else ""
            name = f"{picking_prefix}{order_number}{suffix}"
            if name not in used_names and not self.sudo().search_count(
                    [('name', '=', name), ('company_id', '=', company.id)], limit=1):
                return name
            count += 1

    @api.model_create_multi
    def create(self, vals_list):
        """Picking'name must have PO or SO name in it."""
        used_names = set()
        for vals in vals_list:
            origin = vals.get('origin')
            if not origin or vals.get('name', '/') != '/':
                continue
            defaults = self.default_get(['picking_type_id'])
            picking_type = self.env['stock.picking.type'].browse(vals.get('picking_type_id', defaults.get('picking_type_id')))
            if not picking_type:
                continue

            name = False
            if so := self.env['sale.order'].sudo().search([('name', '=', origin)], limit=1):
                name = self._get_order_based_name(so, picking_type, 'sale.seq_sale_order', used_names)
            if po := self.env['purchase.order'].sudo().search([('name', '=', origin)], limit=1):
                name = self._get_order_based_name(po, picking_type, 'purchase.seq_purchase_order', used_names)
            if name:
                vals['name'] = name
                used_names.add(name)

        return super().create(vals_list)

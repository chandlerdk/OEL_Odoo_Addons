from odoo import models, fields, api
import logging


_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = "stock.picking"

    sale_channel = fields.Many2one('shopify.channel', string="Channel")

    @api.model_create_multi
    def create(self, vals_list):
        """Picking'name must have PO or SO name in it."""
        for vals in vals_list:
            if origin := vals.get('origin', ''):
                defaults = self.default_get(['name', 'picking_type_id'])
                picking_type = self.env['stock.picking.type'].browse(vals.get('picking_type_id', defaults.get('picking_type_id')))
                sequence_prefix = picking_type.sequence_id.prefix
                so_obj = self.env['sale.order']
                if so := so_obj.search([('name', '=', origin)], limit=1):
                    same_picking_count = len(so.picking_ids.filtered(lambda p: p.picking_type_id == picking_type))
                    number = same_picking_count and f"-{same_picking_count + 1}" or ""
                    order_number = f"{so.name.replace(self.env.ref('sale.seq_sale_order').prefix, '')}{number}"
                    vals.update({
                        'name': f"{sequence_prefix}{order_number}",
                    })

                po_obj = self.env['purchase.order']
                if po := po_obj.search([('name', '=', origin)], limit=1):
                    same_picking_count = len(po.picking_ids.filtered(lambda p: p.picking_type_id == picking_type))
                    number = same_picking_count and f"-{same_picking_count + 1}" or ""
                    order_number = f"{po.name.replace(self.env.ref('purchase.seq_purchase_order').prefix, '')}{number}"
                    vals.update({
                        'name': f"{sequence_prefix}{order_number}",
                    })

        return super(StockPicking, self).create(vals_list)
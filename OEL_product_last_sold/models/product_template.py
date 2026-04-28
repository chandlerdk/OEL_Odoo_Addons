from odoo import models, fields, api


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    last_sold_date = fields.Datetime(
        string='Last Sold',
        compute='_compute_last_sold_date',
        store=True,
        readonly=True,
        help='Date of the last confirmed sale order containing this product.',
    )

    @api.depends('product_variant_ids.last_sold_date')
    def _compute_last_sold_date(self):
        for tmpl in self:
            dates = tmpl.product_variant_ids.mapped('last_sold_date')
            dates = [d for d in dates if d]
            tmpl.last_sold_date = max(dates) if dates else False


class ProductProduct(models.Model):
    _inherit = 'product.product'

    last_sold_date = fields.Datetime(
        string='Last Sold',
        compute='_compute_last_sold_date',
        store=True,
        readonly=True,
        help='Date of the last confirmed sale order containing this product.',
    )

    @api.depends('sale_ok')
    def _compute_last_sold_date(self):
        """
        Intentional stub — real values are written by
        _recompute_last_sold on sale.order.line.
        We depend on sale_ok just to satisfy the ORM;
        actual updates are triggered via write on sale.order.
        """
        # Only fill in for new records that have no value yet
        for product in self:
            if not product.last_sold_date:
                product.last_sold_date = False


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def _update_product_last_sold(self):
        """Recompute last_sold_date for all products on these lines."""
        products = self.mapped('product_id')
        for product in products:
            last_order = self.env['sale.order.line'].search(
                [
                    ('product_id', '=', product.id),
                    ('order_id.state', 'in', ('sale', 'done')),
                ],
                order='order_id.date_order desc',
                limit=1,
            )
            product.last_sold_date = last_order.order_id.date_order if last_order else False

    def write(self, vals):
        res = super().write(vals)
        if 'product_id' in vals or 'order_id' in vals:
            self._update_product_last_sold()
        return res


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        res = super().action_confirm()
        self.order_line._update_product_last_sold()
        return res

    def action_cancel(self):
        res = super().action_cancel()
        self.order_line._update_product_last_sold()
        return res

from odoo import fields, models


class SaleReport(models.Model):
    _inherit = 'sale.report'

    flag = fields.Boolean(string='Complete Order', readonly=True)

    def _select_dict(self, table):
        # sale.order.flag is a stored boolean. Selecting it here keeps the
        # SQL view column boolean so pivot grouping can COALESCE it with FALSE.
        return super()._select_dict(table) | {
            'flag': table.order_id.flag,
        }

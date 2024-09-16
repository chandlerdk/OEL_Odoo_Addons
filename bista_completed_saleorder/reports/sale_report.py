from odoo import fields, models


class SaleReport(models.Model):
    _inherit = 'sale.report'

    complete_order = fields.Boolean('Complete Orders')

    def _select_additional_fields(self):
        res = super()._select_additional_fields()
        res['complete_order'] = "s.complete_order"
        return res

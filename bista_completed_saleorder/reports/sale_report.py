from odoo import fields, models


class SaleReport(models.Model):
    _inherit = 'sale.report'

    flag = fields.Boolean('Margin')

    def _select_additional_fields(self):
        res = super()._select_additional_fields()
        res['flag'] = "s.flag"
        return res

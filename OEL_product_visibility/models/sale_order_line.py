from odoo import models, api, _
from odoo.exceptions import ValidationError

class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    @api.constrains('product_id', 'order_id')
    def _check_product_visibility(self):
        for line in self:
            if line.product_id and line.order_id.partner_id:
                if not line.product_id.product_tmpl_id.check_visibility_for_partner(line.order_id.partner_id):
                    raise ValidationError(_(
                        "The product '%s' is restricted for customer '%s'."
                    ) % (line.product_id.name, line.order_id.partner_id.name))

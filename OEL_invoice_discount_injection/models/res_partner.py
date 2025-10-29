# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

class ResPartner(models.Model):
    _inherit = 'res.partner'

    x_has_global_discount = fields.Boolean(
        string="Global Discount Enabled",
        help="If enabled, all invoices for this customer will include a global discount line."
    )
    x_global_discount_type = fields.Selection(
        [('percent', 'Percent'), ('fixed', 'Fixed Amount')],
        string="Discount Type",
        default='percent'
    )
    x_global_discount_value = fields.Float(
        string="Discount Value",
        help="Percent (e.g. 10 for 10%) or Fixed amount in company currency."
    )
    x_global_discount_product_id = fields.Many2one(
        'product.product',
        string="Discount Product",
        help="Optional product used for the discount line. Configure income account and taxes."
    )

    @api.constrains('x_global_discount_value', 'x_global_discount_type')
    def _check_discount_value(self):
        for partner in self:
            if partner.x_has_global_discount:
                if partner.x_global_discount_type == 'percent':
                    if partner.x_global_discount_value < 0 or partner.x_global_discount_value > 100:
                        raise ValidationError(_("Percent discount must be between 0 and 100."))
                else:
                    if partner.x_global_discount_value < 0:
                        raise ValidationError(_("Fixed discount must be >= 0."))

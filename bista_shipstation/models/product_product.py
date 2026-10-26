from odoo import fields, models


class ProductProduct(models.Model):
    _inherit = "product.product"

    to_sync = fields.Boolean(default=False, help="If True, this product will be synced with ShipStation.")

    def write(self, vals):
        sync_fields = ["hs_code"]
        if any(x in sync_fields for x in vals):
            vals.update({"to_sync": True})
        return super(ProductProduct, self).write(vals)

from odoo import models, fields, api
from odoo.osv import expression

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    enable_customer_visibility_rules = fields.Boolean(
        string='Enable Customer Visibility Rules',
        default=False
    )
    allowed_partner_ids = fields.Many2many(
        'res.partner', 'product_template_allowed_partner_rel',
        'template_id', 'partner_id',
        string='Allowed Customers',
        domain=[('is_company', '=', True)]
    )
    disallowed_partner_ids = fields.Many2many(
        'res.partner', 'product_template_disallowed_partner_rel',
        'template_id', 'partner_id',
        string='Disallowed Customers',
        domain=[('is_company', '=', True)]
    )

    def check_visibility_for_partner(self, partner):
        self.ensure_one()
        if not self.enable_customer_visibility_rules:
            return True
        commercial_partner = partner.commercial_partner_id
              if commercial_partner in self.disallowed_partner_ids:
            return False
        if self.allowed_partner_ids and commercial_partner not in self.allowed_partner_ids:
            return False
        return True

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        # Capture the customer context passed from the Sale Order View
        partner_id = self.env.context.get('sale_order_partner_id') or self.env.context.get('partner_id')

        if partner_id and isinstance(partner_id, int):
            partner = self.env['res.partner'].browse(partner_id)
            if partner.exists():
                c_id = partner.commercial_partner_id.id

                # Logic: Visible if (Rules Off) OR (Not Disallowed AND (Allowed list empty OR In Allowed list))
                visibility_domain = [
                    '|',
                    ('enable_customer_visibility_rules', '=', False),
                    '&',
                    ('disallowed_partner_ids', '!=', c_id),
                    '|',
                    ('allowed_partner_ids', '=', False),
                    ('allowed_partner_ids', 'in', c_id)
                ]
                domain = expression.AND([domain, visibility_domain])
              
        return super()._search(domain, offset, limit, order, access_rights_uid)

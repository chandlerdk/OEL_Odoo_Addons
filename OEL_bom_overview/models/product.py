# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ProductTemplate(models.Model):
    _inherit = "product.template"

    has_kit_bom = fields.Boolean(
        string="Has Kit BoM",
        compute="_compute_has_kit_bom",
        help="True if this product has at least one Kit (phantom) BoM"
    )

    @api.depends('bom_ids', 'bom_ids.type')
    def _compute_has_kit_bom(self):
        """Check if product has at least one Kit BoM (type = phantom)."""
        Bom = self.env["mrp.bom"]
        company = self.env.company
        
        for tmpl in self:
            # Only count phantom (Kit) BoMs
            cnt = Bom.search_count([
                ("type", "=", "phantom"),
                ("product_tmpl_id", "=", tmpl.id),
                "|", ("company_id", "=", False), ("company_id", "=", company.id),
            ])
            tmpl.has_kit_bom = bool(cnt)

    def _get_preferred_kit_bom(self):
        """
        Pick the most relevant Kit BoM for this template.
        Priority: variant-specific > company-specific > sequence
        """
        self.ensure_one()
        company = self.env.company
        variant = self.product_variant_id
        
        domain = [
            ("type", "=", "phantom"),
            ("product_tmpl_id", "=", self.id),
            "|", ("company_id", "=", False), ("company_id", "=", company.id),
            "|", ("product_id", "=", False), ("product_id", "=", variant.id),
        ]
        
        return self.env["mrp.bom"].search(
            domain,
            order="product_id desc, company_id desc, sequence asc, id asc",
            limit=1
        )

    def action_open_kit_bom_overview(self):
        """Open BoM Overview for this product's Kit BoM."""
        self.ensure_one()
        
        bom = self._get_preferred_kit_bom()
        if not bom:
            raise UserError(
                _("No Kit BoM found for product '%s'.", self.display_name)
            )

        return {
            "type": "ir.actions.client",
            "tag": "mrp_bom_report",
            "name": _("BoM Overview - %s", self.display_name),
            "context": {
                "active_model": "mrp.bom",
                "active_id": bom.id,
                "active_ids": [bom.id],
            },
        }


class ProductProduct(models.Model):
    _inherit = "product.product"

    has_kit_bom = fields.Boolean(
        related="product_tmpl_id.has_kit_bom",
        readonly=True,
        store=False
    )

    def action_open_kit_bom_overview(self):
        """Delegate to template logic."""
        self.ensure_one()
        return self.product_tmpl_id.action_open_kit_bom_overview()

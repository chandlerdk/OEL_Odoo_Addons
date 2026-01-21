# -*- coding: utf-8 -*-
from odoo import api, fields, models, _

class MailActivity(models.Model):
    _inherit = "mail.activity"

    # Explicit contact field for the activity
    contact_id = fields.Many2one(
        "res.partner",
        string="Contact",
        help="Contact related to this activity.",
    )
    # Convenience related fields for display in views
    contact_parent_id = fields.Many2one(
        "res.partner",
        string="Org",
        related="contact_id.parent_id",
        store=False,
        readonly=True,
    )
    contact_email = fields.Char(
        string="Email",
        related="contact_id.email",
        store=False,
        readonly=True,
    )
    contact_phone = fields.Char(
        string="Phone",
        related="contact_id.phone",
        store=False,
        readonly=True,
    )

    @api.model
    def create(self, vals):
        """
        When an activity is created from another document (SO, PO, Contact, etc),
        auto-link the contact based on common field names if not explicitly set.
        """
        activities = super().create(vals)
        for act in activities:
            if not act.contact_id and act.res_model and act.res_id:
                record = self.env[act.res_model].browse(act.res_id)

                partner = False
                # Most common patterns to find a partner on the source doc
                for field_name in ("partner_id", "commercial_partner_id", "company_id"):
                    if hasattr(record, field_name):
                        partner = getattr(record, field_name)
                        if partner:
                            break
                # Fallback for models like sale.order (shipping partner)
                if not partner and hasattr(record, "partner_shipping_id"):
                    partner = record.partner_shipping_id

                if partner:
                    act.contact_id = partner.id

        return activities

    def action_mark_done_oel(self):
        """
        Called from the custom 'Mark as Done' button on the activity form.

        - Uses the standard Odoo 'action_feedback' to mark done.
        - Immediately opens a new activity form, prefilled with:
          * same document (res_model/res_id)
          * same contact
          * same user
        """
        self.ensure_one()

        # Standard "mark as done" behavior
        self.action_feedback()

        # Prompt for a follow-up activity
        return {
            "type": "ir.actions.act_window",
            "name": _("New Activity"),
            "res_model": "mail.activity",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_res_model": self.res_model,
                "default_res_id": self.res_id,
                "default_contact_id": self.contact_id.id,
                "default_user_id": self.user_id.id,
            },
        }

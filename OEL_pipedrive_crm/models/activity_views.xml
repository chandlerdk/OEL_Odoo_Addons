# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


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

    @api.model_create_multi
    def create(self, vals_list):
        """
        Behaviors:
        - If an activity is created WITHOUT res_model/res_id but WITH contact_id
          (from our standalone CRM actions), auto-link it to that contact:
            * res_model_id  -> ir.model for 'res.partner'
            * res_model     -> 'res.partner'
            * res_id        -> contact_id
        - If an activity is created FROM another document (SO, Contact, etc.)
          and contact_id is not explicitly set, auto-derive contact_id from
          common partner fields on the source document.
        """
        partner_model = self.env["ir.model"]._get("res.partner")

        # Pre-process values before calling super
        for vals in vals_list:
            has_document = bool(vals.get("res_model_id") or vals.get("res_model") or vals.get("res_id"))
            contact_id = vals.get("contact_id")

            # Case 1: standalone activity from our CRM views -> bind to contact
            if not has_document and contact_id:
                vals["res_model_id"] = partner_model.id
                vals["res_model"] = "res.partner"
                vals["res_id"] = contact_id

        activities = super().create(vals_list)

        # Case 2: activity created from a document -> derive contact_id if missing
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

    def action_open_document_oel(self):
        """
        Opens the linked document (res_model/res_id) in form view.
        """
        self.ensure_one()
        if not self.res_model or not self.res_id:
            raise UserError(_("No document is linked to this activity."))

        return {
            "type": "ir.actions.act_window",
            "name": _("Document"),
            "res_model": self.res_model,
            "res_id": self.res_id,
            "view_mode": "form",
            "target": "current",
        }

    def action_view_contact_oel(self):
        """
        Opens the related contact (contact_id) in form view.
        """
        self.ensure_one()
        if not self.contact_id:
            raise UserError(_("No contact is linked to this activity."))

        return {
            "type": "ir.actions.act_window",
            "name": _("Contact"),
            "res_model": "res.partner",
            "res_id": self.contact_id.id,
            "view_mode": "form",
            "target": "current",
        }

# -*- coding: utf-8 -*-
from odoo import api, fields, models, _

class CrmLead(models.Model):
    _inherit = "crm.lead"

    def action_convert_to_prospect(self):
        """
        Convert selected leads into Prospects (res.partner) for the
        custom OEL Sales Hub, without creating opportunities.

        Repeatable: if partner already exists/linked, it reuses it.
        """
        Partner = self.env["res.partner"]
        created_partners = Partner.browse()

        for lead in self:
            # only process plain leads, skip opportunities
            if lead.type != "lead":
                continue

            partner = lead.partner_id

            # Try to reuse partner using email if not linked already
            if not partner and lead.email_from:
                partner = Partner.search(
                    [("email", "=", lead.email_from)], limit=1
                )

            if not partner:
                partner_vals = {
                    "name": lead.contact_name or lead.partner_name or lead.name,
                    "email": lead.email_from,
                    "phone": lead.phone,
                    "mobile": lead.mobile,
                    "street": lead.street,
                    "street2": lead.street2,
                    "city": lead.city,
                    "state_id": lead.state_id.id,
                    "zip": lead.zip,
                    "country_id": lead.country_id.id,
                    "type": "contact",
                    # If you use this custom status field for Prospects:
                    # "customer_status": "prospect",
                }
                partner = Partner.create(partner_vals)
                created_partners |= partner

            # link the lead to that partner
            lead.partner_id = partner

            # optionally archive lead to keep inbox clean
            # comment this out if you want to keep leads visible
            lead.active = False

        # After conversion, open the created/linked Prospects list
        if created_partners:
            return {
                "type": "ir.actions.act_window",
                "name": _("Prospects"),
                "res_model": "res.partner",
                "view_mode": "tree,form",
                "domain": [("id", "in", created_partners.ids)],
            }
        # If we didn't create new partners (everything already had partners),
        # just go back to the leads list.
        return True

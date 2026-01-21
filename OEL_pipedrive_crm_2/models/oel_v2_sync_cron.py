# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class OelV2SyncContactsCron(models.Model):
    _name = 'oel.v2.sync.contacts.cron'
    _description = 'OEL v2 Sync Contacts to Prospects (Cron Helper)'

    name = fields.Char(default="OEL v2 Sync Contacts to Prospects Helper")

    @api.model
    def run_sync_contacts_to_prospects(self):
        """
        Sync partners into crm.lead opportunities as "prospects"
        using ONLY standard fields.

        Partner domain (prospect-like contacts):
          - type = 'contact'              -> not invoice/delivery/other
          - active = True
          - name not ilike 'A/P'
          - name not ilike 'accounts payable'

        Additional filters:
          - must NOT have sale orders in 'sale' or 'done'
          - must NOT already have an opportunity with this partner_id
        """
        Partner = self.env['res.partner']
        Lead = self.env['crm.lead']
        SaleOrder = self.env['sale.order']

        # 1) Partner domain: contact-type records, excluding AP-style names
        partners = Partner.search([
            ('type', '=', 'contact'),
            ('active', '=', True),
            ('name', 'not ilike', 'A/P'),
            ('name', 'not ilike', 'accounts payable'),
        ])

        created_count = 0
        skipped_has_orders = 0
        skipped_existing_opp = 0

        for partner in partners:
            # Skip if this partner has sale/done orders (they are already customers)
            has_order = SaleOrder.search_count([
                ('partner_id', '=', partner.id),
                ('state', 'in', ['sale', 'done']),
            ])
            if has_order:
                skipped_has_orders += 1
                continue

            # Skip if there is already an opportunity for this partner
            existing_opp = Lead.search([
                ('type', '=', 'opportunity'),
                ('partner_id', '=', partner.id),
            ], limit=1)
            if existing_opp:
                skipped_existing_opp += 1
                continue

            # Build lead values using standard fields only
            vals = {
                'name': partner.name or _('Opportunity for %s') % partner.display_name,
                'type': 'opportunity',
                'partner_id': partner.id,
                # map partner contact info
                'contact_name': partner.name or partner.display_name,
                'email_from': partner.email or False,
                'phone': partner.phone or partner.mobile or False,
                # keep them clearly as "prospects" via probability
                'probability': 10,
            }

            Lead.create(vals)
            created_count += 1

        # Optionally return something, but cron ignores it
        return True

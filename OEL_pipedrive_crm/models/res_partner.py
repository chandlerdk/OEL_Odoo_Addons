# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    org_status = fields.Selection([
        ('prospect', 'Prospect'),
        ('customer', 'Customer'),
    ], string="Status", default='prospect', tracking=True)

    # Company Type radio field (syncs with is_company)
    company_type = fields.Selection(
        string='Company Type',
        selection=[('person', 'Individual'), ('company', 'Company')],
        compute='_compute_company_type',
        inverse='_inverse_company_type',
        store=False
    )

    # Smart button counts
    opportunity_count = fields.Integer(compute='_compute_opportunity_count')
    quote_count = fields.Integer(compute='_compute_quote_count')
    active_quote_count = fields.Integer(compute='_compute_active_quote_count')
    cancelled_quote_count = fields.Integer(compute='_compute_cancelled_quote_count')
    order_count = fields.Integer(compute='_compute_order_count')
    invoice_count = fields.Integer(compute='_compute_invoice_count')
    delivery_count = fields.Integer(compute='_compute_delivery_count')
    document_count = fields.Integer(compute='_compute_document_count')
    task_count = fields.Integer(compute='_compute_task_count', string="Tasks")
    mail_history_count = fields.Integer(compute='_compute_mail_history_count', string='Emails')

    # Technical Many2many to all opportunities of this partner (and its children)
    partner_opportunity_ids = fields.Many2many(
        'crm.lead',
        'res_partner_crm_lead_rel',      # relation table name
        'partner_id',                     # column for partner
        'lead_id',                        # column for lead
        string="All Opportunities (Technical)",
        compute='_compute_partner_opportunity_ids',
        store=True                        # stored so it is searchable in domains
    )

    # Display helpers for conditional view logic
    display_title_name = fields.Char(compute='_compute_display_names', store=False)
    display_contact_name = fields.Char(compute='_compute_display_names', store=False)

    # Mail History
    custom_mail_history_ids = fields.One2many(
        'oel.mail.history',
        'partner_id',
        string='Mail History'
    )

    # -------------------------------------------------------------------------
    # Company type (radio) <-> is_company
    # -------------------------------------------------------------------------
    @api.depends('is_company')
    def _compute_company_type(self):
        for partner in self:
            partner.company_type = 'company' if partner.is_company else 'person'

    def _inverse_company_type(self):
        for partner in self:
            partner.is_company = (partner.company_type == 'company')

    # -------------------------------------------------------------------------
    # BREAK default parent address copy in CRM UI only
    # Use type='other' for sub-contacts so they have independent addresses
    # -------------------------------------------------------------------------

    @api.onchange('parent_id')
    def _onchange_parent_id(self):
        """
        Odoo 17: don't call super() here because base res.partner may not define
        _onchange_parent_id() (depends on installed modules/version).

        In custom CRM UI (oel_crm_ui):
          - link to company via parent_id
          - force type='other'
          - do NOT copy address from company

        Outside CRM UI:
          - do nothing (native behavior continues without our interference)
        """
        if not self.env.context.get('oel_crm_ui'):
            return

        for partner in self:
            if partner.parent_id:
                # Treat as an independent "Other Address" under that company
                partner.type = 'other'
                # Do NOT touch street, city, zip, state, country, etc.
                # Address remains whatever the user enters.

    @api.model_create_multi
    def create(self, vals_list):
        """
        In CRM UI:
          - keep child contacts as 'other' type when parent_id is set,
          - do NOT force addresses to match parent.
        Everywhere else, standard create.
        """
        if not self.env.context.get('oel_crm_ui'):
            return super().create(vals_list)

        new_vals_list = []
        for vals in vals_list:
            vals = dict(vals)
            if vals.get('parent_id'):
                # Sub-contact in CRM UI is an "Other Address"
                vals.setdefault('type', 'other')
            new_vals_list.append(vals)

        partners = super().create(new_vals_list)
        return partners

    def write(self, vals):
        """
        In CRM UI:
          - if parent_id is set, keep type='other',
          - never auto-sync the address from the company.
        Elsewhere, use standard behavior.
        """
        if not self.env.context.get('oel_crm_ui'):
            return super().write(vals)

        vals = dict(vals)
        if 'parent_id' in vals and vals.get('parent_id'):
            # Keep as "Other Address" when linked to a company
            vals.setdefault('type', 'other')

        return super().write(vals)

    # -------------------------------------------------------------------------
    # Display names
    # -------------------------------------------------------------------------
    @api.depends('name', 'parent_id', 'parent_id.name')
    def _compute_display_names(self):
        for rec in self:
            if rec.parent_id:
                rec.display_title_name = rec.parent_id.name or rec.name or ''
                rec.display_contact_name = rec.name or ''
            else:
                rec.display_title_name = rec.name or ''
                rec.display_contact_name = ''

    # -------------------------------------------------------------------------
    # Counts
    # -------------------------------------------------------------------------
    @api.depends('sale_order_ids.state', 'child_ids.sale_order_ids.state')
    def _compute_quote_count(self):
        for partner in self:
            partners = partner | partner.child_ids
            partner.quote_count = self.env['sale.order'].search_count([
                ('partner_id', 'in', partners.ids),
                ('state', 'in', ['draft', 'sent'])
            ])

    @api.depends('sale_order_ids.state', 'child_ids.sale_order_ids.state')
    def _compute_active_quote_count(self):
        for partner in self:
            partners = partner | partner.child_ids
            partner.active_quote_count = self.env['sale.order'].search_count([
                ('partner_id', 'in', partners.ids),
                ('state', 'in', ['draft', 'sent'])
            ])

    @api.depends('sale_order_ids.state', 'child_ids.sale_order_ids.state')
    def _compute_cancelled_quote_count(self):
        for partner in self:
            partners = partner | partner.child_ids
            partner.cancelled_quote_count = self.env['sale.order'].search_count([
                ('partner_id', 'in', partners.ids),
                ('state', '=', 'cancel')
            ])

    @api.depends('sale_order_ids.state', 'child_ids.sale_order_ids.state')
    def _compute_order_count(self):
        for partner in self:
            partners = partner | partner.child_ids
            partner.order_count = self.env['sale.order'].search_count([
                ('partner_id', 'in', partners.ids),
                ('state', 'in', ['sale', 'done'])
            ])

    @api.depends('child_ids')
    def _compute_opportunity_count(self):
        for partner in self:
            partners = partner | partner.child_ids
            if 'crm.lead' in self.env:
                partner.opportunity_count = self.env['crm.lead'].search_count([
                    ('partner_id', 'in', partners.ids),
                    ('type', '=', 'opportunity')
                ])
            else:
                partner.opportunity_count = 0

    @api.depends('child_ids')
    def _compute_partner_opportunity_ids(self):
        """Technical Many2many listing all opportunities for partner + children."""
        Lead = self.env['crm.lead']
        for partner in self:
            partners = partner | partner.child_ids
            if 'crm.lead' in self.env:
                opps = Lead.search([
                    ('partner_id', 'in', partners.ids),
                    ('type', '=', 'opportunity')
                ])
                partner.partner_opportunity_ids = opps
            else:
                partner.partner_opportunity_ids = Lead.browse()

    @api.depends('child_ids', 'child_ids.invoice_count')
    def _compute_invoice_count(self):
        for partner in self:
            partners = partner | partner.child_ids
            if 'account.move' in self.env:
                partner.invoice_count = self.env['account.move'].search_count([
                    ('partner_id', 'in', partners.ids),
                    ('move_type', '=', 'out_invoice')
                ])
            else:
                partner.invoice_count = 0

    @api.depends('sale_order_ids.state', 'child_ids.sale_order_ids.state')
    def _compute_delivery_count(self):
        for partner in self:
            partners = partner | partner.child_ids
            sale_orders = self.env['sale.order'].search([
                ('partner_id', 'in', partners.ids),
                ('state', 'in', ['sale', 'done'])
            ])
            procurement_group_ids = sale_orders.mapped('procurement_group_id').ids
            if 'stock.picking' in self.env:
                partner.delivery_count = self.env['stock.picking'].search_count([
                    ('group_id', 'in', procurement_group_ids),
                    ('picking_type_code', '=', 'outgoing')
                ])
            else:
                partner.delivery_count = 0

    @api.depends('child_ids', 'child_ids.document_count')
    def _compute_document_count(self):
        for partner in self:
            partners = partner | partner.child_ids
            partner.document_count = self.env['ir.attachment'].search_count([
                ('res_model', '=', 'res.partner'),
            ])

    @api.depends('child_ids', 'child_ids.task_count')
    def _compute_task_count(self):
        for partner in self:
            partners = partner | partner.child_ids
            partner.task_count = self.env['project.task'].search_count([
                ('partner_id', 'in', partners.ids)
            ])

    @api.depends('custom_mail_history_ids')
    def _compute_mail_history_count(self):
        for rec in self:
            rec.mail_history_count = len(rec.custom_mail_history_ids)

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------
    def action_view_opportunities(self):
        partners = self | self.mapped('child_ids')
        return {
            'name': 'Opportunities',
            'type': 'ir.actions.act_window',
            'res_model': 'crm.lead',
            'view_mode': 'tree,form',
            'domain': [('partner_id', 'in', partners.ids), ('type', '=', 'opportunity')],
            'context': {'default_partner_id': self.id},
        }

    def action_view_quotes(self):
        partners = self | self.mapped('child_ids')
        return {
            'name': 'Quotations',
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'view_mode': 'tree,form',
            'domain': [('partner_id', 'in', partners.ids), ('state', 'in', ['draft', 'sent'])],
            'context': {'default_partner_id': self.id},
        }

    def action_view_active_quotes(self):
        partners = self | self.mapped('child_ids')
        return {
            'name': 'Active Quotations',
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'view_mode': 'tree,form',
            'domain': [('partner_id', 'in', partners.ids), ('state', 'in', ['draft', 'sent'])],
            'context': {'default_partner_id': self.id},
        }

    def action_view_cancelled_quotes(self):
        partners = self | self.mapped('child_ids')
        return {
            'name': 'Cancelled Quotations',
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'view_mode': 'tree,form',
            'domain': [('partner_id', 'in', partners.ids), ('state', '=', 'cancel')],
            'context': {'default_partner_id': self.id},
        }

    def action_view_orders(self):
        partners = self | self.mapped('child_ids')
        return {
            'name': 'Sales Orders',
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'view_mode': 'tree,form',
            'domain': [('partner_id', 'in', partners.ids), ('state', 'in', ['sale', 'done'])],
            'context': {'default_partner_id': self.id},
        }

    def action_view_invoices(self):
        partners = self | self.mapped('child_ids')
        return {
            'name': 'Customer Invoices',
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'tree,form',
            'domain': [('partner_id', 'in', partners.ids), ('move_type', '=', 'out_invoice')],
            'context': {'default_partner_id': self.id, 'default_move_type': 'out_invoice'},
        }

    def action_view_deliveries(self):
        partners = self | self.mapped('child_ids')
        sale_orders = self.env['sale.order'].search([
            ('partner_id', 'in', partners.ids),
            ('state', 'in', ['sale', 'done'])
        ])
        procurement_group_ids = sale_orders.mapped('procurement_group_id').ids
        return {
            'name': 'Deliveries',
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'view_mode': 'tree,form',
            'domain': [('group_id', 'in', procurement_group_ids), ('picking_type_code', '=', 'outgoing')],
            'context': {'default_partner_id': self.id},
        }

    def action_view_documents(self):
        partners = self | self.mapped('child_ids')
        return {
            'name': 'Documents',
            'type': 'ir.actions.act_window',
            'res_model': 'ir.attachment',
            'view_mode': 'tree,form',
            'domain': [('res_model', '=', 'res.partner'), ('res_id', 'in', partners.ids)],
            'context': {'default_res_model': 'res.partner', 'default_res_id': self.id},
        }

    def action_view_tasks(self):
        partners = self | self.mapped('child_ids')
        return {
            'name': 'Tasks',
            'type': 'ir.actions.act_window',
            'res_model': 'project.task',
            'view_mode': 'kanban,tree,form,calendar',
            'domain': [('partner_id', 'in', partners.ids)],
            'context': {'default_partner_id': self.id},
        }

    def action_view_standard_profile(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Contact',
            'res_model': 'res.partner',
            'res_id': self.id,
            'view_mode': 'form',
            'view_id': self.env.ref('base.view_partner_form').id,
            'target': 'current',
        }

    def action_send_email(self):
        """Open email compose wizard"""
        return {
            'name': 'Send Email',
            'type': 'ir.actions.act_window',
            'res_model': 'oel.mail.compose.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_partner_id': self.id,
            },
        }

    def action_open_mail_history(self):
        """Open mail history list"""
        return {
            'name': 'Mail History',
            'type': 'ir.actions.act_window',
            'res_model': 'oel.mail.history',
            'view_mode': 'tree,form',
            'domain': [('partner_id', '=', self.id)],
            'context': {'default_partner_id': self.id},
        }

    def action_create_sales_opportunity(self):
        """Create a new opportunity for this partner and open it."""
        self.ensure_one()
        Lead = self.env['crm.lead']

        vals = {
            'type': 'opportunity',
            'partner_id': self.id,
            'name': f'Opportunity - {self.name}',
            'email_from': self.email,
            'phone': self.phone or self.mobile,
            'user_id': self.env.user.id,
        }

        opportunity = Lead.create(vals)

        # Force recompute of partner_opportunity_ids for this partner
        self._compute_partner_opportunity_ids()

        return {
            'type': 'ir.actions.act_window',
            'name': 'Sales Opportunity',
            'res_model': 'crm.lead',
            'view_mode': 'form',
            'res_id': opportunity.id,
            'target': 'current',
        }


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    @api.model_create_multi
    def create(self, vals_list):
        """Override create to invalidate partner cache when opportunity is created."""
        leads = super().create(vals_list)
        partners = self.env['res.partner'].browse()
        for lead in leads:
            if lead.type == 'opportunity' and lead.partner_id:
                partners |= lead.partner_id
        if partners:
            partners._compute_partner_opportunity_ids()
        return leads

    def write(self, vals):
        """Override write to invalidate partner cache when partner_id or type changes."""
        old_partners = self.env['res.partner'].browse()
        if 'partner_id' in vals or 'type' in vals:
            for lead in self:
                if lead.partner_id:
                    old_partners |= lead.partner_id

        res = super().write(vals)

        new_partners = self.env['res.partner'].browse()
        if 'partner_id' in vals or 'type' in vals:
            for lead in self:
                if lead.type == 'opportunity' and lead.partner_id:
                    new_partners |= lead.partner_id

        all_partners = old_partners | new_partners
        if all_partners:
            all_partners._compute_partner_opportunity_ids()

        return res

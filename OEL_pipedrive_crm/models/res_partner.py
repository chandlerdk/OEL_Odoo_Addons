# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    org_status = fields.Selection([
        ('prospect', 'Prospect'),
        ('customer', 'Customer'),
    ], string="Status", default='prospect', tracking=True)

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

    @api.depends('name', 'parent_id', 'parent_id.name')
    def _compute_display_names(self):
        for rec in self:
            if rec.parent_id:
                rec.display_title_name = rec.parent_id.name or rec.name or ''
                rec.display_contact_name = rec.name or ''
            else:
                rec.display_title_name = rec.name or ''
                rec.display_contact_name = ''

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
        # Collect all partners that need recompute
        partners = self.env['res.partner'].browse()
        for lead in leads:
            if lead.type == 'opportunity' and lead.partner_id:
                partners |= lead.partner_id
        # Trigger recompute
        if partners:
            partners._compute_partner_opportunity_ids()
        return leads

    def write(self, vals):
        """Override write to invalidate partner cache when partner_id or type changes."""
        old_partners = self.env['res.partner'].browse()
        if 'partner_id' in vals or 'type' in vals:
            # Collect old partners
            for lead in self:
                if lead.partner_id:
                    old_partners |= lead.partner_id
        
        res = super().write(vals)
        
        # Collect new partners
        new_partners = self.env['res.partner'].browse()
        if 'partner_id' in vals or 'type' in vals:
            for lead in self:
                if lead.type == 'opportunity' and lead.partner_id:
                    new_partners |= lead.partner_id
        
        # Trigger recompute for both old and new partners
        all_partners = old_partners | new_partners
        if all_partners:
            all_partners._compute_partner_opportunity_ids()
        
        return res

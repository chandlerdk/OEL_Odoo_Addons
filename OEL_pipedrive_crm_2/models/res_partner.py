# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # Optional status flag; safe to keep even if not used heavily
    org_status = fields.Selection([
        ('prospect', 'Prospect'),
        ('customer', 'Customer'),
    ], string="Status", default='prospect', tracking=True)

    # Helper: is a customer if has at least one confirmed order
    has_confirmed_order = fields.Boolean(
        string="Has Confirmed Order",
        compute='_compute_has_confirmed_order',
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

    # Display helpers for conditional view logic
    display_title_name = fields.Char(compute='_compute_display_names', store=False)
    display_contact_name = fields.Char(compute='_compute_display_names', store=False)

    # -------------------------------------------------------------------------
    # COMPUTES
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

    @api.depends('sale_order_ids.state', 'child_ids.sale_order_ids.state')
    def _compute_has_confirmed_order(self):
        """True if partner or its children have at least one order in sale/done."""
        SaleOrder = self.env['sale.order']
        for partner in self:
            partners = partner | partner.child_ids
            count = SaleOrder.search_count([
                ('partner_id', 'in', partners.ids),
                ('state', 'in', ['sale', 'done']),
            ])
            partner.has_confirmed_order = bool(count)

    @api.depends('sale_order_ids.state', 'child_ids.sale_order_ids.state')
    def _compute_quote_count(self):
        SaleOrder = self.env['sale.order']
        for partner in self:
            partners = partner | partner.child_ids
            partner.quote_count = SaleOrder.search_count([
                ('partner_id', 'in', partners.ids),
                ('state', 'in', ['draft', 'sent'])
            ])

    @api.depends('sale_order_ids.state', 'child_ids.sale_order_ids.state')
    def _compute_active_quote_count(self):
        SaleOrder = self.env['sale.order']
        for partner in self:
            partners = partner | partner.child_ids
            partner.active_quote_count = SaleOrder.search_count([
                ('partner_id', 'in', partners.ids),
                ('state', 'in', ['draft', 'sent'])
            ])

    @api.depends('sale_order_ids.state', 'child_ids.sale_order_ids.state')
    def _compute_cancelled_quote_count(self):
        SaleOrder = self.env['sale.order']
        for partner in self:
            partners = partner | partner.child_ids
            partner.cancelled_quote_count = SaleOrder.search_count([
                ('partner_id', 'in', partners.ids),
                ('state', '=', 'cancel')
            ])

    @api.depends('sale_order_ids.state', 'child_ids.sale_order_ids.state')
    def _compute_order_count(self):
        SaleOrder = self.env['sale.order']
        for partner in self:
            partners = partner | partner.child_ids
            partner.order_count = SaleOrder.search_count([
                ('partner_id', 'in', partners.ids),
                ('state', 'in', ['sale', 'done'])
            ])

    @api.depends('child_ids', 'child_ids.opportunity_count')
    def _compute_opportunity_count(self):
        CrmLead = self.env['crm.lead'] if 'crm.lead' in self.env else False
        for partner in self:
            partners = partner | partner.child_ids
            if CrmLead:
                partner.opportunity_count = CrmLead.search_count([
                    ('partner_id', 'in', partners.ids),
                    ('type', '=', 'opportunity')
                ])
            else:
                partner.opportunity_count = 0

    @api.depends('child_ids', 'child_ids.invoice_count')
    def _compute_invoice_count(self):
        AccountMove = self.env['account.move'] if 'account.move' in self.env else False
        for partner in self:
            partners = partner | partner.child_ids
            if AccountMove:
                partner.invoice_count = AccountMove.search_count([
                    ('partner_id', 'in', partners.ids),
                    ('move_type', '=', 'out_invoice')
                ])
            else:
                partner.invoice_count = 0

    @api.depends('sale_order_ids.state', 'child_ids.sale_order_ids.state')
    def _compute_delivery_count(self):
        SaleOrder = self.env['sale.order']
        StockPicking = self.env['stock.picking'] if 'stock.picking' in self.env else False
        for partner in self:
            partners = partner | partner.child_ids
            sale_orders = SaleOrder.search([
                ('partner_id', 'in', partners.ids),
                ('state', 'in', ['sale', 'done'])
            ])
            procurement_group_ids = sale_orders.mapped('procurement_group_id').ids
            if StockPicking and procurement_group_ids:
                partner.delivery_count = StockPicking.search_count([
                    ('group_id', 'in', procurement_group_ids),
                    ('picking_type_code', '=', 'outgoing')
                ])
            else:
                partner.delivery_count = 0

    @api.depends('child_ids', 'child_ids.document_count')
    def _compute_document_count(self):
        Attachment = self.env['ir.attachment']
        for partner in self:
            partners = partner | partner.child_ids
            partner.document_count = Attachment.search_count([
                ('res_model', '=', 'res.partner'),
                ('res_id', 'in', partners.ids)
            ])

    @api.depends('child_ids', 'child_ids.task_count')
    def _compute_task_count(self):
        Task = self.env['project.task'] if 'project.task' in self.env else False
        for partner in self:
            partners = partner | partner.child_ids
            if Task:
                partner.task_count = Task.search_count([
                    ('partner_id', 'in', partners.ids)
                ])
            else:
                partner.task_count = 0

    # -------------------------------------------------------------------------
    # ACTIONS (smart buttons etc.)
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
        SaleOrder = self.env['sale.order']
        StockPicking = self.env['stock.picking'] if 'stock.picking' in self.env else False
        sale_orders = SaleOrder.search([
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

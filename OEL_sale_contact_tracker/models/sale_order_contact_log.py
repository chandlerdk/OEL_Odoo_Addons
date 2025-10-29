# -*- coding: utf-8 -*-
from odoo import api, fields, models


class SaleOrderContactLog(models.Model):
    _name = 'sale.order.contact.log'
    _description = 'Sales Order Touched Contacts'
    _order = 'date_touched desc'
    _rec_name = 'display_name'

    sale_order_id = fields.Many2one(
        'sale.order',
        string='Sales Order',
        required=True,
        ondelete='cascade'
    )

    partner_id = fields.Many2one(
        'res.partner',
        string='Contact'
    )

    email = fields.Char(
        string='Email Address',
        help='Email address for contacts not in the system'
    )

    source = fields.Selection([
        ('email_sent', 'Email Sent'),
        ('email_received', 'Email Received'),
        ('manual', 'Manually Added'),
        ('quote_sent', 'Quote Sent'),
        ('order_confirmed', 'Order Confirmed'),
        ('order_cancelled', 'Order Cancelled'),
    ], string='Source', required=True, default='manual')

    date_touched = fields.Datetime(
        string='Date Touched',
        default=fields.Datetime.now,
        required=True
    )

    notes = fields.Text(string='Notes')

    display_name = fields.Char(
        string='Display Name',
        compute='_compute_display_name',
        store=True
    )

    @api.depends('partner_id', 'email')
    def _compute_display_name(self):
        for record in self:
            if record.partner_id:
                record.display_name = record.partner_id.name
            elif record.email:
                record.display_name = record.email
            else:
                record.display_name = 'Unknown Contact'

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        """Auto-populate email when a contact is selected."""
        if self.partner_id and self.partner_id.email:
            self.email = self.partner_id.email

    @api.model
    def log_contact(self, sale_order_id, partner_id=None, email=None, source='manual', notes=None):
        """Helper method to log a contact interaction with deduplication"""
        domain = [
            ('sale_order_id', '=', sale_order_id),
            ('source', '=', source),
        ]

        if partner_id:
            domain.append(('partner_id', '=', partner_id))
        elif email:
            domain.append(('email', '=', email))

        existing = self.search(domain, limit=1)
        if existing:
            # Update existing record
            existing.write({
                'date_touched': fields.Datetime.now(),
                'notes': notes or existing.notes
            })
            return existing
        else:
            # Create new record
            return self.create({
                'sale_order_id': sale_order_id,
                'partner_id': partner_id,
                'email': email,
                'source': source,
                'notes': notes,
            })

# -*- coding: utf-8 -*-
from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    contact_log_ids = fields.One2many(
        'sale.order.contact.log',
        'sale_order_id',
        string='Touched Contacts'
    )

    contact_log_count = fields.Integer(
        string='Touched Contacts Count',
        compute='_compute_contact_log_count'
    )

    @api.depends('contact_log_ids')
    def _compute_contact_log_count(self):
        for record in self:
            record.contact_log_count = len(record.contact_log_ids)

    def _log_partner(self, source, notes):
        """Convenience method to log the main partner with a given source."""
        ContactLog = self.env['sale.order.contact.log']
        for order in self:
            partner = order.partner_id
            if not partner:
                continue
            ContactLog.log_contact(
                sale_order_id=order.id,
                partner_id=partner.id,
                email=partner.email,
                source=source,
                notes=notes,
            )

    def action_confirm(self):
        """Override to log customer contact when order is confirmed."""
        result = super().action_confirm()
        self._log_partner(source='order_confirmed', notes='Order confirmed')
        return result

    def action_quotation_sent(self):
        """Override to log when quotation is sent."""
        result = super().action_quotation_sent()
        self._log_partner(source='quote_sent', notes='Quotation sent')
        return result

    def action_cancel(self):
        """Override to log when order is cancelled."""
        result = super().action_cancel()
        self._log_partner(source='order_cancelled', notes='Order cancelled')
        return result

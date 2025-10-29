# -*- coding: utf-8 -*-
from odoo import models, fields as odoo_fields, api

class MailMessage(models.Model):
    _inherit = 'mail.message'

    is_pinned = odoo_fields.Boolean(string='Pinned', default=False, index=True)

    @api.model
    def message_fetch(self, domain, field_names, limit=None, order='id desc'):
        """Override to always include pinned status."""
        # field_names is a list of field names to fetch on each message
        if field_names is not None and 'is_pinned' not in field_names:
            field_names.append('is_pinned')
        return super().message_fetch(domain, field_names, limit=limit, order=order)

    def toggle_pin(self):
        """Toggle the pinned status of messages and notify bus."""
        for message in self:
            message.is_pinned = not message.is_pinned
            # Notify frontend (make sure your JS subscribes to this channel/event)
            self.env['bus.bus']._sendone(
                f'{self._name},{message.id}',
                'mail.message/pin_changed',
                {
                    'id': message.id,
                    'is_pinned': message.is_pinned,
                }
            )
        return True

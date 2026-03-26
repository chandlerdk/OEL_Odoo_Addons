# -*- coding: utf-8 -*-

from odoo import models
import logging

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _message_auto_subscribe_notify(self, partner_ids, template):
        """
        Override to prevent automatic email notifications when users 
        are assigned to draft invoices.
        
        By returning True for draft invoices, we skip the notification step
        while keeping the assignment functionality intact.
        """
        # Skip notification for draft invoices
        if self.state == 'draft':
            _logger.info(
                'Skipping assignment notification for draft invoice %s (ID: %s)',
                self.name or 'New',
                self.id
            )
            return True
        
        # For non-draft invoices, use the standard behavior
        return super(AccountMove, self)._message_auto_subscribe_notify(
            partner_ids, 
            template
        )

# -*- coding: utf-8 -*-
from odoo import api, fields, models
import logging

_logger = logging.getLogger(__name__)


class MailMessage(models.Model):
    _inherit = 'mail.message'

    @api.model_create_multi
    def create(self, vals_list):
        """Hook to capture incoming emails and log them to oel.mail.history"""
        records = super().create(vals_list)
        
        for vals in vals_list:
            message_id = vals.get('message_id')
            in_reply_to = vals.get('in_reply_to')
            references = vals.get('references', '')
            email_from = vals.get('email_from')
            email_to = vals.get('email_to')
            subject = vals.get('subject') or ''
            body = vals.get('body', '')
            
            # 🔍 DEBUG: Log every incoming message
            _logger.info(f"🔍 Incoming mail.message created:")
            _logger.info(f"   Message-ID: {message_id}")
            _logger.info(f"   In-Reply-To: {in_reply_to}")
            _logger.info(f"   References: {references}")
            _logger.info(f"   From: {email_from}")
            _logger.info(f"   To: {email_to}")
            _logger.info(f"   Subject: {subject}")
            _logger.info(f"   Model: {vals.get('model')}, Res ID: {vals.get('res_id')}")
            
            # Skip if this is an internal Odoo message (has model/res_id)
            if vals.get('model') and vals.get('res_id'):
                _logger.info(f"   ⏭️ Skipping - internal message")
                continue

            # Try to match this as a reply to an outbound message
            if in_reply_to or references:
                # Search for matching outbound message
                match = self.env['oel.mail.history'].search([
                    '|',
                    ('mail_message_id', '=', in_reply_to),
                    ('mail_message_id', 'in', references.split())
                ], limit=1, order='date desc')

                if match:
                    # Found matching conversation - log the reply
                    partner = self.env['res.partner'].search([
                        ('email', '=ilike', email_from)
                    ], limit=1)

                    # Use the partner from the match if we can't find one by email
                    if not partner:
                        partner = match.partner_id

                    _logger.info(f"💬 Inbound reply matched: {message_id}, partner={partner.name if partner else 'unknown'}")

                    self.env['oel.mail.history'].sudo().create({
                        'partner_id': partner.id if partner else match.partner_id.id,
                        'direction': 'in',
                        'subject': subject,
                        'email_from': email_from,
                        'email_to': email_to,
                        'date': fields.Datetime.now(),
                        'body_html': body,
                        'state': 'received',
                        'mail_message_id': message_id,
                        'in_reply_to': in_reply_to,
                        'references': references,
                    })
                else:
                    _logger.info(f"⚠️ No match found for inbound email {message_id} (In-Reply-To: {in_reply_to})")
                    _logger.info(f"   Searched for mail_message_id matching: {in_reply_to} or in {references}")
            else:
                _logger.info(f"⚠️ No threading headers - not a reply")
            
        return records

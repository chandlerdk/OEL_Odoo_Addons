# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class OelMailComposeWizard(models.TransientModel):
    _name = 'oel.mail.compose.wizard'
    _description = 'Email Compose Wizard'

    partner_id = fields.Many2one(
        'res.partner', 
        string='Recipient', 
        required=True, 
        readonly=True
    )
    email_to = fields.Char(string='To', required=True)
    subject = fields.Char(string='Subject', required=True)
    body_html = fields.Html(string='Message', required=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        partner_id = self.env.context.get('default_partner_id')
        if partner_id:
            partner = self.env['res.partner'].browse(partner_id)
            res['partner_id'] = partner.id
            res['email_to'] = partner.email or ''
        return res

    def action_send_email(self):
        """Send email via user's SMTP and log to mail history with threading support"""
        self.ensure_one()
        
        if not self.email_to:
            raise UserError("Recipient email is required.")
        
        # Get current user's email
        user_email = self.env.user.email_formatted or self.env.user.email
        if not user_email:
            raise UserError("Your user account does not have an email configured.")

        # Create mail.mail record to send via Odoo's mail system
        mail_values = {
            'subject': self.subject,
            'body_html': self.body_html,
            'email_from': user_email,
            'email_to': self.email_to,
            'auto_delete': False,
        }
        
        mail = self.env['mail.mail'].sudo().create(mail_values)
        
        try:
            # Send the email (uses user-specific outgoing mail server if configured)
            mail.send()
            state = 'sent'
            _logger.info(f"✅ Email sent successfully to {self.email_to}, Message-ID: {mail.message_id}")
        except Exception as e:
            _logger.error(f"❌ Failed to send email to {self.email_to}: {str(e)}")
            state = 'failed'
            raise UserError(f"Failed to send email: {str(e)}")

        # Log to custom mail history with threading support
        mail_history_vals = {
            'partner_id': self.partner_id.id,
            'direction': 'out',
            'subject': self.subject,
            'email_from': user_email,
            'email_to': self.email_to,
            'date': fields.Datetime.now(),
            'body_html': self.body_html,
            'state': state,
            'mail_message_id': mail.message_id if mail.message_id else False,
            'references': mail.message_id if mail.message_id else False,
        }
        
        history_record = self.env['oel.mail.history'].create(mail_history_vals)
        _logger.info(f"📝 Mail history logged: ID={history_record.id}, Message-ID={mail.message_id}")

        return {'type': 'ir.actions.act_window_close'}

from odoo import models, api, fields
import logging

_logger = logging.getLogger(__name__)

class CrmLead(models.Model):
    _inherit = 'crm.lead'

    @api.model
    def create(self, vals_list):
        """Override create to handle assignment + notifications, but skip during import dry-runs."""
        if not isinstance(vals_list, list):
            vals_list = [vals_list]

        # Check if this is a dry-run/validation pass and skip notifications
        if self._is_import_dryrun():
            _logger.info("🔄 Import dry-run detected - skipping notifications")
            return super(CrmLead, self).create(vals_list)

        # Normal creation with notifications
        leads = super(CrmLead, self.with_context(
            mail_create_nolog=True,
            mail_create_nosubscribe=True
        )).create(vals_list)

        for lead in leads:
            try:
                _logger.info(f"Processing notifications for new lead: {lead.name}")
                
                if lead.user_id:
                    partner = lead.user_id.partner_id
                    if partner and partner.id not in lead.message_partner_ids.ids:
                        lead.with_context(mail_notify_noemail=True).message_subscribe([partner.id])
                        _logger.info(f"✅ Subscribed {partner.name} to lead {lead.name}")
                    
                    self._send_salesperson_notification(lead)

                if lead.email_from:
                    self._send_welcome_email(lead)

            except Exception as e:
                _logger.error(f"❌ Error in lead notification for {lead.name}: {str(e)}")

        return leads

    def write(self, vals):
        """Catch reassignment and notify, but skip during dry-runs."""
        if self._is_import_dryrun():
            _logger.info("🔄 Import dry-run detected - skipping write notifications")
            return super(CrmLead, self).write(vals)

        old_users = {lead.id: lead.user_id.id if lead.user_id else False for lead in self}
        result = super(CrmLead, self.with_context(mail_auto_subscribe_no_notify=True)).write(vals)

        if 'user_id' in vals:
            for lead in self:
                old_user_id = old_users.get(lead.id, False)
                if lead.user_id and lead.user_id.id != old_user_id:
                    partner = lead.user_id.partner_id
                    if partner and partner.id not in lead.message_partner_ids.ids:
                        lead.with_context(mail_notify_noemail=True).message_subscribe([partner.id])
                        _logger.info(f"✅ Subscribed {partner.name} to lead {lead.name} (reassignment)")
                    self._send_salesperson_notification(lead)

        return result

    def _is_import_dryrun(self):
        """Detect if we're in an import dry-run/validation phase."""
        context = self.env.context
        
        # Check for our custom dry-run flag from base_import override
        if context.get('import_dryrun'):
            _logger.debug("Detected import_dryrun=True")
            return True
            
        return False

    def _send_salesperson_notification(self, lead):
        """Send notification email to assigned salesperson."""
        try:
            if not lead.user_id or not lead.user_id.email:
                _logger.warning(f"Lead {lead.name}: No user assigned or user has no email")
                return False

            # Duplicate guard
            already = self.env['mail.mail'].sudo().search([
                ('model', '=', 'crm.lead'),
                ('res_id', '=', lead.id),
                ('email_to', '=', lead.user_id.email),
                ('state', 'in', ['outgoing', 'sent']),
            ], limit=1)
            if already:
                _logger.info(f"Skipping duplicate salesperson notification for {lead.name}")
                return False

            template = self.env.ref('OEL_lead_assignment.mail_template_notify_salesperson', raise_if_not_found=False)
            if template:
                template.with_context(force_send=True).send_mail(
                    lead.id,
                    force_send=True,
                    email_values={
                        'email_to': lead.user_id.email,
                        'recipient_ids': [],
                        'auto_delete': True,
                    }
                )
                _logger.info(f"✅ Salesperson email sent to {lead.user_id.email} for lead {lead.name}")
                return True
            else:
                _logger.warning("Salesperson email template not found")
                lead.message_post(
                    body=f"Salesperson notification failed (template missing). Lead: {lead.name}",
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                )
                return False
                
        except Exception as e:
            _logger.error(f"❌ Salesperson notification failed: {str(e)}")
            return False

    def _send_welcome_email(self, lead):
        """Send welcome email to the lead."""
        try:
            if not lead.email_from:
                _logger.debug(f"Lead {lead.name} has no email_from - skipping welcome email")
                return False

            # Duplicate guard
            already = self.env['mail.mail'].sudo().search([
                ('model', '=', 'crm.lead'),
                ('res_id', '=', lead.id),
                ('email_to', '=', lead.email_from),
                ('state', 'in', ['outgoing', 'sent']),
            ], limit=1)
            if already:
                _logger.info(f"Skipping duplicate welcome email for {lead.name}")
                return False

            template = self.env.ref('OEL_lead_assignment.mail_template_welcome_lead', raise_if_not_found=False)
            if template:
                template.with_context(force_send=True).send_mail(
                    lead.id,
                    force_send=True,
                    email_values={
                        'email_to': lead.email_from,
                        'recipient_ids': [],
                        'auto_delete': True,
                    }
                )
                _logger.info(f"✅ Welcome email sent to {lead.email_from} for lead {lead.name}")
                return True
            else:
                _logger.warning("Welcome email template not found")
                lead.message_post(
                    body=f"Welcome email failed (template missing). Lead: {lead.name}",
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                )
                return False
                
        except Exception as e:
            _logger.error(f"❌ Welcome email failed: {str(e)}")
            return False

    def manual_send_notifications(self):
        """Manual method to force send notifications - useful for testing."""
        _logger.info(f"Manual notification trigger for lead: {self.name}")
        
        if self.user_id:
            self._send_salesperson_notification(self)
        
        if self.email_from:
            self._send_welcome_email(self)
        
        return True

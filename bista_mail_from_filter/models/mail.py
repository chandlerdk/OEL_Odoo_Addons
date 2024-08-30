from odoo import models, _
from odoo.addons.base.models.ir_mail_server import MailDeliveryException
import logging
from odoo import tools
import psycopg2
import smtplib

_logger = logging.getLogger(__name__)


class Mail(models.Model):
    _inherit = "mail.mail"

    def send(self, auto_commit=False, raise_exception=False):
        """
            @Overwrite
            the sender email and sender name defined in the outgoing mail server.
        """
        for mail_server_id, alias_domain_id, smtp_from, batch_ids in self._split_by_mail_configuration():
            smtp_session = None
            self = self.with_context(mail_server_id=mail_server_id)
            try:
                server_id = self.env["ir.mail_server"].browse(mail_server_id)
                email_from = server_id.email_from
                if email_from:
                    if server_id.from_name_type:
                        from_name = self._get_sender_name(server_id, True)
                        email_from = self._get_formatted_address(from_name, email_from)
                    smtp_from = email_from

                smtp_session = self.env['ir.mail_server'].connect(mail_server_id=mail_server_id, smtp_from=smtp_from)
            except Exception as exc:
                if raise_exception:
                    # To be consistent and backward compatible with mail_mail.send() raised
                    # exceptions, it is encapsulated into an Odoo MailDeliveryException
                    raise MailDeliveryException(_('Unable to connect to SMTP Server'), exc)
                else:
                    batch = self.browse(batch_ids)
                    batch.write({'state': 'exception', 'failure_reason': exc})
                    batch._postprocess_sent_message(success_pids=[], failure_type="mail_smtp")
            else:
                self.browse(batch_ids)._send(
                    auto_commit=auto_commit,
                    raise_exception=raise_exception,
                    smtp_session=smtp_session,
                    alias_domain_id=alias_domain_id,
                )
                _logger.info(
                    'Sent batch %s emails via mail server ID #%s',
                    len(batch_ids), mail_server_id)
            finally:
                if smtp_session:
                    smtp_session.quit()

    def _get_formatted_address(self, sender_name, sender_email):
        return f'"{sender_name}" <{sender_email}>'

    def _get_sender_name(self, server, is_from_address):
        sender_name_type = server.from_name_type if is_from_address else server.reply_name_type
        sender_name = server.from_name if is_from_address else server.reply_name
        return sender_name if sender_name_type == 'static' else self.env.user.display_name

    def _prepare_outgoing_list(self, recipients_follower_status=None):
        """
            @Overwrite
            The reply to because it is constructed outside the send method
            Since we are updating the reply to it is nice to update the email from as well
            so odoo email_from matches the outgoing email from
            The email_from,reply_from and email_name,reply_name is defined in the outgoing mail server
        """
        results = super()._prepare_outgoing_list(recipients_follower_status)
        server_id = self.mail_server_id
        mail_server_id = self.env.context.get('mail_server_id')
        if not server_id and mail_server_id:
            server_id = self.env['ir.mail_server'].browse(mail_server_id)
        if not server_id:
            return results
        email_from = server_id.email_from
        reply_to = server_id.email_reply
        for result in results:
            if email_from:
                if server_id.from_name_type:
                    from_name = self._get_sender_name(server_id, True)
                    email_from = self._get_formatted_address(from_name, email_from)
                result['email_from'] = email_from
            if reply_to:
                if server_id.reply_name_type:
                    reply_name = self._get_sender_name(server_id, False)
                    reply_to = self._get_formatted_address(reply_name, reply_to)
                result['reply_to'] = reply_to
        return results

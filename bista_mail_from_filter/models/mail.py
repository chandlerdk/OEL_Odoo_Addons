from odoo import models


class Mail(models.Model):
    _inherit = "mail.mail"

    def _split_by_mail_configuration(self):
        """
            Overwrite the sender email and sender name with the ones defined
            on the outgoing mail server. The SMTP session "smtp_from" replaces
            the From header when the message is sent.
        """
        for mail_server_id, alias_domain_id, smtp_from, batch_ids in super()._split_by_mail_configuration():
            server = self.env["ir.mail_server"].sudo().browse(mail_server_id)
            if server.email_from:
                smtp_from = self._get_server_email_from(server)
            yield mail_server_id, alias_domain_id, smtp_from, batch_ids

    def _get_formatted_address(self, sender_name, sender_email):
        return f'"{sender_name}" <{sender_email}>'

    def _get_sender_name(self, server, is_from_address):
        sender_name_type = server.from_name_type if is_from_address else server.reply_name_type
        sender_name = server.from_name if is_from_address else server.reply_name
        return sender_name if sender_name_type == 'static' else self.env.user.display_name

    def _get_server_email_from(self, server):
        email_from = server.email_from
        if server.from_name_type:
            email_from = self._get_formatted_address(self._get_sender_name(server, True), email_from)
        return email_from

    def _get_server_reply_to(self, server):
        reply_to = server.email_reply
        if server.reply_name_type:
            reply_to = self._get_formatted_address(self._get_sender_name(server, False), reply_to)
        return reply_to

    def _prepare_outgoing_list(self, mail_server=False, doc_to_followers=None):
        """
            Overwrite the reply to (and the email from, so it matches the
            outgoing From header) with the values defined on the outgoing mail server.
        """
        results = super()._prepare_outgoing_list(mail_server=mail_server, doc_to_followers=doc_to_followers)
        server = (mail_server or self.mail_server_id).sudo()
        if not server:
            return results
        email_from = self._get_server_email_from(server) if server.email_from else False
        reply_to = self._get_server_reply_to(server) if server.email_reply else False
        for result in results:
            if email_from:
                result['email_from'] = email_from
            if reply_to:
                result['reply_to'] = reply_to
        return results

# -*- coding: utf-8 -*-
from odoo import models, fields


class MailComposeMessage(models.TransientModel):
    _inherit = 'mail.compose.message'

    def _action_send_mail(self, auto_commit=False):
        result = super()._action_send_mail(auto_commit=auto_commit)

        ContactLog = self.env['sale.order.contact.log']

        for wizard in self:
            if wizard.model != 'sale.order' or not wizard.res_ids:
                continue

            # Normalize res_ids to a list of integers
            if isinstance(wizard.res_ids, (list, tuple)):
                order_ids = [int(x) for x in wizard.res_ids]
            elif isinstance(wizard.res_ids, int):
                order_ids = [wizard.res_ids]
            else:
                # Handle strings like "[1]" or "1"
                s = str(wizard.res_ids).strip().strip('[]() ')
                order_ids = [int(p) for p in s.split(',') if p.strip().isdigit()]

            if not order_ids:
                continue

            sale_orders = self.env['sale.order'].browse(order_ids).exists()
            if not sale_orders:
                continue

            subject = wizard.subject or 'No subject'
            for order in sale_orders:
                # Log all recipients in partner_ids
                for partner in wizard.partner_ids:
                    ContactLog.log_contact(
                        sale_order_id=order.id,
                        partner_id=partner.id,
                        email=partner.email,
                        source='email_sent',
                        notes=f'Email sent: {subject}',
                    )

                # Also log any extra free-form email addresses if the field exists
                if hasattr(wizard, 'email_to') and wizard.email_to:
                    extra_emails_raw = wizard.email_to
                    for raw in extra_emails_raw.replace(';', ',').split(','):
                        email = (raw or '').strip()
                        if email:
                            ContactLog.log_contact(
                                sale_order_id=order.id,
                                partner_id=None,
                                email=email,
                                source='email_sent',
                                notes=f'Email sent: {subject}',
                            )

        return result

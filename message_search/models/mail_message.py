from markupsafe import Markup, escape

from odoo import models, fields, api


class MailMessage(models.Model):
    _inherit = 'mail.message'

    record_link = fields.Html('Related Record', compute='_prepare_related_record_link', store=True)

    @api.depends('res_id', 'model')
    def _prepare_related_record_link(self):
        for rec in self:
            if not rec.res_id or not rec.model or rec.model not in self.env:
                rec.record_link = False
                continue
            record = self.env[rec.model].sudo().browse(rec.res_id).exists()
            if not record:
                rec.record_link = False
                continue
            rec.record_link = Markup(
                "<strong><a href='/odoo/%s/%s' target='_blank'>%s</a></strong>"
            ) % (rec.model, rec.res_id, escape(record.display_name or ''))

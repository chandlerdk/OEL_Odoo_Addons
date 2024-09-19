from odoo import models, fields, api, _


class MailMessage(models.Model):
    _inherit = 'mail.message'

    record_link = fields.Html('Related Record', compute='_prepare_related_record_link', store=True)

    @api.depends('res_id', 'model')
    def _prepare_related_record_link(self):
        for rec in self:
            if not rec.res_id or not rec.model:
                rec.record_link = False
                continue
            record_id = self.env[rec.model].sudo().browse(rec.res_id)
            rec.record_link = f"<strong><a href='/web#id={rec.res_id}&model={rec.model}' target='_blank' aria-describedby='tooltip904260'>{record_id.display_name} </a><strong>"

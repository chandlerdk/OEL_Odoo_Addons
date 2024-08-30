from odoo import models, fields


class MailServer(models.Model):
    _inherit = "ir.mail_server"

    email_from = fields.Char(help="Sender email address will be replaced with this email address")
    from_name_type = fields.Selection([('user', 'User'), ('static', 'Static ')])
    from_name = fields.Char()
    email_reply = fields.Char()
    reply_name_type = fields.Selection([('user', 'User'), ('static', 'Static ')])
    reply_name = fields.Char()


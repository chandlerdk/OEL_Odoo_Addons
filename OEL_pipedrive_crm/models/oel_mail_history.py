# -*- coding: utf-8 -*-
from odoo import fields, models


class OelMailHistory(models.Model):
    _name = 'oel.mail.history'
    _description = 'CRM Mail History'
    _order = 'date desc'

    partner_id = fields.Many2one(
        'res.partner', 
        string='Related Contact', 
        ondelete='cascade', 
        index=True, 
        required=True
    )
    direction = fields.Selection([
        ('out', 'Outgoing'),
        ('in', 'Incoming'),
    ], string='Direction', required=True)
    subject = fields.Char(string='Subject')
    email_from = fields.Char(string='From')
    email_to = fields.Char(string='To')
    date = fields.Datetime(string='Date', default=fields.Datetime.now, index=True)
    body_html = fields.Html(string='Body')
    state = fields.Selection([
        ('sent', 'Sent'),
        ('received', 'Received'),
        ('failed', 'Failed'),
    ], string='Status', default='sent')
    
    # Threading fields
    mail_message_id = fields.Char(string='Message ID', index=True)
    in_reply_to = fields.Char(string='In-Reply-To', index=True)
    references = fields.Char(string='References')
    external_thread_id = fields.Char(string='External Thread ID')

from odoo import api, fields, models
from ast import literal_eval


class AccountMoveSend(models.TransientModel):
    _inherit = 'account.move.send'

    enable_cc = fields.Boolean(string='Enable CC')
    enable_bcc = fields.Boolean(string='Enable BCC')
    cc_recipient_ids = fields.Many2many('res.partner', 'cc_receipt_rel_move','partner_id',
                                        'cc_receipt', string="CC")
    bcc_recipient_ids = fields.Many2many('res.partner','bcc_receipt_rel_move','partner_id',
                                         'bcc_receipt', string="BCC")

    @api.model
    def default_get(self, fields):
        res = super(AccountMoveSend, self).default_get(fields)
        cc_check = self.env['ir.config_parameter'].sudo().get_param('mail_compose_message.enable_cc')
        bcc_check = self.env['ir.config_parameter'].sudo().get_param('mail_compose_message.enable_bcc')
        res.update({
            'enable_cc': cc_check,
            'enable_bcc': bcc_check,
        })
        if cc_check:
            vals = self.env['ir.config_parameter'].sudo().get_param('mail_compose_message.cc_recipient_ids')
            partner_ids = literal_eval(vals)
            res.update({'cc_recipient_ids': [(4, pid) for pid in partner_ids]})

        if bcc_check:
            vals = self.env['ir.config_parameter'].sudo().get_param('mail_compose_message.bcc_recipient_ids')
            partner_ids = literal_eval(vals)
            res.update({'bcc_recipient_ids': [(4, pid) for pid in partner_ids]})
        return res

    # @api.model
    # def _send_mail(self, move, mail_template, **kwargs):
    #     res = super(AccountMoveSend, self)._send_mail(move, mail_template, **kwargs)
    #     if self.enable_cc:
    #         cc_recipient_ids = self.cc_recipient_ids.ids
    #         res.update({'cc_recipient_ids': cc_recipient_ids})
    #     if self.enable_bcc:
    #         bcc_recipient_ids = self.bcc_recipient_ids.ids
    #         res.update({'bcc_recipient_ids': bcc_recipient_ids})
    #     return res

    @api.model
    def _send_mail(self, move, mail_template, **kwargs):
        if self.enable_cc:
            cc_recipient_ids = self.cc_recipient_ids.ids
            kwargs.update({'cc_recipient_ids': cc_recipient_ids})
        if self.enable_bcc:
            bcc_recipient_ids = self.bcc_recipient_ids.ids
            kwargs.update({'bcc_recipient_ids': bcc_recipient_ids})
        res = super(AccountMoveSend, self)._send_mail(move, mail_template, **kwargs)
        return res

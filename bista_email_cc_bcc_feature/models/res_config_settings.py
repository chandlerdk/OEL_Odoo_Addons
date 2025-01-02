# -*- encoding: utf-8 -*-
from odoo import models, fields, api
from ast import literal_eval


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    cc_recipient_ids = fields.Many2many('res.partner', 'cc_field_tag_rel_setting', 'partner_id', 'cc_id',
                                        string="Default Cc")
    bcc_recipient_ids = fields.Many2many('res.partner', 'bcc_field_tag_rel_setting', 'part_id', 'bcc_id',
                                         string="Default Bcc")
    enable_cc = fields.Boolean(string='Enable CC')
    enable_bcc = fields.Boolean(string='Enable BCC')

    def set_values(self):
        res = super(ResConfigSettings, self).set_values()
        self.env['ir.config_parameter'].set_param("mail_compose_message.enable_cc", self.enable_cc)
        self.env['ir.config_parameter'].set_param("mail_compose_message.enable_bcc", self.enable_bcc)
        self.env['ir.config_parameter'].set_param("mail_compose_message.cc_recipient_ids", self.cc_recipient_ids.ids)
        self.env['ir.config_parameter'].set_param("mail_compose_message.bcc_recipient_ids", self.bcc_recipient_ids.ids)
        return res

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        enable_cc = self.env['ir.config_parameter'].sudo().get_param(
            'mail_compose_message.enable_cc') or False
        enable_bcc = self.env['ir.config_parameter'].sudo().get_param(
            'mail_compose_message.enable_bcc') or False
        cc_recipient_ids = self.env['ir.config_parameter'].sudo().get_param(
            'mail_compose_message.cc_recipient_ids') or False
        bcc_recipient_ids = self.env['ir.config_parameter'].sudo().get_param(
            'mail_compose_message.bcc_recipient_ids') or False
        res.update(
            enable_cc=enable_cc,
            enable_bcc=enable_bcc,
        )
        if cc_recipient_ids:
            res.update(
                cc_recipient_ids=[[6, 0, literal_eval(cc_recipient_ids)]],
            )
        if bcc_recipient_ids:
            res.update(
                bcc_recipient_ids=[[6, 0, literal_eval(bcc_recipient_ids)]],
            )
        return res

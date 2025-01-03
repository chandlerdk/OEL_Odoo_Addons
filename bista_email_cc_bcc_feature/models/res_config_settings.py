# -*- encoding: utf-8 -*-
from odoo import models, fields, api
from ast import literal_eval


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    cc_recipient_ids = fields.Many2many(
        "res.partner",
        "cc_field_tag_rel_setting",
        "partner_id",
        "cc_id",
        string="Default Cc",
    )
    bcc_recipient_ids = fields.Many2many(
        "res.partner",
        "bcc_field_tag_rel_setting",
        "part_id",
        "bcc_id",
        string="Default Bcc",
    )
    enable_cc = fields.Boolean(string="Enable CC")
    enable_bcc = fields.Boolean(string="Enable BCC")
    
    @api.onchange('enable_bcc')
    def onchange_enable_bcc(self):
        if not self.enable_bcc:
            self.bcc_recipient_ids = False

    def set_values(self):
        res = super(ResConfigSettings, self).set_values()
        self.env["ir.config_parameter"].set_param(
            "mail_compose_message.enable_cc", self.enable_cc
        )
        self.env["ir.config_parameter"].set_param(
            "mail_compose_message.enable_bcc", self.enable_bcc
        )
        self.env["ir.config_parameter"].set_param(
            "mail_compose_message.cc_recipient_ids", self.cc_recipient_ids.ids
        )
        self.env["ir.config_parameter"].set_param(
            "mail_compose_message.bcc_recipient_ids", self.bcc_recipient_ids.ids
        )
        return res

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        ICP = self.env["ir.config_parameter"].sudo()
        enable_cc = ICP.get_param("mail_compose_message.enable_cc", False)
        enable_bcc = ICP.get_param("mail_compose_message.enable_bcc", False)
        cc_recipient_ids = ICP.get_param("mail_compose_message.cc_recipient_ids", '[]')
        bcc_recipient_ids = ICP.get_param("mail_compose_message.bcc_recipient_ids", '[]')
        res.update(
            enable_cc=enable_cc,
            enable_bcc=enable_bcc,
            cc_recipient_ids=[(6, 0, literal_eval(cc_recipient_ids))],
            bcc_recipient_ids=[(6, 0, literal_eval(bcc_recipient_ids))],
        )
        return res

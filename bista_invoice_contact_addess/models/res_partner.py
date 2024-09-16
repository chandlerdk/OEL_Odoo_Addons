
# -*- coding: utf-8 -*-
###############################################################################
#    License, author and contributors information in:                         #
#    __manifest__.py file at the root folder of this module.                  #
###############################################################################

from odoo import models, fields, api, _,tools
from odoo.exceptions import UserError, ValidationError


class ResPartner(models.Model):
    _inherit = 'res.partner'

    email_all_contact_invoice = fields.Boolean('Send company invoice')

    # def get_invoice_contact(self):
    #     company = self.search([('id', 'parent_of', self.id), ('parent_id', '=', False)], limit=1)
    #     partners = self + self.search([('id', 'child_of', company.id), ('email_all_contact_invoice', '=', True)])
    #     return ','.join(partners.mapped(lambda p: str(p.id)))


class AccountMoveSend(models.TransientModel):
    _inherit = 'account.move.send'

    def _get_default_mail_partner_ids(self, move, mail_template, mail_lang):
        partners = self.env['res.partner'].with_company(move.company_id)
        if mail_template.email_to:
            for mail_data in tools.email_split(mail_template.email_to):
                partners |= partners.find_or_create(mail_data)
        if mail_template.email_cc:
            for mail_data in tools.email_split(mail_template.email_cc):
                partners |= partners.find_or_create(mail_data)
        if mail_template.partner_to:
            partner_to = self._get_mail_default_field_value_from_template(mail_template, mail_lang, move, 'partner_to')
            partner_ids = mail_template._parse_partner_to(partner_to)
            partners |= self.env['res.partner'].sudo().browse(partner_ids).exists()

            company = self.env['res.partner'].search([('id', 'parent_of', partners.id), ('parent_id', '=', False)], limit=1)
            partners = partners + self.env['res.partner'].search([('id', 'child_of', company.id), ('email_all_contact_invoice', '=', True)])

        return partners
# -*- coding: utf-8 -*-
###############################################################################
#    License, author and contributors information in:                         #
#    __manifest__.py file at the root folder of this module.                  #
###############################################################################

from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    email_all_contact_invoice = fields.Boolean('Send company invoice')


class AccountMoveSend(models.AbstractModel):
    _inherit = 'account.move.send'

    @api.model
    def _get_default_mail_partner_ids(self, move, mail_template, mail_lang):
        partners = super()._get_default_mail_partner_ids(move, mail_template, mail_lang)
        recipients = partners or move.partner_id
        if not recipients:
            return partners

        Partner = self.env['res.partner'].with_company(move.company_id)
        company = Partner.search([('id', 'parent_of', recipients.ids), ('parent_id', '=', False)], limit=1)
        if company:
            contacts = Partner.search([('id', 'child_of', company.id), ('email_all_contact_invoice', '=', True)])
            if not self.env.context.get('allow_partners_without_mail'):
                contacts = contacts.filtered('email')
            partners |= contacts
        return partners

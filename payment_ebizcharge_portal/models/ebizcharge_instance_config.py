# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class EbizchargeInstanceConfig(models.Model):
    _inherit = 'ebizcharge.instance.config'

    def _default_is_website_installed(self):
        web = self.env['ir.module.module'].sudo().search([('name', '=', 'website_sale')])
        return True if web.state == "installed" else False

    website_ids = fields.Many2many('website')
    is_website = fields.Boolean()

    is_website_installed = fields.Boolean(default=_default_is_website_installed)

    def _compute_is_website_installed(self):
        web = self.env['ir.module.module'].sudo().search([('name', '=', 'website_sale')])
        for se in self:
            se.is_website_installed = True if web else False

    def _default_website(self):
        return self.env['website'].search([('company_id', '=', self.env.company.id)], limit=1)

    website_id = fields.Many2one('website', string="website",
                                 default=_default_website, ondelete='cascade')

    website = fields.Reference(selection='_select_target_model', string="Select Website")

    @api.model
    def _select_target_model(self):
        models = self.env['ir.model'].search([('model', '=', 'website')])
        return [(model.model, model.name) for model in models]

    def write(self, vals_list):
        rec = super(EbizchargeInstanceConfig, self).write(vals_list)
        if not self.is_website and self.website_ids:
            self.website_ids = False
        if self.is_website and not self.website_ids:
            raise UserError('Please uncheck website button or add a website.')
        return rec


class WebsiteSettings(models.Model):
    _inherit = 'website'

    merchant_data = fields.Boolean(string='Merchant Data')
    merchant_card_verification = fields.Char(string='Merchant Data Verification')
    verify_card_before_saving = fields.Boolean(string='Verify Card Before Saving')
    allow_credit_card_pay = fields.Boolean(string='AllowCreditCardPayments')
    enable_cvv = fields.Boolean(string='EnableCVV')

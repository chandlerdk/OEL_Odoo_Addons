from odoo import fields, models, api
import logging
from ..models.ebiz_charge import message_wizard
from odoo.exceptions import ValidationError, UserError

_logger = logging.getLogger(__name__)


class GeneratePaymentLinkWizard(models.TransientModel):
    _name = 'wizard.ebiz.generate.link.payment.bulk'
    _description = "EBiz Generate Payment Link Bulk"

    payment_lines = fields.One2many('ebiz.generate.payment.link.lines.bulk', 'wizard_id')
    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config', string='EBizCharge Merchant Account')
    invoice_link = fields.Boolean(string='Invoice link')
    sale_link = fields.Boolean(string='Sale link')

    def generate_payment_link(self):
        try:
            wizards = []
            default_ebiz_model = 'account.move'
            if self.sale_link:
                default_ebiz_model = 'sale.order'
            for record in self.payment_lines:
                template_type_id = 'WebFormEmail' if default_ebiz_model == 'account.move' else 'SalesOrderWebFormEmail'
                tem_check = self.env['email.templates'].search([
                    ('template_type_id', '=', template_type_id),
                    ('instance_id', '=', record.customer_name.ebiz_profile_id.id)], limit=1)
                wizard_vals = {
                    'ebiz_profile_id': record.customer_name.ebiz_profile_id.id,
                    'partner_id': record.customer_name.id,
                    'amount': record.amount_residual_signed,
                    'currency_id': record.currency_id.id,
                    'res_model': 'account.move',
                    'link_check_box': True,
                    'select_template': tem_check.id
                }
                wizard = self.env['ebiz.payment.link.wizard'].create(wizard_vals)
                wizards.append(wizard)
                if wizard:

                    wizard.with_context(
                        {'active_model': default_ebiz_model, 'active_id': int(record.invoice_id),
                         'from_bulk': True, 'requested_amount': record.request_amount}).generate_link()
            if self.invoice_link or self.sale_link:
                copy_links = []
                for record in self.payment_lines:
                    doc = self.env[default_ebiz_model].search([('id','=',int(record.invoice_id))], limit=1)
                    if doc:
                        copy_link = {
                            'number': doc.name,
                            'link': doc.save_payment_link,
                        }
                        copy_links.append([0, 0, copy_link])
                # raise UserError(str(copy_links))
                cpyline = {'copy_link_lines': copy_links}
                wiz = self.env['ebiz.payment.link.copy'].create(cpyline)
                action = self.env.ref('payment_ebizcharge_crm.wizard_copy_link_form_views_action').read()[0]
                action['res_id'] = wiz.id
                action['context'] = self.env.context
                return action
            return message_wizard(f'{len(wizards)} payment link(s) generated successfully.')
        except Exception as e:
            raise ValidationError(e)


class GeneratePaymentLinkLines(models.TransientModel):
    _name = 'ebiz.generate.payment.link.lines.bulk'
    _description = "EBiz Payment Lines Bulk"

    wizard_id = fields.Many2one('wizard.ebiz.generate.link.payment.bulk')
    name = fields.Char(string='Number')
    customer_name = fields.Many2one('res.partner', string='Customer')
    odoo_payment_link = fields.Boolean(string='Generated Link', )
    amount_residual_signed = fields.Float(string='Balance Remaining')
    amount_total_signed = fields.Float(string='Amount Total')
    request_amount = fields.Float(string='Request Amount', )
    amount_due = fields.Float(string='Amount Due')
    check_box = fields.Boolean('Select')
    link_check_box = fields.Boolean('Link Check Box')
    email_id = fields.Char(string='Email ID')
    invoice_id = fields.Char('Invoice ID')
    currency_id = fields.Many2one('res.currency', string='Company Currency')
    select_template = fields.Many2one('email.templates', string='Select Template')
    email_subject = fields.Char(string='Subject', related='select_template.template_subject')
    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config')

    @api.constrains('request_amount')
    def _constrains_min_amount(self):
        for rec in self:
            if rec.request_amount > rec.amount_residual_signed:
                raise UserError('Request Amount cannot be greater than the Balance Remaining.')
            elif rec.request_amount < 0:
                raise UserError('Request Amount cannot be negative.')


class WizardRemoveExistPaymentLink(models.TransientModel):
    _name = 'wizard.exist.payment.link'
    _description = "Wizard Exist Payment Link"

    record_id = fields.Integer('Record Id')
    invoice_id = fields.Many2one('account.move', 'Invoice')
    record_model = fields.Char('Record Model')
    text = fields.Text('Message', readonly=True)

    def delete_record_link(self):
        values = self.env.context.get('kwargs_values')
        if values:
            vals = [val for val in values if val['generated_link']]
            for inv in vals:
                odoo_invoice = self.env['account.move'].search([('id', '=', inv['invoice'][0])])
                odoo_invoice.delete_ebiz_invoice()
        return message_wizard(f'{len(vals)} payment link(s) removed successfully.')


class WizardGenerateSelectPaymentLink(models.TransientModel):
    _name = 'wizard.generate.select.payment.link'
    _description = "Wizard Generate Select Payment Link"

    record_id = fields.Integer('Record Id')
    invoice_id = fields.Many2one('account.move', 'Invoice')
    record_model = fields.Char('Record Model')
    text = fields.Text('Message', readonly=True)

    def generate_selected_record_link(self):
        values = self.env.context.get('kwargs_values')
        profile = False
        payment_lines = []

        if values:
            vals = [val for val in values if not val['generated_link']]
            for inv in vals:
                search_invoice = self.env['account.move'].search([('id', '=', inv['invoice'][0])])
                if search_invoice:
                    if not search_invoice.save_payment_link:
                        payment_line = {
                            "invoice_id": int(search_invoice.id),
                            "name": search_invoice.name,
                            "customer_name": search_invoice.partner_id.id,
                            "amount_due": search_invoice.amount_residual_signed,
                            "amount_residual_signed": search_invoice.amount_residual_signed,
                            "amount_total_signed": search_invoice.amount_total,
                            "request_amount": search_invoice.amount_residual_signed,
                            "odoo_payment_link": search_invoice.odoo_payment_link,
                            "currency_id": self.env.user.currency_id.id,
                            "email_id": search_invoice.partner_id.email,
                            "ebiz_profile_id": search_invoice.partner_id.ebiz_profile_id.id,
                        }
                        payment_lines.append([0, 0, payment_line])
                profile = search_invoice.partner_id.ebiz_profile_id.id
        wiz = self.env['wizard.ebiz.generate.link.payment.bulk'].with_context(
            profile=profile).create(
            {'payment_lines': payment_lines,
             'ebiz_profile_id': profile})
        action = self.env.ref('payment_ebizcharge_crm.wizard_generate_link_form_views_action').read()[0]
        action['res_id'] = wiz.id
        action['context'] = self.env.context
        return action

from odoo import fields, models, api
import logging
from ..models.ebiz_charge import message_wizard
from odoo.exceptions import ValidationError, UserError

_logger = logging.getLogger(__name__)


class WizardGenerateSoPaymentLink(models.TransientModel):
    _name = 'wizard.generate.so.link.payment'
    _description = "Wizard Generate So Payment Link"

    payment_lines = fields.One2many('wizard.generate.so.payment.link.lines', 'wizard_id')
    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config', string='EBizCharge Merchant Account')
    sale_link = fields.Boolean(string='Sales')


    def generate_payment_link(self):
        try:
            wizards = []
            if any(rec.transaction_type == 'pre_auth' and rec.request_amount < rec.amount_residual_signed for rec in self.payment_lines):
                raise UserError('Request Amount must be greater than or equal to Balance Remaining for Pre-Auths.')
            for record in self.payment_lines:
                tem_check = self.env['email.templates'].search([
                    ('template_type_id', '=', 'SalesOrderWebFormEmail'),
                    ('instance_id', '=', record.partner_id.ebiz_profile_id.id)], limit=1)
                wizard_vals = {
                    'ebiz_profile_id': record.partner_id.ebiz_profile_id.id,
                    'partner_id': record.partner_id.id,
                    'amount': record.amount_residual_signed,
                    'currency_id': record.currency_id.id,
                    'res_model': 'sale.order',
                    'link_check_box': True,
                    'select_template': tem_check.id,
                    'transaction_type': record.transaction_type,
                    'is_sale_order': True,
                }
                wizard = self.env['ebiz.payment.link.wizard'].create(wizard_vals)
                wizards.append(wizard)
                if wizard:
                    wizard.with_context(
                        {'active_model': 'sale.order', 'active_id': int(record.order_id.id),
                         'from_bulk': True,
                         'requested_amount': record.request_amount}).generate_link()

            if self.sale_link:
                copy_links = []
                for record in self.payment_lines:
                    doc = self.env['sale.order'].search([('id','=',int(record.order_id.id))], limit=1)
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


class WizardGenerateSoPaymentLinkLines(models.TransientModel):
    _name = 'wizard.generate.so.payment.link.lines'
    _description = "Wizard Generate So Payment Link Lines"

    wizard_id = fields.Many2one('wizard.generate.so.link.payment')
    order_id = fields.Many2one('sale.order', 'So')
    transaction_type = fields.Selection(
        [('pre_auth', 'Pre-Auth'), ('deposit', 'Deposit'),
         ],
        string='Transaction Type' )

    name = fields.Char(string='Number')
    partner_id = fields.Many2one('res.partner', string='Customer')
    so_payment_link = fields.Boolean(string='Generated Link', )
    amount_residual_signed = fields.Float(string='Balance Remaining', related='order_id.ebiz_order_amount_residual')
    amount_total_signed = fields.Float(string='Amount Total')
    request_amount = fields.Float(string='Request Amount', )
    amount_due = fields.Float(string='Amount Due')
    check_box = fields.Boolean('Select')
    link_check_box = fields.Boolean('Link Check Box')
    email_id = fields.Char(string='Email ID')
    record_id = fields.Char('Invoice ID')
    currency_id = fields.Many2one('res.currency', string='Company Currency')
    select_template = fields.Many2one('email.templates', string='Select Template')
    email_subject = fields.Char(string='Subject', related='select_template.template_subject')
    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config')
    transaction_type = fields.Selection([
        ('pre_auth', 'Pre-Auth'),
        ('deposit', 'Deposit'),
    ], string='Transaction Type', required=True, default='pre_auth')

    # @api.onchange('request_amount')
    # def onchange_request_amount(self):
    #     for rec in self:
    #         if rec.request_amount < rec.amount_residual_signed and rec.transaction_type=='pre_auth':
    #             raise UserError('Request Amount must be greater than or equal to Balance Remaining for Pre-Auths.')
    #         elif rec.request_amount < 0:
    #             raise UserError('Request Amount cannot be negative.')


class WizardRemoveSoExistPaymentLink(models.TransientModel):
    _name = 'wizard.so.exist.payment.link'
    _description = "Wizard SO Exist Payment Link"

    record_id = fields.Many2one('sale.order', 'SO')
    record_model = fields.Char('Record Model')
    text = fields.Text('Message', readonly=True)

    def delete_record_link(self):
        values = self.env.context.get('kwargs_values')
        if values:
            vals = [val for val in values if val['generated_link']]
            for inv in vals:
                odoo_sale = self.env['sale.order'].search([('id', '=', inv['order_id'][0])])
                odoo_sale.delete_ebiz_so_link()
        return message_wizard(f'{len(vals)} payment link(s) removed successfully.')


class WizardGenerateSelectPaymentLink(models.TransientModel):
    _name = 'wizard.generate.so.select.payment.link'
    _description = "Wizard Generate Select Payment Link"

    record_id = fields.Integer('Record Id')
    order_id = fields.Many2one('sale.order', 'So')
    record_model = fields.Char('Record Model')
    text = fields.Text('Message', readonly=True)

    def generate_selected_record_link(self):
        values = self.env.context.get('kwargs_values')
        profile = False
        payment_lines = []
        if values:
            vals = [val for val in values if not val['generated_link']]
            for inv in vals:
                search_so = self.env['sale.order'].search([('id', '=', inv['order_id'][0])])
                if search_so:
                    if not search_so.save_payment_link:
                        payment_line = {
                            "order_id": int(search_so.id),
                            "partner_id": search_so.partner_id.id,
                            "transaction_type": search_so.partner_id.ebiz_profile_id.gpl_pay_sale,
                            "amount_total_signed": search_so.amount_total,
                            "request_amount": search_so.ebiz_order_amount_residual,
                            "so_payment_link": search_so.odoo_payment_link,
                            "currency_id": self.env.user.currency_id.id,
                            "email_id": search_so.partner_id.email,
                            "ebiz_profile_id": search_so.partner_id.ebiz_profile_id.id,
                        }
                        payment_lines.append([0, 0, payment_line])
                profile = search_so.partner_id.ebiz_profile_id.id
        wiz = self.env['wizard.generate.so.link.payment'].with_context(
            profile=profile).create(
            {'payment_lines': payment_lines,
             'ebiz_profile_id': profile})
        action = self.env.ref('payment_ebizcharge_crm.wizard_generate_so_link_form_views_action').read()[0]
        action['res_id'] = wiz.id
        action['context'] = self.env.context
        return action

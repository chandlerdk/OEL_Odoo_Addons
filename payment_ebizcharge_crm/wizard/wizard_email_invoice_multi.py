
from odoo import models, api, fields
import json
from odoo.exceptions import UserError, ValidationError
from ..models.ebiz_charge import message_wizard


class EmailReceipt(models.TransientModel):
    _name = 'wizard.email.multi.receipts'
    _description = "Wizard Email Multi Receipts"

    partner_ids = fields.Many2many('res.partner', string='Customer')
    select_template = fields.Many2one('email.receipt', string='Select Template')
    email_subject = fields.Char(string='Subject')
    record_id = fields.Char(string='Record ID')
    model_name = fields.Char(string='Model Name')
    email_customer = fields.Char('', related='partner_ids.email', readonly=True)
    email_transaction_id = fields.Char(string='RefNum')

    def send_email(self):
        try:
            instance = None
            if self.partner_ids.ebiz_profile_id:
                instance = self.partner_ids.ebiz_profile_id

            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            record = self.env[self.env.context.get('active_model')].browse(self.env.context.get('active_id'))
            if not record.partner_id.email:
                raise UserError(f'"{record.partner_id.name}" does not contain Email Address!')
            form_url = ebiz.client.service.EmailReceipt(**{
                'securityToken': ebiz._generate_security_json(),
                'transactionRefNum': self.email_transaction_id,
                'receiptRefNum': self.select_template.receipt_id,
                'receiptName': self.select_template.name,
                'emailAddress': self.partner_ids.email,
            })
            if form_url.Status == 'Success':
                return message_wizard('The invoice receipt has been sent successfully!')
            elif form_url.Status == 'Failed':
                raise UserError('Operation Denied!')

        except Exception as e:
            raise ValidationError(e)

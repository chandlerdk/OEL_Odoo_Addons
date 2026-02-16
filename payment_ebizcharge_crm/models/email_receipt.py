# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError


class EmailReceipt(models.Model):
    _name = 'email.receipt'
    _description = "Email Receipt"

    name = fields.Char(string='Name')
    receipt_subject = fields.Char(string='Subject')
    receipt_from_email = fields.Char(string='From Email')
    receipt_id = fields.Char(string='Receipt ID')
    auto_get_receipts = fields.Char(string="Auto Get Receipts", compute='get_receipts')
    target = fields.Char(string='Description')
    content_type = fields.Char(string='Type ID')
    instance_id = fields.Many2one('ebizcharge.instance.config')

    def get_receipts(self):
        """
            Niaz implementation
            Fetch email receipts
        """
        try:
            instances = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_active', '=', True)])
            if instances:
                instances[0].action_update_profiles('email.templates')
            email_obj = self.env['email.receipt']
            ebiz_obj = self.env['ebiz.charge.api']
            for instance in instances:
                ebiz = ebiz_obj.get_ebiz_charge_obj(instance=instance)
                receipts = ebiz.client.service.GetEmailTemplates(**{
                    'securityToken': ebiz._generate_security_json(),
                })
                if receipts:
                    for template in receipts:
                        odoo_temp = email_obj.search(
                            [('receipt_id', '=', template['TemplateInternalId']), ('instance_id', '=', instance.id)])
                        if not odoo_temp:
                            if template['TemplateTypeId'] == 'TransactionReceiptMerchant' or template[
                                'TemplateTypeId'] == 'TransactionReceiptCustomer':
                                email_obj.create({
                                    'name': template['TemplateName'],
                                    'receipt_subject': template['TemplateSubject'],
                                    'receipt_id': template['TemplateInternalId'],
                                    'target': template['TemplateDescription'],
                                    'content_type': template['TemplateTypeId'],
                                    'instance_id': instance.id,
                                })
            self.auto_get_receipts = False

        except Exception as e:
            raise ValidationError(e)

# -*- coding: utf-8 -*-
from odoo import models, fields
from odoo.exceptions import ValidationError


class EmailTemplates(models.Model):
    _name = 'email.templates'
    _description = "Email Templates"

    name = fields.Char(string='Name')
    template_id = fields.Char(string='Email Templates ID')
    template_subject = fields.Char(string='Subject')
    template_description = fields.Char(string='Description')
    template_type_id = fields.Char(string='Type ID')
    auto_get_templates = fields.Char(string="Auto Get Templates", compute='get_templates')
    instance_id = fields.Many2one('ebizcharge.instance.config')

    def get_templates(self):
        """
            Niaz implementation
            Used to fetch Email Receipts
            """
        try:
            instances = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_active', '=', True)])
            if instances:
                instances[0].action_update_profiles('email.templates')
            ebiz_obj = self.env['ebiz.charge.api']
            template_obj = self.env['email.templates']
            for instance in instances:
                ebiz = ebiz_obj.get_ebiz_charge_obj(instance=instance)
                templates = ebiz.client.service.GetEmailTemplates(**{
                    'securityToken': ebiz._generate_security_json()
                })
                if templates:
                    for template in templates:
                        odoo_temp = template_obj.search(
                            [('template_id', '=', template['TemplateInternalId']), ('instance_id', '=', instance.id)])
                        if not odoo_temp:
                            if template['TemplateTypeId'] != 'TransactionReceiptMerchant' and template[
                                'TemplateTypeId'] != 'TransactionReceiptCustomer':
                                template_obj.create({
                                    'name': template['TemplateName'],
                                    'template_id': template['TemplateInternalId'],
                                    'template_subject': template['TemplateSubject'],
                                    'template_description': template['TemplateDescription'],
                                    'template_type_id': template['TemplateTypeId'],
                                    'instance_id': instance.id,
                                })
                        else:
                            odoo_temp.write({
                                'template_subject': template['TemplateSubject'],
                            })
            self.auto_get_templates = False
        except Exception as e:
            raise ValidationError(e)

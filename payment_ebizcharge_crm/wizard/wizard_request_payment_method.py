from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
from ..models.ebiz_charge import message_wizard


class RequestPaymentMethod(models.TransientModel):
    _name = 'wizard.ebiz.request.payment.method'
    _description = "Wizard Ebiz Request Method"

    @api.model
    def default_get(self, default_fields):
        rec = super(RequestPaymentMethod, self).default_get(default_fields)
        partner = self.env['res.partner'].browse([self._context['partner']])
        ach_option = True if partner.ebiz_profile_id.merchant_data else False
        cc_option = True if partner.ebiz_profile_id.allow_credit_card_pay else False       
        rec.update({
            'cc_option': cc_option,
            'ach_option': ach_option,
        })
        return rec

    partner_id = fields.Many2many('res.partner', string='Customer')
    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config')

    def _default_template(self):
        partner = self.env['res.partner'].browse([self._context['partner']])

        tem_check = self.env['email.templates'].search(
            [('template_type_id', '=', 'AddPaymentMethodFormEmail'), ('instance_id', '=', partner.ebiz_profile_id.id)])
        if tem_check:
            return tem_check[0].id
        else:
            ebiz_obj = self.env['ebiz.charge.api']
            template_obj = self.env['email.templates']
            instance = partner.ebiz_profile_id
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
            tem_check = self.env['email.templates'].search(
                [('template_type_id', '=', 'AddPaymentMethodFormEmail'),
                 ('instance_id', '=', partner.ebiz_profile_id.id)])
            if tem_check:
                return tem_check[0].id
            else:
                return None


    select_template = fields.Many2one('email.templates', string='Select Template', default=_default_template)
    email = fields.Char('Email')
    subject = fields.Char('Subject', related='select_template.template_subject', readonly=False)
    from_email = fields.Char("From Email")

    cc_option = fields.Boolean(string='CC Option')
    ach_option = fields.Boolean(string='ACH Option')

    payment_method_type = fields.Selection([('BOTH', 'Both'),
                                            ('CC', 'Credit Card'),
                                            ],
                                           string='Payment Method Type', default='BOTH')
    payment_method_type_cc = fields.Selection([('CC', 'Credit Card')
                                               ],
                                              string='Payment Method Type', default='CC')
    payment_method_type_ach = fields.Selection([('ACH', "Bank Account")], string='Payment Method Type', default='ACH')

    email_note = fields.Text('Additional Email Comments')



    def send_email(self):
        try:
            instance = False
            if self.partner_id.ebiz_profile_id:
                instance = self.partner_id.ebiz_profile_id
            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            partner = self.env['res.partner']
            addr = self.partner_id.address_get(['delivery', 'invoice'])
            payment_type = 'CC'
            if self.cc_option and self.ach_option and self.payment_method_type=='BOTH':
                payment_type = 'CC,ACH'
            elif self.cc_option and self.ach_option and self.payment_method_type == 'CC':
                payment_type = 'CC'
            elif self.cc_option and not self.ach_option:
                payment_type = 'CC'
            elif not self.cc_option and self.ach_option:
                payment_type = 'ACH'
            ePaymentForm = {
                'FormType': 'PmRequestForm',
                'FromEmail': 'support@ebizcharge.com',
                'FromName': 'EBizCharge',
                'EmailSubject': self.subject,
                'EmailNotes': self.email_note if self.email_note else '',
                'EmailAddress': self.email,
                'EmailTemplateID': self.select_template.template_id,
                'EmailTemplateName': self.select_template.name,
                'CustFullName': self.partner_id.name,
                'BillingAddress': ebiz._get_customer_address(partner.browse(addr['invoice'])),
                'InvoiceNumber': 'PM',
                'PayByType': payment_type,
                'SoftwareId': 'Odoo CRM',
                'CustomerId': self.partner_id.id,
                'TotalAmount': 0.05,
                'AmountDue': 0.05,
                'ShowViewInvoiceLink': True,
                'SendEmailToCustomer': True,
            }
            form_url = ebiz.client.service.GetEbizWebFormURL(**{
                'securityToken': ebiz._generate_security_json(),
                'ePaymentForm': ePaymentForm
            })
            self.partner_id.request_payment_method_sent = True
            return message_wizard('Payment method request was successfully sent.')

        except Exception as e:
            raise ValidationError(e)


class PaymentMethodBulk(models.TransientModel):
    _name = 'wizard.ebiz.request.payment.method.bulk'
    _description = "Wizard EBiz Request Payment Method Bulk"

    partner_id = fields.Many2many('email.recipients', string='Customer')

    @api.model
    def default_get(self, default_fields):
        rec = super(PaymentMethodBulk, self).default_get(default_fields)
        if 'ebiz_profile_id' in rec:
            instance = self.env['ebizcharge.instance.config'].search([('id', '=', rec['ebiz_profile_id'] )], limit=1)
            instance_id = instance.id
            ach_option = True if instance.merchant_data else False
            cc_option = True if instance.allow_credit_card_pay else False
            tem_check = self.env['email.templates'].search([('instance_id', '=', instance_id), ('template_type_id', '=', 'AddPaymentMethodFormEmail')])
            if tem_check:
                select_template = tem_check[0].id
            else:
                ebiz_obj = self.env['ebiz.charge.api']
                template_obj = self.env['email.templates']
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
                tem_check = self.env['email.templates'].search(
                    [('instance_id', '=', instance_id), ('template_type_id', '=', 'AddPaymentMethodFormEmail')])
                if tem_check:
                    select_template = tem_check[0].id
                else:
                    select_template = False
 
            rec.update({
                'cc_option': cc_option,
                'ach_option': ach_option,
                'select_template': select_template,
            })
        return rec

    def _default_template(self):
        if 'profile' in self.env.context:
            instances = self.env['ebizcharge.instance.config'].search([('id', '=', self.env.context['profile'])])
        else:
            instances = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_active', '=', True)])
        instance_id = False
        for instance in instances:
            instance_id = instance.id
        tem_check = self.env['email.templates'].search(
            [('instance_id', '=', instance_id), ('template_type_id', '=', 'AddPaymentMethodFormEmail')])
        if tem_check:
            return tem_check[0].id
        else:
            return None

    select_template = fields.Many2one('email.templates', string='Select Template')
    subject = fields.Char('Subject', related='select_template.template_subject', readonly=False)
    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config', string='EBizCharge Merchant Account')
    cc_option = fields.Boolean(string='CC Option')
    ach_option = fields.Boolean(string='ACH Option')
    payment_method_type = fields.Selection([('BOTH', 'Both'),
                                            ('CC', 'Credit Card'),
                                            ],
                                           string='Payment Method Type', default='BOTH')
    payment_method_type_cc = fields.Selection([('CC', 'Credit Card')
                                               ],
                                              string='Payment Method Type', default='CC')
    payment_method_type_ach = fields.Selection([('ACH', "Bank Account")], string='Payment Method Type', default='ACH')
    email_note = fields.Text('Additional Email Comments')
    is_read_type = fields.Boolean(string='Read Type')



    def send_email(self):
        try:
            resp_lines = []
            success = 0
            failed = 0
            total_count = len(self.partner_id)
            for record in self.partner_id:
                resp_line = {}
                payment_type = 'CC'
                if self.cc_option and self.ach_option and self.payment_method_type == 'BOTH':
                    payment_type = 'CC,ACH'
                elif self.cc_option and self.ach_option and self.payment_method_type == 'CC':
                    payment_type = 'CC'
                elif self.cc_option and not self.ach_option:
                    payment_type = 'CC'
                elif not self.cc_option and self.ach_option:
                    payment_type = 'ACH'

                resp_line['customer_name'] = resp_line['customer_id'] = record.partner_id.id
                resp_line['email_address'] = record.email
                if record.email and '@' in record.email and '.' in record.email:
                    ePaymentForm = {
                        'FormType': 'PmRequestForm',
                        'FromEmail': 'support@ebizcharge.com',
                        'FromName': 'EBizCharge',
                        'EmailSubject': self.subject,
                        'EmailNotes': self.email_note if self.email_note else '',
                        'EmailAddress': record.email,
                        'EmailTemplateID': self.select_template.template_id,
                        'EmailTemplateName': self.select_template.name,
                        'CustFullName': record.name,
                        'InvoiceNumber': 'PM',
                        'PayByType': payment_type,
                        'SoftwareId': 'Odoo CRM',
                        'CustomerId': record.partner_id.id,
                        'TotalAmount': 0.05,
                        'AmountDue': 0.05,
                        'ShowViewInvoiceLink': True,
                        'SendEmailToCustomer': True,
                    }
                    instance = None
                    if record.partner_id.ebiz_profile_id:
                        instance = record.partner_id.ebiz_profile_id

                    ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
                    form_url = ebiz.client.service.GetEbizWebFormURL(**{
                        'securityToken': ebiz._generate_security_json(),
                        'ePaymentForm': ePaymentForm
                    })
                    record.partner_id.request_payment_method_sent = True
                    self.env['rpm.counter'].create({
                        'request_id': form_url.split('=')[1],
                        'counter': 1,
                    })

                    resp_line['status'] = 'Success'
                    success += 1
                elif not record.email:
                    resp_line['status'] = 'Failed (No Email Address)'
                    failed += 1
                else:
                    resp_line['status'] = 'Failed (Wrong Email Address)'
                    failed += 1

                resp_lines.append([0, 0, resp_line])

            if self.env.context.get('active_model') == 'payment.method.ui':
                active_id = self.env[self.env.context.get('active_model')].browse(self.env.context.get('active_id'))
                active_id.search_customers()

            wizard = self.env['wizard.multi.payment.message'].create({'name': 'send', 'lines_ids': resp_lines,
                                                                      'success_count': success, 'failed_count': failed,
                                                                      'total': total_count})
            action = self.env.ref('payment_ebizcharge_crm.wizard_multi_payment_message_action').read()[0]
            action['context'] = self._context
            action['res_id'] = wizard.id
            return action

        except Exception as e:
            raise ValidationError(e)


class EmailRecipients(models.TransientModel):
    _name = 'email.recipients'
    _description = "Email Recipients"

    partner_id = fields.Many2one('res.partner', string='Customer')
    email = fields.Char(string="Email")
    name = fields.Char(related='partner_id.name')


class RPMCounter(models.Model):
    _name = 'rpm.counter'
    _description = "Rpm Counter"

    request_id = fields.Char(string='Request ID')
    counter = fields.Integer(string="Counter")


from odoo import models, api, fields
import json
from odoo.exceptions import UserError, ValidationError
from ..models.ebiz_charge import message_wizard


class EmailReceipt(models.TransientModel):
    _name = 'wizard.email.receipts'
    _description = "Wizard Email Receipts"

    partner_ids = fields.Many2many('res.partner', string='Customer')
    select_template = fields.Many2one('email.receipt', string='Select Template', required=True)
    email_subject = fields.Char(string='Subject', related='select_template.receipt_subject')
    record_id = fields.Char(string='Record ID')
    model_name = fields.Char(string='Model Name')
    email_customer = fields.Char('')
    email_transaction_id = fields.Char(string='RefNum')
    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config')
    multiple_ref_num = fields.Many2many('account.move.receipts', string='Select Transaction',)

    def send_email(self):
        try:
            instance = None
            if self.partner_ids.ebiz_profile_id:
                instance = self.partner_ids.ebiz_profile_id

            if instance and not instance.use_econnect_transaction_receipt:
                raise ValidationError(
                    'Configuration required. Please enable eConnect transaction receipts in the integration server.')

            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            form_url = False
            for ref_no in self.multiple_ref_num:
                if '@' in self.email_customer and '.' in self.email_customer:
                    form_url = ebiz.client.service.EmailReceipt(**{
                        'securityToken': ebiz._generate_security_json(),
                        'transactionRefNum': ref_no['ref_nums'],
                        'receiptRefNum': self.select_template.receipt_id,
                        'receiptName': self.select_template.name,
                        'emailAddress': self.email_customer,
                    })
                else:
                    raise UserError('You might have entered wrong email address!')

            if form_url and form_url.Status == 'Success':
                return message_wizard('The invoice receipt has been sent successfully!')
            elif form_url and form_url.Status == 'Failed':
                raise ValidationError('Configuration required. Please enable eConnect transaction receipts in the integration server.')
            else:
                raise ValidationError('Configuration required. Please enable eConnect transaction receipts in the integration server.')

        except Exception as e:
            raise ValidationError(e)
    
    
class EmailReceiptBulk(models.TransientModel):
    _name = 'wizard.email.receipts.bulk'
    _description = "Wizard Email Receipts bulk"

    partner_ids = fields.Many2many('res.partner', string='Customer')
    select_template = fields.Many2one('email.receipt', string='Select Template')
    email_subject = fields.Char(string='Subject', related='select_template.receipt_subject')
    record_id = fields.Char(string='Record ID')
    model_name = fields.Char(string='Model Name')
    email_customer = fields.Char('', related='partner_ids.email', readonly=True)
    email_transaction_id = fields.Char(string='RefNum')
    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config')

    def send_email(self):
        try:
            resp_lines = []
            success = 0
            failed = 0
            filter_record = self._context.get('transaction_ids')
            for record in filter_record:
                resp_line = {}
                customer_name_up =  record['account_holder']
                if record['partner_id'] and len(record['partner_id'])>=2:
                    customer_name_up = record['partner_id'][1]
                resp_line.update({
                    'customer_name': customer_name_up,
                    'customer_id': record['customer_id'],
                    'ref_num': record['ref_no'],
                })
                if record['email_id']:
                    if '@' in record['email_id'] and '.' in record['email_id']:
                        instance = self.ebiz_profile_id

                        if instance and not instance.use_econnect_transaction_receipt:
                            raise ValidationError(
                                'Configuration required. Please enable eConnect transaction receipts in the integration server.')

                        ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
                        form_url = ebiz.client.service.EmailReceipt(**{
                            'securityToken': ebiz._generate_security_json(),
                            'transactionRefNum': record['ref_no'],
                            'receiptRefNum': self.select_template.receipt_id,
                            'receiptName': self.select_template.name,
                            'emailAddress': record['email_id'],
                        })
                        if form_url.Status == 'Success':
                            resp_line['status'] = 'Success'
                            success += 1
                        elif form_url.Status == 'Failed':
                            raise ValidationError(
                                'Configuration required. Please enable eConnect transaction receipts in the integration server.')
                    else:
                        resp_line['status'] = 'Wrong Email Address!'
                        failed += 1
                else:
                    resp_line['status'] = 'Email ID Not Found!'
                    failed += 1

                resp_lines.append([0, 0, resp_line])
            else:
                wizard = self.env['wizard.transaction.history.message'].create({'name': 'Message', 'lines_ids': resp_lines,
                                                                                'success_count': success,
                                                                                'failed_count': failed, })
                return {'type': 'ir.actions.act_window',
                        'name': 'Email Receipt',
                        'res_model': 'wizard.transaction.history.message',
                        'target': 'new',
                        'view_mode': 'form',
                        'view_type': 'form',
                        'res_id': wizard.id,
                        'context': self._context
                        }

        except Exception as e:
            raise ValidationError(e)

from odoo import fields, models,api, _
from odoo.exceptions import UserError, ValidationError
import logging
from ..models.ebiz_charge import message_wizard

_logger = logging.getLogger(__name__)


class WizardAddNewCard(models.TransientModel):
    _name = 'wizard.add.new.ach'
    _description = "Wizard Add New Ach"
    
    @api.model
    def year_selection(self):
        today = fields.Date.today()
        # year =  # replace 2000 with your a start year
        year = 2000
        max_year = today.year+30
        year_list = []
        while year != max_year: # replace 2030 with your end year
            year_list.append((str(year), str(year)))
            year += 1
        return year_list

    @api.model
    def month_selection(self):
        m_list = []
        for i in range(1, 13):
            m_list.append((str(i), str(i)))
        return m_list
    
    ach_account_holder_name = fields.Char("Account Holder Name*")
    ach_account_number = fields.Char("Account Number *")
    ach_account_type = fields.Selection([('Checking', 'Checking'), (
        'Savings', 'Savings')], "Account Type *", default="Checking")
    ach_routing = fields.Char('Routing Number *')
    partner_id = fields.Many2one('res.partner')
    make_default_card = fields.Boolean('Make Default')

    @api.constrains('ach_account_number')
    def validate_ach_account_number(self):
        for rec in self:
            if not rec.ach_account_number.isnumeric():
                raise ValidationError(_('Account number must be numeric only!'))
            elif rec.ach_account_number and (len(rec.ach_account_number) > 17 or len(rec.ach_account_number) < 4):
                raise ValidationError(_('Account number should be 4-17 digits!'))
   
    @api.constrains('ach_routing')
    def validate_ach_routing(self):
        if self.ach_routing:
            if not len(self.ach_routing) == 9:
                raise ValidationError(_('Routing number must be 9 digits.'))

    def save_ach(self):
        try:
            current_entry = self.create_bank_account()
            if self.make_default_card:
                check = self.partner_id.payment_token_ids.filtered(
                    lambda x: x.is_default and x.id != self.id and x.provider_id.code == 'ebizcharge')
                if check:
                    message = 'A payment method is already selected as default! Do you want to mark this one as ' \
                              'default instead?'
                    wiz = self.env['wizard.validate.default'].create(
                        {'token_id': current_entry.id, 'text': message, 'default_token_id': check[0].id})
                    action = self.env.ref('payment_ebizcharge_crm.action_wizard_validate_default_on_create').read()[0]
                    action['res_id'] = wiz.id
                    return action
                else:
                    self.make_default(current_entry)
        except Exception as e:
            raise ValidationError(str(e))
        return message_wizard('Bank account has been successfully saved!')

    def make_default(self, current_pointer):
        check = self.partner_id.payment_token_ids.filtered(lambda x: x.is_default and x.provider_id.code == 'ebizcharge')
        if check:
            self.partner_id.payment_token_ids.filtered(lambda x: x.is_default and x.provider_id.code == 'ebizcharge').update({'is_default': False})
        current_pointer.write({'is_default': True})
        instance = None
        if self.partner_id.ebiz_profile_id:
            instance = self.partner_id.ebiz_profile_id
        ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
        resp = ebiz.client.service.SetDefaultCustomerPaymentMethodProfile(**{
            'securityToken': ebiz._generate_security_json(),
            'customerToken': self.partner_id.ebizcharge_customer_token,
            'paymentMethodId': current_pointer.ebizcharge_profile
        })
        return True

    def create_ebiz_payment_method(self, params_dict):
        instance = self.partner_id.ebiz_profile_id
        ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
        resp = ebiz.add_customer_payment_profile(profile=params_dict, p_type='bank')
        return resp

    def create_bank_account(self):
        params = {
            "account_holder_name": self.ach_account_holder_name, 
            "payment_details": self.ach_account_number,
            "account_number": self.ach_account_number,
            "account_type": self.ach_account_type,
            "routing": self.ach_routing,
            "partner_id": self.partner_id.id,
            "ebiz_internal_id": self.partner_id.ebiz_internal_id,
            "token_type": 'ach',
            "provider_ref": 'Temp',
            'provider_id': self.env['payment.provider'].search(
                [('company_id', '=', self.partner_id.company_id.id if self.partner_id.company_id else self.env.company.id), ('code', '=', 'ebizcharge')]).id
            }
        resp = self.create_ebiz_payment_method(params)
        del params['ebiz_internal_id']
        method = self.env.ref('payment_ebizcharge_crm.payment_method_ebizcharge').id
        params.update({
            'payment_method_id': method,
            'ebizcharge_profile': resp,
            "user_id": self.env.user.id,
            "is_card_save": True,
            "active": True,
        })
        token = self.env['payment.token'].with_context({'from_wizard': True}).create(params)
        token.action_sync_token_to_ebiz()
        return token

# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from odoo.exceptions import ValidationError, UserError
from xlwt import easyxf
import xlwt
import requests
import json
import hashlib
import base64


class EBizChargeInstanceConfig(models.Model):
    _name = 'ebizcharge.instance.config'
    _description = "EBizCharge Instance Configuration"

    def _domain_companies_ids(self):
        instances = self.env['ebizcharge.instance.config'].search([])
        company_list = []
        for ins in instances:
            company_list += ins.company_ids.ids
        companies = self.env['res.company'].search([('id', 'not in', company_list)])
        return [('id', 'in', companies.ids)]

    name = fields.Char(string="Merchant Account")
    company_id = fields.Many2one('res.company', string="Company")
    company_ids = fields.Many2many('res.company', string="Companies")
    is_default = fields.Boolean(string="Set Default Instance",copy=False)
    ebiz_auto_sync_customer = fields.Boolean(string='Auto Sync Customers', default=False)
    ebiz_auto_sync_invoice = fields.Boolean(string='Auto Sync Invoices', default=False)
    ebiz_auto_sync_sale_order = fields.Boolean(string='Auto Sync Sales Orders', default=False)
    ebiz_auto_sync_products = fields.Boolean(string='Auto Sync Products', default=False)
    ebiz_auto_sync_credit_notes = fields.Boolean(string='Auto Sync Credit Notes', default=False)
    ebiz_security_key = fields.Char(string='Security key')
    ebiz_user_id = fields.Char(string='User ID')
    ebiz_password = fields.Char(string='Password')
    ebiz_website_allowed_command = fields.Selection([
        ('pre-auth', 'Allow authorization only'),
        ('deposit', 'Allow authorization and capture')],
        string='eCommerce Orders', default='pre-auth')

    is_surcharge_enabled = fields.Boolean(string="Surcharge Enabled")
    surcharge_type_id = fields.Char(string="Surcharge Type")
    surcharge_percentage = fields.Float(string="Surcharge Percentage")
    surcharge_caption = fields.Char(string="Surcharge Caption")
    surcharge_terms = fields.Char(string="Surcharge Terms")
    batch_terms = fields.Char(string="Batch Terms")
    ebiz_document_download_range = fields.Selection([
        ('1-week', 'One Week'),
        ('2-week', 'Two Weeks'),
        ('1-month', 'One Month'),
        ('2-month', 'Two Months'),
        ('6-month', 'Six Months'),
        ('1-year', 'One Year')], string='Document Download Date Range', required=True,
        default='1-week')

    is_valid_credential = fields.Boolean()
    is_active = fields.Boolean(string='Is Active', default=True)
    transaction_type = fields.Selection([
        ('pre_auth', 'Pre-Auth'),
        ('deposit', 'Deposit'),
    ], string='Transaction Type', default='pre_auth', index=True)

    scheduler_act_deact = fields.Boolean(string='Scheduler Check', default=False)
    invoice_cron_job = fields.Boolean(string='Download and apply payments', default=False)
    merchant_data = fields.Boolean(string='Merchant Data')
    merchant_card_verification = fields.Char(string='Merchant Data Verification')
    verify_card_before_saving = fields.Boolean(string='Verify Card Before Saving')
    use_full_amount_for_avs = fields.Char(string='UseFullAmountForAVS')
    allow_credit_card_pay = fields.Boolean(string='AllowCreditCardPayments')
    enable_cvv = fields.Boolean(string='EnableCVV')
    
    emv_device_ids = fields.One2many('ebizcharge.emv.device', 'merchant_id', string='EMV Device List', copy=True)

    source_key = fields.Char(string='EMV Source Key')
    pin = fields.Char(string='EMV PIN')
    is_emv_enabled = fields.Boolean(string='Is EMV Enabled', default=False)
    is_emv_pre_auth = fields.Boolean(string='Pre-Auth EMV Enabled', default=False)
    use_econnect_transaction_receipt = fields.Boolean(string='Use EConnect Transaction Receipt', default=False)

    invoice_auto_gpl = fields.Boolean(string='Auto Generate links for Invoices')
    sales_auto_gpl = fields.Boolean(string='Auto Generate links for Sales Orders')
    apply_sale_pay_inv = fields.Boolean(string='Apply sale order payment conversion')
    email_pay_sale = fields.Selection([
        ('pre_auth', 'Pre-Auth'),
        ('deposit', 'Deposit'),
    ], string='Transaction Type', default='pre_auth', index=True)
    gpl_pay_sale = fields.Selection([
        ('pre_auth', 'Pre-Auth'),
        ('deposit', 'Deposit'),
    ], string='GPL Type', default='pre_auth', index=True)



    def generate_hash(self, source_key, seed, pin):
        hash_input = str(source_key) + str(seed) + str(pin)
        hash_value = hashlib.sha256(hash_input.encode()).hexdigest()
        return hash_value

    def generate_auth_info(self, source_key, seed, pin):
        hash_value = self.generate_hash(source_key, seed, pin)
        auth_info = str(source_key) + ':s2/' + str(seed) + '/' + str(hash_value)
        encoded_auth_info = base64.b64encode(auth_info.encode()).decode()
        return 'Basic ' + encoded_auth_info

    def action_get_devices(self):
        url = 'https://secure.ebizcharge.com/api/v2/paymentengine/devices'
        headers = {
            "Content-Type": "application/json"
        }
        # Example usage:
        source_key = self.source_key
        seed = ''
        pin = self.pin

        auth_info = self.generate_auth_info(source_key, seed, pin)
        headers.update({
            "Authorization": auth_info,
        })
        response = requests.get(url, headers=headers)
        data_list = response.content
        final_list = json.loads(data_list)

        emv_devices = self.env['ebizcharge.emv.device'].search([('merchant_id', '=', False)])
        emv_devices.unlink()

        if 'data' in final_list:
            for device in final_list['data']:
                exist = self.env['ebizcharge.emv.device'].search([('source_key', '=', device['key'])])
                if exist:
                    exist.update({
                        'name': device['name'],
                        'pin': device['terminal_info']['key_pin'] if 'terminal_info' in device else '',
                        'status': device['status'].capitalize(),
                        'key': source_key,
                        # 'merchant_id': self.id,
                        'source_key': device['key'],
                        'enable_emv': device['terminal_config']['enable_emv'] if 'terminal_config' in device else '',
                    })
                else:
                    device_info = {
                        'name': device['name'],
                        'pin': device['terminal_info']['key_pin'] if 'terminal_info' in device else '',
                        'status': device['status'].capitalize(),
                        'key': source_key,
                        # 'merchant_id': self.id,
                        'source_key': device['key'],
                        'enable_emv': device['terminal_config']['enable_emv'] if 'terminal_config' in device else '',
                    }
                    ebiz_devices = self.env['ebizcharge.emv.device'].create(device_info)
        else:
            return True



    def action_configure_device(self):
        self.ensure_one()
        device_resp = self.action_get_devices()
        if device_resp!=None:
            self.source_key = ''
            self.pin = ''
        if device_resp != None:
            context = dict()
            context['message'] = 'Invalid EMV credentials.'
            return {
                'name': 'User Error',
                'view_type': 'form',
                'view_mode': 'form',
                'res_model': 'message.wizard',
                'view_id': False,
                'type': 'ir.actions.act_window',
                'target': 'new',
                'context': context
            }
        else:
            emv_device = {
                'name': _('Configure Device'),
                'res_model': 'wizard.emv.device',
                'view_mode': 'form',
                'context': {
                    'default_source_key': self.source_key,
                    'default_merchant_id': self.id,
                    'default_pin': self.pin,
                },
                'target': 'new',
                'type': 'ir.actions.act_window',
            }
            return emv_device   
    

    def unlink(self):
        for record in self:
            profile = self.env['res.partner'].search([('ebiz_profile_id', '=', record.id)])
            if profile:
                raise ValidationError('You cannot delete this Merchant Account because this is attached to a customer.')
            if record.is_default:
                raise ValidationError('You cannot delete this Merchant Account because this is a default Merchant '
                                      'Account. Please set another as default then you can delete it.')
        return super(EBizChargeInstanceConfig, self).unlink()

    @api.onchange('is_default')
    def onchange_is_default_warning(self):
        profiles = self.env['ebizcharge.instance.config'].search(
            [('is_default', '=', True), ('is_active', '=', True), ('id', 'not in', self.ids)])
        if self.company_ids and self.is_default:
            raise UserError(
                'The default merchant account cannot be associated with a company. Please detach the merchant account '
                'from the company if you want to set it as the default.')
        if not profiles and not self.is_default:
            raise UserError('There must always be a default merchant account. Please set a different merchant account '
                            'as the default if you want to remove ' + self.name + ' as the default.')
        if self.is_default and profiles:
            title = _("New Default")
            message = "You have overridden the existing default merchant account and set " + (self.name if self.name
                                                                           else 'this') + " as the new default."
            warning = {
                'title': title,
                'message': message
            }
            return {'warning': warning}

    @api.model_create_multi
    def create(self, vals_list):
        rec = super().create(vals_list)
        if 'ebiz_security_key' in vals_list or 'ebiz_user_id' in vals_list or 'ebiz_password' in vals_list:
            rec.is_valid_credential = False

        if not rec.is_valid_credential:
            rec.update_valid_info(rec)

        if 'company_ids' in vals_list:
            for r in rec.company_ids:
                r.is_selected = True
            companies = self.env['res.company'].search([('is_selected', '=', True)])
            selected_companies = self.env['ebizcharge.instance.config'].search([('is_active', '=', True)]).mapped(
                'company_ids').ids
            for com in companies:
                if com.id not in selected_companies:
                    com.is_selected = False

        if rec.is_default and 'company_ids' in vals_list and rec.company_ids:
            raise UserError('The default merchant account cannot be associated with a company.')

        if 'is_default' in vals_list and rec.is_default:
            profile = self.env['ebizcharge.instance.config'].search(
                [('is_default', '=', True), ('is_active', '=', True), ('id', 'not in', rec.ids)])
            profile.is_default = False
        return rec

    def write(self, vals_list):
        context = {}
        if 'company_ids' in vals_list or 'is_default' in vals_list:
            context.update({
                'is_write': True
            })
        rec = super(EBizChargeInstanceConfig, self.with_context(context)).write(vals_list)
        if 'ebiz_security_key' in vals_list or 'ebiz_user_id' in vals_list or 'ebiz_password' in vals_list:
            self.is_valid_credential = False
        if not self.is_valid_credential:
            self.update_valid_info(self)
        if 'company_ids' in vals_list:
            for r in self.company_ids:
                r.is_selected = True
            companies = self.env['res.company'].search([('is_selected', '=', True)])
            selected_companies = self.env['ebizcharge.instance.config'].search([('is_active', '=', True)]).mapped(
                'company_ids').ids
            for com in companies:
                if com.id not in selected_companies:
                    com.is_selected = False
        if self.is_default and 'company_ids' in vals_list and self.company_ids:
            raise UserError('The default merchant account cannot be associated with a company.')

        if 'is_default' in vals_list and self.is_default:
            profile = self.env['ebizcharge.instance.config'].search(
                [('is_default', '=', True), ('is_active', '=', True), ('id', 'not in', self.ids)])
            profile.is_default = False
        return rec

    def update_valid_info(self, instance):
        if instance and instance.ebiz_security_key:
            ebiz = self.env['ebiz.charge.api'].sudo().get_ebiz_charge_obj(instance=instance)
            resp, card_verification = self.sudo().merchant_details(ebiz)
            if resp:
                instance.update({
                    'is_valid_credential': True,
                    'merchant_data': resp['AllowACHPayments'],
                    'merchant_card_verification': card_verification,
                    'verify_card_before_saving': resp['VerifyCreditCardBeforeSaving'],
                    'use_full_amount_for_avs': resp['UseFullAmountForAVS'],
                    'allow_credit_card_pay': resp['AllowCreditCardPayments'],
                    'enable_cvv': resp['EnableCVVWarnings'],
                })
            else:
                raise UserError('Invalid Credentials.')

    def merchant_details(self, ebiz):
        try:
            resp = ebiz.client.service.GetMerchantTransactionData(**{
                'securityToken': ebiz._generate_security_json()
            })
        except:
            return None, None

        if resp['VerifyCreditCardBeforeSaving']:
            if resp['UseFullAmountForAVS']:
                return resp, 'full-amount'
            else:
                return resp, 'minimum-amount'
        else:
            return resp, 'no-validation'

    @api.model
    def default_get(self, fields):
        res = super(EBizChargeInstanceConfig, self).default_get(fields)
        if 'is_check' in self.env.context:
            instances = self.env['ebizcharge.instance.config'].search(
                [('is_valid_credential', '=', True), ('is_default', '=', True), ('is_active', '=', True)])
            if not instances:
                res['is_default'] = True
        return res

    def get_document_download_start_date(self):
        instance = self.env['ebizcharge.instance.config'].search(
            [('is_valid_credential', '=', True), ('is_default', '=', True), ('is_active', '=', True)], limit=1)
        if instance:
            return self.get_start_date(*instance.ebiz_document_download_range.split('-'))
        else:
            return datetime.now().date() - timedelta(days=6)

    def get_start_date(self, step=1, step_type='week'):
        step_type = step_type if step_type[-1] == 's' else step_type + 's'
        end = datetime.now()
        start = end - relativedelta(**{step_type: int(step)})
        return start.date()

    @api.model
    def web_search_read(self, domain, specification, offset=0, limit=None, order=None, count_limit=None):
        profile = self.env['ebizcharge.instance.config'].search([], limit=1)
        if profile:
            profile.action_update_profiles('ebizcharge.instance.config')
        return super().web_search_read(domain, specification, offset=offset, limit=limit, order=order,
                                       count_limit=count_limit)

    def action_update_profiles(self, active_model):
        model_list = ['upload.customers', 'upload.sale.orders', 'ebiz.upload.invoice', 'upload.products', 'upload.credit.notes',
                      'payment.request.bulk.email', 'payment.method.ui', 'batch.processing', 'inv.payment.link.bulk','sale.order.payment.link.bulk']
        if active_model in model_list:
            model_list.remove(active_model)
        for model in model_list:
            self.env[model].search([], limit=1).ebiz_profile_id = False
            if model=='payment.method.ui':
                self.env[model].search([], limit=1).ebiz_profile_pending_id = False
                self.env[model].search([], limit=1).ebiz_profile_received_id = False

    def get_upload_instance(self, active_model, active_id):
        profile_obj = self.env['ebizcharge.instance.config']
        default_instance = profile_obj.search(
            [('is_valid_credential', '=', True), ('is_default', '=', True), ('is_active', '=', True), '|',
             ('company_ids', '=', False),
             ('company_ids', 'in', self._context.get('allowed_company_ids'))],
            limit=1)
        default_instance.action_update_profiles(active_model)
        default_val = False
        if not active_id.ebiz_profile_id:
            if default_instance:
                if not default_instance.company_ids:
                    default_val = str(default_instance.id)
                elif default_instance.company_ids and default_instance.company_ids.ids in self._context.get(
                        'allowed_company_ids'):
                    default_val = str(default_instance.id)
                else:
                    profile = profile_obj.search(
                        [('is_valid_credential', '=', True), ('is_active', '=', True), '|', ('company_ids', '=', False),
                         ('company_ids', 'in', self.env.company.ids)], limit=1)
                    default_val = str(profile.id) if profile else False
            else:
                profile = profile_obj.search(
                    [('is_valid_credential', '=', True), ('is_active', '=', True), '|', ('company_ids', '=', False),
                     ('company_ids', 'in', self.env.company.ids)], limit=1)
                default_val = str(profile.id) if profile else False
        return default_val

    @api.model
    def _default_get_start(self):
        return self.env['ebizcharge.instance.config'].get_document_download_start_date()

    def _default_get_end_date(self):
        today = datetime.now() + timedelta(days=1)
        return today.date()

    def _default_instance_id(self):
        profile_obj = self.env['ebizcharge.instance.config']
        default_instance = profile_obj.search(
            [('is_valid_credential', '=', True), ('is_default', '=', True), ('is_active', '=', True), '|',
             ('company_ids', '=', False),
             ('company_ids', 'in', self._context.get('allowed_company_ids'))],
            limit=1)
        if default_instance:
            if not default_instance.company_ids:
                return default_instance.id
            elif default_instance.company_ids and default_instance.company_ids.ids in self._context.get('allowed_company_ids'):
                return default_instance.id
            else:
                return profile_obj.search(
                    [('is_valid_credential', '=', True), ('is_active', '=', True), '|', ('company_id', '=', False),
                     ('company_ids', 'in', self.env.company.ids)], limit=1).id
        else:
            return profile_obj.search(
                [('is_valid_credential', '=', True), ('is_active', '=', True), '|', ('company_ids', '=', False),
                 ('company_ids', 'in', self.env.company.ids)], limit=1).id

    def export_generic_method(self, sheet_name, columns):
        header_style = easyxf('font:height 200;pattern: pattern solid, fore_color gray25;'
                              'align: horiz center;font: color black; font:bold True;'
                              "borders: top thin,left thin,right thin,bottom thin")
        text_center = easyxf('font:height 200; align: horiz center;' "borders: top thin,bottom thin")
        workbook = xlwt.Workbook()
        worksheet = []
        worksheet.append(0)
        worksheet[0] = workbook.add_sheet(sheet_name)
        for j in range(len(columns)):
            worksheet[0].write(3, j + 1, columns[j], header_style)
            worksheet[0].col(j).width = 256 * 20
        return worksheet, workbook, header_style, text_center


class EBizChargeEMVDevice(models.Model):
    _name = 'ebizcharge.emv.device'
    _description = "EBizCharge EMV Devices"
    _order = 'sequence ASC'


    name = fields.Char(string="Device Name")
    sequence = fields.Integer(string="Sequnence")
    command = fields.Boolean(string="Set as Default")
    merchant_id = fields.Many2one("ebizcharge.instance.config", "Merchant Account", )
    pin = fields.Char(string="Pin")
    source_key = fields.Char(string="EMV Device Key")
    status = fields.Char(string="Status")
    key = fields.Char(string="Key")
    terminal_type = fields.Char(string="Terminal Type")
    apikeyid = fields.Char(string="API Key Id")
    enable_emv = fields.Char(string="Enable EMV")
    clientip = fields.Char(string="Client IP")
    is_default_emv = fields.Boolean(string="Set as Default")

    def action_delete(self):
        for line in self:
            if line.is_default_emv:
                for device in line.merchant_id.emv_device_ids:
                    if device.id!= line.id:
                        device.is_default_emv = True
                        break
            line.unlink()


    def open_edit(self):
        title = 'Edit EMV Device'
        emv_device = {
            'name': _('Configure Device'),
            'res_model': 'wizard.emv.device',
            'view_mode': 'form',
            'context': {
                'default_source_key': self.source_key,
                'default_merchant_id': self.merchant_id.id,
                'default_emv_device_id': self.id,
                'default_pin': self.pin,
                'default_is_default_emv': self.is_default_emv,
            },
            'target': 'new',
            'type': 'ir.actions.act_window',
        }
        return emv_device
        




    @api.depends('status')
    def _compute_display_name(self):
        for ddt in self:
            if ddt.is_default_emv:
                ddt.display_name = f"{ddt.name} ({ddt.status}) (Default)"
            else:
                ddt.display_name = f"{ddt.name} ({ddt.status})"

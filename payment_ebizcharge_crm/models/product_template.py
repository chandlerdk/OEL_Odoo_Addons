# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
from datetime import datetime

time_spam = False


class SyncProducts(models.Model):
    _inherit = 'product.template'

    def _default_instance_id(self):
        return self.env['ebizcharge.instance.config']._default_instance_id()

    def get_default_ebiz(self):
        profiles = self.env['ebizcharge.instance.config'].search(
            [('is_active', '=', True), '|', ('company_ids', '=', False),
             ('company_ids', 'in', self._context.get('allowed_company_ids'))]).ids
        return profiles

    def get_default_company(self):
        companies = self._context.get('allowed_company_ids')
        return companies

    ebiz_product_internal_id = fields.Char(string='Ebiz Product Internal ID', copy=False)
    ebiz_product_id = fields.Char(string='Ebiz Product ID', copy=False)
    sync_status = fields.Char(string='Sync Status', readonly=True, copy=False)
    last_sync_date = fields.Datetime(string="Upload Date & Time", copy=False)
    sync_response = fields.Char(string="Sync Response", copy=False)
    upload_status = fields.Char(string="EBizCharge Upload Status", compute="_compute_sync_status")
    ebiz_profile_id = fields.Many2one('ebizcharge.instance.config', copy=False, default=_default_instance_id)
    ebiz_profile_ids = fields.Many2many('ebizcharge.instance.config', compute='compute_ebiz_profiles',
                                        string="Profiles", default=get_default_ebiz)
    auto_sync_button = fields.Boolean(compute="_compute_button_auto_check", default=False)
    ebiz_company_ids = fields.Many2many('res.company', compute='compute_company', default=get_default_company)

    @api.depends('ebiz_profile_id', 'company_id')
    def compute_ebiz_profiles(self):
        profile_obj = self.env['ebizcharge.instance.config']
        if self.company_id:
            profiles = profile_obj.search(
                [('is_active', '=', True), ('is_default', '=', False), '|', ('company_ids', '=', False),
                 ('company_ids', 'in', self.company_id.ids)]).ids
        elif self.ebiz_profile_id:
            profiles = profile_obj.search(
                [('is_active', '=', True), '|', '|', ('id', '=', self.ebiz_profile_id.id), ('company_ids', '=', False),
                 ('company_ids', 'in',
                  self._context.get('allowed_company_ids'))])
        else:
            profiles = profile_obj.search(
                [('is_active', '=', True), '|', ('company_ids', '=', False),
                 ('company_ids', 'in', self._context.get('allowed_company_ids'))])

        self.ebiz_profile_ids = profiles

    @api.depends('ebiz_profile_id', 'company_id')
    def compute_company(self):
        profile_obj = self.env['ebizcharge.instance.config']
        if self.ebiz_profile_id and self.ebiz_profile_id.is_default:
            companies = []
        elif self.ebiz_profile_id:
            companies = self.ebiz_profile_id.company_ids.filtered(
                lambda i: i.id in self._context.get('allowed_company_ids')).ids
            if not self.ebiz_profile_id.company_ids:
                existing_companies = profile_obj.search([('is_active', '=', True)]).mapped('company_ids').ids
                companies = self.env['res.company'].search([('id', 'not in', existing_companies), (
                    'id', 'in', self._context.get('allowed_company_ids'))])
        elif self.company_id:
            companies = profile_obj.search(
                [('is_active', '=', True), '|', ('company_ids', '=', False),
                 ('company_ids', 'in', self.company_id.ids)]).mapped('company_ids').ids
        else:
            companies = self._context.get('allowed_company_ids')
        self.ebiz_company_ids = companies

    @api.onchange('ebiz_profile_id')
    def onchange_ebiz_profile(self):
        if len(self.ebiz_profile_id.company_ids) == 1:
            self.company_id = self.ebiz_profile_id.company_ids[0].id
        else:
            self.company_id = False

    @api.onchange('company_id')
    def onchange_ebiz_company(self):
        ebiz_profile = self.env['ebizcharge.instance.config'].search(
            [('is_active', '=', True), ('company_ids', 'in', self.company_id.ids)], limit=1)
        if ebiz_profile:
            self.ebiz_profile_id = ebiz_profile.id

    @api.depends('ebiz_product_internal_id')
    def _compute_sync_status(self):
        for order in self:
            order.upload_status = "Synchronized" if order.ebiz_product_internal_id else "Pending"

    @api.depends('ebiz_profile_id')
    def _compute_button_auto_check(self):
        ebiz_auto_sync_products = False
        if self.ebiz_profile_id:
            ebiz_auto_sync_products = self.ebiz_profile_id.ebiz_auto_sync_products
        self.auto_sync_button = ebiz_auto_sync_products

    def import_ebiz_products(self):
        """
        Niaz Implementation:
        Getting All EBiz Products to Odoo Products.
        Added button at random position(Product Form), further on the position will be set according to PM instructions
        """
        try:
            instance = False
            if self.ebiz_profile_id:
                instance = self.ebiz_profile_id

            security_key = instance.ebiz_security_key
            user_id = instance.ebiz_user_id
            password = instance.ebiz_password
            if not security_key or not user_id or not password:
                raise UserError(f'Dear "{self.env.user.name}," You Have Not Entered The EBiz Credentials!')

            if hasattr(self, 'website_id'):
                ebiz = self.get_ebiz_charge_obj(self.website_id.id, instance=instance)
            else:
                ebiz = self.get_ebiz_charge_obj(instance=instance)
            get_all_products = ebiz.client.service.SearchItems(**{
                'securityToken': {'SecurityId': security_key, 'UserId': user_id, 'Password': password},
                'start': 0,
                'limit': 1000000,
            })
            if get_all_products != None:
                product_obj = self.env['product.template']
                for product in get_all_products:
                    odoo_product = product_obj.search(
                        [('ebiz_product_internal_id', '=', product['ItemInternalId']),
                         ('ebiz_product_id', '=', product['ItemId'])])
                    if not odoo_product:
                        product_data = {
                            'name': product['Name'],
                            'description': product['Description'],
                            'list_price': product['UnitPrice'],
                            'type': 'service' if product['ItemType'] == 'Service' else 'consu' if product[
                                                                                                      'ItemType'] == 'inventory' else None,
                            'barcode': product['SKU'] if product['SKU'] != 'False' else '',
                            'ebiz_product_id': product['ItemId'] if product['ItemId'] else '',
                            'ebiz_product_internal_id': product['ItemInternalId'],
                        }
                        product_obj.create(product_data)
                        self.env.cr.commit()
                context = dict(self._context)
                context['message'] = 'Successful!'
                return self.message_wizard(context)
            else:
                raise UserError('No New Product To Import!')

        except UserError as e:
            raise UserError('No New Product To Import!')

        except Exception as e:
            raise UserError('Something Went Wrong!')

    def add_update_to_ebiz_ind(self):
        if not self.ebiz_profile_id:
            raise UserError('Please select EBizCharge Merchant Account.')
        return self.with_context({'message_bypass': True}).add_update_to_ebiz(self.id)

    def add_update_to_ebiz(self, list_of_products=None):
        """
            Niaz Implementation:
            Update Products
        """
        try:
            resp_lines = []
            resp_line = {}
            success = 0
            failed = 0
            if list_of_products:
                self = products_records = self.env['product.template'].browse(list_of_products).exists()
            else:
                list_of_products = self

            total = len(self)
            for product in self:
                if list_of_products:
                    reference_to_upload_product = self.env['list.of.products'].search(
                        [('product_id', '=', product.id)]) or False
                    if reference_to_upload_product:
                        reference_to_upload_product.last_sync_date = datetime.now()
                    else:
                        self.env['list.of.products'].create({
                            'product_name': product.id,
                            'sync_transaction_id': self.env['upload.products'].search([])[0].id,
                        })
                        reference_to_upload_product = self.env['list.of.products'].search(
                            [('product_id', '=', product.id)]) or False
                        if reference_to_upload_product:
                            reference_to_upload_product.last_sync_date = datetime.now()

                resp_line = {
                    'record_name': product.name
                }

                tax_amount = 0
                total_tax = 0
                if product['taxes_id']:
                    for tax_id in product['taxes_id']:
                        if tax_id['amount_type'] == 'percent':
                            tax_amount = (product['list_price'] * tax_id['amount']) / 100
                        elif tax_id['amount_type'] == 'fixed':
                            tax_amount = tax_id['amount']
                        elif tax_id['amount_type'] == 'group':
                            for child in tax_id.children_tax_ids:
                                if child['amount_type'] == 'percent':
                                    tax_amount += (product['list_price'] * child['amount']) / 100
                                elif child['amount_type'] == 'fixed':
                                    tax_amount += child['amount']
                        total_tax += tax_amount
                if product.ebiz_profile_id:
                    instance = product.ebiz_profile_id
                else:
                    instance = self.env['ebizcharge.instance.config'].search(
                        [('is_valid_credential', '=', True), ('is_default', '=', True)], limit=1)
                if not instance:
                    raise ValidationError('Please attach profile on product record or set one of profile to default.')
                ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
                if not product.ebiz_profile_id:
                    product.ebiz_profile_id = instance.id
                product_details = {
                    'Name': product['name'],
                    'Description': product['name'],
                    'UnitPrice': product['list_price'],
                    'UnitCost': product['standard_price'] if product['standard_price'] else product['list_price'],
                    'Active': True,
                    'ItemType': 'Service' if product['type'] == 'service' else 'inventory',
                    'SKU': product['barcode'] if product['barcode'] else '',
                    'Taxable': True if product['taxes_id'] else False,
                    'TaxRate': total_tax,
                    'ItemNotes': product['description'] if product['description'] else '',
                    'ItemId': product.id,
                    'SoftwareId': 'Odoo CRM',
                    'QtyOnHand': int(product.qty_available) if 'qty_available' in product else 0,
                }
                product_upload = self.env['upload.products'].search([], limit=1)
                if product.ebiz_product_internal_id and product.ebiz_product_id:
                    update = ebiz.client.service.UpdateItem(**{
                        'securityToken': ebiz._generate_security_json(),
                        'itemInternalId': product.ebiz_product_internal_id,
                        'itemId': product.ebiz_product_id,
                        'itemDetails': product_details,
                    })
                    resp_line['record_message'] = update['Error'] or update['Status']
                    product.sync_status = 'Success'
                    product.sync_response = update.Status
                    if product.sync_response == 'Success':
                        product.last_sync_date = datetime.now()
                        success += 1
                    else:
                        failed += 1

                    resp_lines.append([0, 0, resp_line])
                    if product:
                        self.env['logs.of.products'].create({
                            'product_name': product.id,
                            'sync_status': update.Status,
                            'last_sync_date': datetime.now(),
                            'sync_log_id': product_upload.id if product_upload else False,
                            'user_id': self.env.user.id,
                            'internal_reference': product.default_code,
                            'name': product.name,
                            'sales_price': product.list_price,
                            'cost': product.standard_price,
                            'quantity': product.qty_available,
                            'type': product.type,
                        })
                        product.sync_status = update.Status
                else:
                    create = ebiz.client.service.AddItem(**{
                        'securityToken': ebiz._generate_security_json(),
                        'itemDetails': product_details,
                    })
                    resp_line['record_message'] = create['Error'] or create['Status']
                    if create.Status == 'Success':
                        product.ebiz_product_internal_id = create['ItemInternalId']
                        product.ebiz_product_id = product.id
                        product.sync_status = 'Success'
                        product.sync_response = create.Status
                        product.last_sync_date = datetime.now()
                        success += 1
                        resp_lines.append([0, 0, resp_line])

                        if list_of_products:
                            self.env['logs.of.products'].create({
                                'product_name': product.id,
                                'sync_status': create.Status,
                                'last_sync_date': datetime.now(),
                                'sync_log_id': product_upload.id if product_upload else False,
                                'user_id': self.env.user.id,
                                'internal_reference': product.default_code,
                                'name': product.name,
                                'sales_price': product.list_price,
                                'cost': product.standard_price,
                                'quantity': product.qty_available,
                                'type': product.type,
                            })
                            reference_to_upload_product.sync_status = create.Status

                    elif create.Error == 'Record already exists':
                        create = ebiz.client.service.UpdateItem(**{
                            'securityToken': ebiz._generate_security_json(),
                            # 'itemInternalId': product.ebiz_product_internal_id,
                            'itemId': product.id,
                            'itemDetails': product_details,
                        })
                        resp_line['record_message'] = create['Error'] or create['Status']
                        product.sync_status = 'Success'
                        product.ebiz_product_internal_id = create['ItemInternalId']
                        product.ebiz_product_id = product.id
                        product.sync_response = create.Status
                        if product.sync_response == 'Success':
                            product.last_sync_date = datetime.now()
                            success += 1

                        else:
                            failed += 1
                        resp_lines.append([0, 0, resp_line])

                        if list_of_products:
                            self.env['logs.of.products'].create({
                                'product_name': product.id,
                                'sync_status': create.Status,
                                'last_sync_date': datetime.now(),
                                'sync_log_id': product_upload.id if product_upload else False,
                                'user_id': self.env.user.id,
                                'internal_reference': product.default_code,
                                'name': product.name,
                                'sales_price': product.list_price,
                                'cost': product.standard_price,
                                'quantity': product.qty_available,
                                'type': product.type,
                            })
                            reference_to_upload_product.sync_status = create.Status
                        else:
                            raise ValidationError(create.Error)
                    else:
                        failed += 1
                        resp_lines.append([0, 0, resp_line])

                        if list_of_products:
                            self.env['logs.of.products'].create({
                                'product_name': product.id,
                                'sync_status': create.Status,
                                'last_sync_date': datetime.now(),
                                'sync_log_id': product_upload.id if product_upload else False,
                                'user_id': self.env.user.id,
                                'internal_reference': product.default_code,
                                'name': product.name,
                                'sales_price': product.list_price,
                                'cost': product.standard_price,
                                'quantity': product.qty_available,
                                'type': product.type,
                            })
                            reference_to_upload_product.sync_status = create.Status

            if self.env.context.get('message_bypass'):
                context = dict(self._context)
                context['message'] = 'Product uploaded successfully!'
                return self.message_wizard(context)
            else:
                wizard = self.env['wizard.multi.sync.message'].create(
                    {'name': 'products', 'lines_ids': resp_lines,
                     'success_count': success, 'failed_count': failed, 'total': total})
                action = self.env.ref('payment_ebizcharge_crm.wizard_multi_sync_message_action').read()[0]
                action['context'] = self._context
                action['res_id'] = wizard.id
                return action

        except Exception as e:
            raise ValidationError(str(e))

    def process_products_bulk(self, list):
        product_records = self.env['product.template'].browse(list).exists()
        resp_lines = []
        success = 0
        failed = 0
        total = len(product_records)

        for product in product_records:
            resp_line = {
                'record_name': product.name
            }
            try:
                resp = product.sync_to_ebiz()
                resp_line['record_message'] = resp['Error'] or resp['Status']

            except Exception as e:
                resp_line['record_message'] = str(e)

            if resp_line['record_message'] == 'Success' or resp_line['record_message'] == 'Record already exists':
                success += 1
            else:
                failed += 1
            resp_lines.append([0, 0, resp_line])

        wizard = self.env['wizard.multi.sync.message'].create(
            {'name': 'products', 'lines_ids': resp_lines,
             'success_count': success, 'failed_count': failed, 'total': total})
        action = self.env.ref('payment_ebizcharge_crm.wizard_multi_sync_message_action').read()[0]
        action['context'] = self._context
        action['res_id'] = wizard.id
        return action

    def sync_to_ebiz(self):
        product_logs = self.env['logs.of.products']
        resp = self.add_update_to_ebiz_bulk(self)
        update_params = {
            'ebiz_product_internal_id': resp['ItemInternalId'],
            'ebiz_product_id': self.id
        }
        product_logs.create({
            'product_name': self.id,
            'sync_status': resp.Status,
            'last_sync_date': datetime.now(),
            'user_id': self.env.user.id,
            'internal_reference': self.default_code,
            'name': self.name,
            'sales_price': self.list_price,
            'cost': self.standard_price,
            'quantity': self.qty_available,
            'type': self.type,
        })

        update_params.update({'last_sync_date': fields.Datetime.now(),
                              'sync_response': 'Success' if resp['ErrorCode'] in [0, 2] else resp['Error'],
                              'sync_status': resp.Status})
        self.write(update_params)
        return resp

    def add_update_to_ebiz_bulk(self, product):
        """
            Niaz Implementation:
            Update Products
        """
        try:
            tax_amount = 0
            total_tax = 0
            if product['taxes_id']:
                for tax_id in product['taxes_id']:
                    if tax_id['amount_type'] == 'percent':
                        tax_amount = (product['list_price'] * tax_id['amount']) / 100
                    elif tax_id['amount_type'] == 'fixed':
                        tax_amount = tax_id['amount']
                    elif tax_id['amount_type'] == 'group':
                        for child in tax_id.children_tax_ids:
                            if child['amount_type'] == 'percent':
                                tax_amount += (product['list_price'] * child['amount']) / 100
                            elif child['amount_type'] == 'fixed':
                                tax_amount += child['amount']
                    total_tax += tax_amount
            if product.ebiz_profile_id:
                instance = product.ebiz_profile_id
            else:
                instance = self.env['ebizcharge.instance.config'].search(
                    [('is_valid_credential', '=', True), ('is_default', '=', True)], limit=1)
            if not instance:
                raise ValidationError('Please attach profile on product record or set one of profile to default.')
            ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
            if not product.ebiz_profile_id:
                product.ebiz_profile_id = instance.id
            product_details = {
                'Name': product['name'],
                'Description': product['name'],
                'UnitPrice': product['list_price'],
                'UnitCost': product['standard_price'] if product['standard_price'] else product['list_price'],
                'Active': True,
                'ItemType': 'Service' if product['type'] == 'service' else 'inventory',
                'SKU': product['barcode'] if product['barcode'] else '',
                'Taxable': True if product['taxes_id'] else False,
                'TaxRate': total_tax,
                'ItemNotes': product['description'] if product['description'] else '',
                'ItemId': product.id,
                'SoftwareId': 'Odoo CRM',
                'QtyOnHand': int(product.qty_available) if 'qty_available' in product else 0,
            }
            if product.ebiz_product_internal_id and product.ebiz_product_id:
                resp = ebiz.client.service.UpdateItem(**{
                    'securityToken': ebiz._generate_security_json(),
                    'itemInternalId': product.ebiz_product_internal_id,
                    'itemId': product.ebiz_product_id,
                    'itemDetails': product_details,
                })
                return resp
            else:
                resp = ebiz.client.service.AddItem(**{
                    'securityToken': ebiz._generate_security_json(),
                    'itemDetails': product_details,
                })
                if resp.Error == 'Record already exists':
                    resp = ebiz.client.service.UpdateItem(**{
                        'securityToken': ebiz._generate_security_json(),
                        'itemId': product.id,
                        'itemDetails': product_details,
                    })
                    return resp
                else:
                    return resp

        except Exception as e:
            raise ValidationError(str(e))

    def message_wizard(self, context):
        """
            Niaz Implementation:
            Generic Function for successful message indication for the user to enhance user experience
            param: Message string will be passed to context
            return: wizard
        """
        return {
            'name': 'Success',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'message.wizard',
            'view_id': False,
            'type': 'ir.actions.act_window',
            'target': 'new',
            'context': context
        }

    @api.model_create_multi
    def create(self, val_list):
        products = super(SyncProducts, self).create(val_list)
        for product in products:
            if product.ebiz_profile_id and product.ebiz_profile_id.ebiz_auto_sync_products:
                product.add_update_to_ebiz()
        return products

    def write(self, values):
        product = super(SyncProducts, self).write(values)
        for item in self:
            if item.ebiz_product_internal_id and 'ebiz_product_internal_id' not in values and 'ebiz_product_id' not in \
                    values and 'last_sync_date' not in values and 'sync_response' not in values and 'sync_status' not in values:
                item.add_update_to_ebiz()
                return product
            else:
                return product
        return product


class SyncQuantity(models.Model):
    _inherit = 'stock.quant'

    @api.model_create_multi
    def create(self, values):
        record = super(SyncQuantity, self).create(values)
        product_upload = self.env['upload.products'].search([], limit=1)
        for rec in record:
            if rec.product_tmpl_id.ebiz_profile_id and rec.product_id.ebiz_product_internal_id and rec.product_id.ebiz_product_id:
                instance = None
                if rec.product_id.ebiz_profile_id:
                    instance = rec.product_id.ebiz_profile_id

                ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)
                update = ebiz.client.service.UpdateItem(**{
                    'securityToken': ebiz._generate_security_json(),
                    'itemInternalId': rec.product_id.ebiz_product_internal_id,
                    'itemId': rec.product_id.ebiz_product_id,
                    'itemDetails': {
                        'QtyOnHand': rec.quantity if 'qty_available' in rec.product_id else 0,
                    },
                })
                self.env['logs.of.products'].create({
                    'product_name': rec.product_id.product_tmpl_id.id,
                    'sync_status': update.Status,
                    'last_sync_date': datetime.now(),
                    'sync_log_id': product_upload.id if product_upload else False,
                    'user_id': self.env.user.id,
                    'internal_reference': rec.product_id.default_code,
                    'name': rec.product_id.name,
                    'sales_price': rec.product_id.list_price,
                    'cost': rec.product_id.standard_price,
                    'quantity': rec.product_id.qty_available,
                    'type': rec.product_id.type,
                })
        return record

    def write(self, values):
        record = super(SyncQuantity, self).write(values)
        for rec in self:
            if rec.product_tmpl_id.ebiz_profile_id:
                product = rec.product_id
                product_upload = self.env['upload.products'].search([], limit=1)
                if rec.product_id.ebiz_product_internal_id and rec.product_id.ebiz_product_id and 'inventory_quantity' in values:
                    instance = None
                    if rec.product_id.ebiz_profile_id:
                        instance = rec.product_id.ebiz_profile_id

                    ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)

                    update = ebiz.client.service.UpdateItem(**{
                        'securityToken': ebiz._generate_security_json(),
                        'itemInternalId': product.ebiz_product_internal_id,
                        'itemId': product.ebiz_product_id,
                        'itemDetails': {
                            'QtyOnHand': rec.quantity if 'qty_available' in product else 0,
                        },
                    })
                    self.env['logs.of.products'].create({
                        'product_name': product.product_tmpl_id.id,
                        'sync_status': update.Status,
                        'last_sync_date': datetime.now(),
                        'sync_log_id': product_upload.id if product_upload else False,
                        'user_id': self.env.user.id,
                        'internal_reference': product.default_code,
                        'name': product.name,
                        'sales_price': product.list_price,
                        'cost': product.standard_price,
                        'quantity': product.qty_available,
                        'type': product.type,
                    })

        return record

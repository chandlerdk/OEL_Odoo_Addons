# -*- coding: utf-8 -*-

from odoo import models
from .ebiz_charge import EBizChargeAPI
from odoo.exceptions import UserError


class EBizChargeApi(models.AbstractModel):
    _name = "ebiz.charge.api"
    _description = "EBizCharge Api"

    """
    This model is inherited by all the model which will be integrate with EBizCharge
    """

    def get_ebiz_charge_obj(self, website_id=None, instance=None):
        """
        Kuldeep's implementation
        Initialize the EBizCharge object this
        """
        if instance and not instance.is_active:
            raise UserError(f'Dear {self.env.user.name}, Merchant Account attached to this customer is not active.')

        if instance:
            credentials = self.get_crm_credentials(instance)
        else:
            credentials = self.get_website_instance_credentials(website_id)
        if not credentials[0] and 'login' not in self._context:
            if self.env.user.has_group('base.group_portal'):
                raise UserError(f'Dear "{self.env.user.name},"You are Not Allowed to process EBizCharge '
                                f'Payment.Please contact Administrator!"')
            else:
                raise UserError(f'Dear "{self.env.user.name},"You Have Not Entered The EBiz Credentials!')

        ebiz = EBizChargeAPI(*credentials)
        return ebiz

    def get_crm_credentials(self, instance):
        if instance and instance.ebiz_security_key and instance.ebiz_user_id and instance.ebiz_password:
            security_key = instance.ebiz_security_key
            user_id = instance.ebiz_user_id
            password = instance.ebiz_password
            return security_key, user_id, password
        else:
            return None, None, None

    def get_website_instance_credentials(self, website_id):
        web = self.env['ir.module.module'].sudo().search(
            [('name', '=', 'website_sale'), ('state', 'in', ['installed', 'to upgrade', 'to remove'])])
        if web and website_id:
            website_id = website_id if type(website_id) == int else website_id.id
            website = self.env['website'].sudo().browse(website_id)

            if website:
                instance = self.env['ebizcharge.instance.config'].sudo().search(
                    [('website_ids', 'in', website.ids), ('is_active', '=', True), ('is_website', '=', True)])
                if instance:
                    return instance.ebiz_security_key, instance.ebiz_user_id, instance.ebiz_password
                else:
                    return None, None, None
            else:
                return None, None, None
        else:
            return None, None, None

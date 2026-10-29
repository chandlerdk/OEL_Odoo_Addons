# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

from odoo import models, fields


class CreatePartnerDeliveryAddress(models.TransientModel):
    _name = 'create.partner.delivery.address.ept'
    _description = 'Wizard to add new delivery partner'

    street = fields.Char()
    street2 = fields.Char()
    state_id = fields.Many2one(
        'res.country.state', string='State', ondelete='restrict')
    city = fields.Char()
    zip = fields.Char(change_default=True)
    country_id = fields.Many2one(
        'res.country', string='Country', ondelete='restrict')
    name = fields.Char(string="Contact Name")
    phone = fields.Char()
    email = fields.Char()
    comment = fields.Char(string="Note")
    mobile = fields.Char()

    def create_new_contact_partner(self):
        """
        This method used for create new contact partner and write new partner in RMA record
        delivery field
        """
        context = self._context
        record = context.get('record')
        partner = self.env['res.partner']
        rma_id = self.env['crm.claim.ept'].browse(record)

        value = self.prepare_res_partner_values(context)
        new_partner_id = partner.create(value)

        is_contact_person = context.get("is_create_contact_person")

        if is_contact_person:
            new_partner_id.type = 'contact'
            rma_id.write({'rma_support_person_id': new_partner_id.id})
        else:
            new_partner_id.type = 'delivery'
            rma_id.write({'partner_delivery_id': new_partner_id.id})

        return True

    def prepare_res_partner_values(self, context):
        """prepare values for partner"""
        return {
            'name': self.name,
            'phone': self.phone,
            'email': self.email,
            'street': self.street,
            'street2': self.street2,
            'state_id': self.state_id.id,
            'city': self.city,
            'zip': self.zip,
            'country_id': self.country_id.id,
            'parent_id': context.get('current_partner_id'),
        }

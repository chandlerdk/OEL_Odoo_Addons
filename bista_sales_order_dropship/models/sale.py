# -*- coding: utf-8 -*-
###############################################################################
#    License, author and contributors information in:                         #
#    __manifest__.py file at the root folder of this module.                  #
###############################################################################

from odoo import api, fields, models
from odoo.fields import Domain


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def _get_dropship_address_domain(self, address_type):
        """Restrict the address choices to the customer (default_parent_id) and its
        child contacts of the given type."""
        partner_id = self.env.context.get('default_parent_id')
        if not partner_id:
            return Domain('id', 'in', [])
        partner = self.browse(partner_id)
        addresses = partner.child_ids.filtered(lambda p: p.type == address_type)
        if partner.type == address_type:
            addresses |= partner
        return Domain('id', 'in', (addresses or partner).ids)

    def _get_dropship_filter_domain(self, domain):
        context = self.env.context
        domain = Domain(domain or Domain.TRUE)
        if context.get('dropship_delivery_ids_filter') and not context.get('allow_do_dropship'):
            domain &= self._get_dropship_address_domain('delivery')
        if context.get('dropship_invoice_ids_filter'):
            domain &= self._get_dropship_address_domain('invoice')
        return domain

    @api.model
    def name_search(self, name='', domain=None, operator='ilike', limit=100):
        return super().name_search(name, self._get_dropship_filter_domain(domain), operator, limit)

    @api.model
    @api.readonly
    def web_search_read(self, domain, specification, offset=0, limit=None, order=None, count_limit=None):
        return super().web_search_read(self._get_dropship_filter_domain(domain), specification,
                                       offset=offset, limit=limit, order=order, count_limit=count_limit)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    allow_do_dropship = fields.Boolean(string="Drop Ship", default=False)


# -*- coding: utf-8 -*-
###############################################################################
#    License, author and contributors information in:                         #
#    __manifest__.py file at the root folder of this module.                  #
###############################################################################



from odoo import models, fields, api, _
from odoo.osv import expression

class ResPartner(models.Model):
    _inherit = 'res.partner'

    def _get_dropship_delivery_filter(self, args, context={}):
        context = dict(self._context) or {}
        dropship_delivery_ids_filter = context.get('dropship_delivery_ids_filter', False)
        allow_do_dropship = context.get('allow_do_dropship', False)
        partner_id = context.get('default_parent_id', False)

        if not dropship_delivery_ids_filter:
            return args

        if allow_do_dropship:
            return args

        if partner_id:
            partner = self.browse(partner_id)
            if not allow_do_dropship:
                partner_delivery_ids = partner.child_ids.filtered(lambda p: p.type == 'delivery')
                if partner.type == 'delivery':
                    partner_delivery_ids |= partner
                if partner_delivery_ids:
                    args = expression.AND([[('id', 'in', partner_delivery_ids.ids)], args])
                else:
                    args = expression.AND([[('id', 'in', [partner.id])], args])
        else:
            args = expression.AND([[('id', 'in', [])], args])

        return args

    def _get_dropship_invoice_filter(self, args, context={}):
        context = dict(self._context) or {}
        dropship_invoice_ids_filter = context.get('dropship_invoice_ids_filter', False)
        partner_id = context.get('default_parent_id', False)

        if not dropship_invoice_ids_filter:
            return args

        if partner_id:
            partner = self.browse(partner_id)
            partner_invoice_ids = partner.child_ids.filtered(lambda p: p.type == 'invoice')
            if partner.type == 'invoice':
                partner_invoice_ids |= partner_id
            if partner_invoice_ids:
                args = expression.AND([[('id', 'in', partner_invoice_ids.ids)], args])
            else:
                args = expression.AND([[('id', 'in', [partner.id])], args])
        else:
            args = expression.AND([[('id', 'in', [])], args])

        return args

    @api.model
    def search_read(self, domain=None, fields=None, offset=0, limit=None, order=None):
        context = dict(self._context) or {}
        domain = self._get_dropship_delivery_filter(domain, context=context)
        domain = self._get_dropship_invoice_filter(domain, context=context)
        return super().search_read(domain=domain, fields=fields, offset=offset, limit=limit, order=order)

    @api.model
    def _name_search(self, name, domain=None, operator='ilike', limit=100, order=None):
        domain = domain or []
        context = dict(self._context) or {}
        domain = self._get_dropship_delivery_filter(domain, context=context)
        domain = self._get_dropship_invoice_filter(domain, context=context)
        return super(ResPartner, self)._name_search(name=name, domain=domain, operator=operator, limit=limit, order=order)

    # @api.model
    # def create(self, vals):
    #     context = dict(self._context) or {}
    #     parent_id = context.get('default_parent_id', False)
    #     if 'type' in vals and vals['type'] == 'invoice' and parent_id:
    #         vals['parent_id'] = parent_id
    #     return super(ResPartner, self).create(vals)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    allow_do_dropship = fields.Boolean(string="Drop Ship", default=False)


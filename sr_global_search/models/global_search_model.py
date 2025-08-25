# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class GlobalSearchField(models.Model):
    _name = 'global.search.field'
    _description = 'Cached Fields for Global Search'

    name = fields.Char('Field Name', required=True)
    model_id = fields.Many2one('ir.model', 'Model', required=True, ondelete='cascade')
    field_id = fields.Many2one('ir.model.fields', 'Original Field', required=True, ondelete='cascade')
    ttype = fields.Char('Field Type')

    @api.model
    def update_search_fields_cache(self):
        """Update the cache of searchable fields"""
        # Clear existing cache
        self.search([]).unlink()
        # Filter fields from ir.model.fields
        domain = [('ttype', 'in', ['char', 'text', 'many2one'])]
        fields = self.env['ir.model.fields'].search(domain)
        for field in fields:
            self.create({
                'name': field.name,
                'model_id': field.model_id.id,
                'field_id': field.id,
                'ttype': field.ttype,
            })


class GlobalSearchModel(models.Model):
    _name = 'global.search.model'
    _description = 'Models for Global Search'

    model_id = fields.Many2one('ir.model', 'Model', ondelete='cascade')
    field_ids = fields.Many2many('global.search.field', string='Fields')

    def search_records(self, terms=''):
        search_model_ids = self.search([])
        contacts = []
        sales_orders = []
        purchase_orders = []
        others = []
        if not terms:
            return []
        for rec in search_model_ids:
            domain = []
            for field in rec.field_ids:
                if not domain:
                    domain = [(field.name, 'ilike', terms)]
                else:
                    domain = ['|'] + domain + [(field.name, 'ilike', terms)]
            try:
                searched_records = self.env[rec.model_id.model].search(domain)
                if searched_records:
                    for s_rec in searched_records:
                        result = {
                            'label': s_rec.display_name,
                            'resId': s_rec.id,
                            'model': rec.model_id.model,
                            'model_name': rec.model_id.name
                        }
                        if rec.model_id.model == 'res.partner':
                            result['is_company'] = getattr(s_rec, 'is_company', False)
                            if result['is_company']:
                                result['icon'] = 'fa fa-building'
                            else:
                                result['icon'] = 'fa fa-user'
                            contacts.append(result)
                        elif rec.model_id.model == 'sale.order':
                            result['icon'] = 'fa fa-file-text-o'
                            sales_orders.append(result)
                        elif rec.model_id.model == 'purchase.order':
                            result['icon'] = 'fa fa-shopping-bag'
                            purchase_orders.append(result)
                        else:
                            others.append(result)
            except Exception as e:
                continue

        data = []
        if contacts:
            data.append({'label': 'Contacts'})
            # Companies first, then other contacts
            contacts = sorted(contacts, key=lambda x: (not x.get('is_company', False), x.get('label', '')))
            data.extend(contacts)
        if sales_orders:
            data.append({'label': 'Sales Orders'})
            data.extend(sales_orders)
        if purchase_orders:
            data.append({'label': 'Purchase Orders'})
            data.extend(purchase_orders)
        if others:
            data.append({'label': 'Other'})
            data.extend(others)
        return data

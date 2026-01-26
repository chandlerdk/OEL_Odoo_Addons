from odoo import fields, models, api, _
from odoo.exceptions import UserError, ValidationError
import logging
from datetime import datetime, timedelta
from dateutil import relativedelta


class CardExpiringSoon(models.TransientModel):
    _name = 'wizard.cards.expiring.soon'
    _description = "Wizard Cards Expiring Soon"

    date_selection = fields.Selection([
        ('this_month', 'This month'),
        ('next_month', 'Next month'),
        ('within_3_month', 'Within 3 months'),
        ('within_6_month', 'Within 6 months'),
        ('within_a_year', 'Within a year'),
    ], string='Display customers with saved card(s) expiring', help="Select specific time you'd like to display", default='this_month')

    no_of_days = fields.Integer('Within days')

    def apply_filters(self):
        try:
            self.env["list.ebiz.customers"].search([]).unlink()
            instances = self.env['ebizcharge.instance.config'].browse(self.env.context.get('profiles'))
            for instance in instances:
                list_of_dict = []
                filters_list = []
                ebiz = self.env['ebiz.charge.api'].get_ebiz_charge_obj(instance=instance)

                if self.date_selection == 'this_month':
                    filters_list.append(
                        {'FieldName': 'ExpireThisMonthCreditCardsCount', 'ComparisonOperator': 'gt', 'FieldValue': 0})

                elif self.date_selection == 'next_month':
                    filters_list.append(
                        {'FieldName': 'ExpireNextMonthCreditCardsCount', 'ComparisonOperator': 'gt', 'FieldValue': 0})

                elif self.date_selection == 'within_3_month':
                    filters_list.append(
                        {'FieldName': 'ExpireWithin3MonthCreditCardsCount', 'ComparisonOperator': 'gt', 'FieldValue': 0})

                elif self.date_selection == 'within_6_month':
                    filters_list.append(
                        {'FieldName': 'ExpireWithin6MonthCreditCardsCount', 'ComparisonOperator': 'gt', 'FieldValue': 0})

                elif self.date_selection == 'within_a_year':
                    filters_list.append(
                        {'FieldName': 'ExpireWithinaYearCreditCardsCount', 'ComparisonOperator': 'gt', 'FieldValue': 0})

                elif self.date_selection == 'specific_days':
                    filters_list.append(
                        {'FieldName': 'ExpireWithinaYearCreditCardsCount', 'ComparisonOperator': 'gt', 'FieldValue': 0})

                else:
                    raise UserError('No option selected!')

                params = {
                    'securityToken': ebiz._generate_security_json(),
                    'filters': {"SearchFilter": filters_list},
                    'countOnly': False,
                    'start': 0,
                    'limit': 100000,
                    'sort': 'DateTime'
                }
                cards = ebiz.client.service.GetCardsExpirationList(**params)['CardExpirationCountsList']
                if cards:
                    cards_lists = cards['CardExpirationCounts']

                    for card in cards_lists:
                        try:
                            local_customer = self.env['res.partner'].search([('ebiz_internal_id', '!=', False),
                                                                             ('ebiz_internal_id', '=',
                                                                              card['CustomerInformation'][
                                                                                  'CustomerInternalId'])])
                        except Exception as e:
                            continue

                        if local_customer:
                            list_of_dict.append({
                                'customer_name': local_customer[0].id,
                                'customer_id': card['CustomerInformation']['CustomerId'] or '',
                                'email_id': card['CustomerInformation']['Email'] or '',
                                'customer_phone': card['CustomerInformation']['Phone'] or '',
                                'customer_city': card['CustomerInformation']['BillingAddress'] or
                                                 card['CustomerInformation']['ShippingAddress'] or '',
                                'sync_transaction_id': self.env['payment.method.ui'].search([])[-1].id,
                            })

                    if list_of_dict:
                        self.env['list.ebiz.customers'].sudo().create(list_of_dict)

        except Exception as e:
            raise ValidationError(e)

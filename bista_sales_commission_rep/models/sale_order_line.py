# -*- coding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (https://www.bistasolutions.com)
#
##############################################################################

from odoo import api, fields, models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    sale_rep_id = fields.Many2one('res.partner', related="order_id.sale_rep_id", store=True)

    manual_commission = fields.Boolean(string="Manual C% Man Amount",copy=False)
    manual_in_commission = fields.Boolean(string="Manual C% In Amount",copy=False)
    manual_out_commission = fields.Boolean(string="Manual C% Out Amount",copy=False)

    def _get_generic_commission(self):
        return self.env['sale.commission'].search([('name', '=', 'Generic Commission')], limit=1)

    def _apply_manual_percent_to_commission(self, percent_field, amount_field, commission_field, sale_partner_type):
        """Recalculate one commission slot after a manual % edit on a sale order line."""
        self.ensure_one()
        percentage = self[percent_field] or 0.0
        if not percentage:
            self[commission_field] = False
            self[amount_field] = 0.0
            return

        sale_commission = self.env['sale.commission']
        data = {
            'product_id': self.product_id,
            'partner_id': self.order_id.partner_id,
            'quantity': self.product_uom_qty,
            'amount_after_tax': self.price_total,
            'amount_before_tax': self.price_subtotal,
            'percentage': percentage,
        }

        rules = sale_commission.browse()
        if sale_partner_type == 'sale_rep' and self.sale_rep_id:
            domain = [('sale_rep_id', '=', self.sale_rep_id.id), ('sale_partner_type', '=', 'sale_rep')]
            if self.product_id.detailed_type == 'service':
                domain += [('product_ids', 'in', self.product_id.id), ('product_ids.detailed_type', '=', 'service')]
            rules = sale_commission.search(domain, order='sequence')
        elif sale_partner_type == 'user':
            user = self.order_id.user_id
            domain = [('user_ids', 'in', user.id), ('sale_partner_type', '=', 'user')] if user else []
            if domain and self.product_id.detailed_type == 'service':
                domain += [('product_ids', 'in', self.product_id.id), ('product_ids.detailed_type', '=', 'service')]
            rules = sale_commission.search(domain, order='sequence') if domain else sale_commission.browse()
        elif sale_partner_type == 'sale_team' and self.order_id.team_id and self.order_id.team_id.user_id:
            domain = [('sale_team_rep', '=', self.order_id.team_id.user_id.id), ('sale_partner_type', '=', 'sale_team')]
            if self.product_id.detailed_type == 'service':
                domain += [('product_ids', 'in', self.product_id.id), ('product_ids.detailed_type', '=', 'service')]
            rules = sale_commission.search(domain, order='sequence')

        for rule in rules:
            data['percentage'] = percentage
            amount = rule.calculate_amount(data)
            if amount is not None:
                self[commission_field] = rule.id
                self[amount_field] = amount
                return

        generic = self._get_generic_commission()
        if generic:
            data['percentage'] = percentage
            amount = generic.calculate_amount(data)
            self[commission_field] = generic.id
            self[amount_field] = amount or 0.0
        else:
            self[commission_field] = False
            self[amount_field] = (percentage * (self.price_subtotal or 0.0)) / 100.0

    @api.onchange('commission_percent')
    def _onchange_commission_percent(self):
        for line in self:
            line.manual_commission = True
            line._apply_manual_percent_to_commission(
                'commission_percent', 'commission_amount', 'commission_id', 'sale_rep'
            )

    @api.onchange('in_commission_percent')
    def _onchange_in_commission_percent(self):
        for line in self:
            line.manual_in_commission = True
            line._apply_manual_percent_to_commission(
                'in_commission_percent', 'in_commission_amount', 'in_commission_id', 'user'
            )

    @api.onchange('out_commission_percent')
    def _onchange_out_commission_percent(self):
        for line in self:
            line.manual_out_commission = True
            line._apply_manual_percent_to_commission(
                'out_commission_percent', 'out_commission_amount', 'out_commission_id', 'sale_team'
            )

    @api.depends("user_id",
                 "sale_rep_id",
                 "team_id",
                 "price_total",
                 "order_id.partner_id",
                 "product_id")
    def _compute_commission_amount(self):
        for line in self:
            data = {
                'product_id': line.product_id,
                'partner_id': line.order_id.partner_id,
                'quantity': line.product_uom_qty,
                'amount_after_tax': line.price_total,
                'amount_before_tax': line.price_subtotal,
                'percentage': 0

            }
            # if not line.validate_commission_rule():
            #     continue
            #
            # if line.commission_id and line.commission_percent != line.commission_id.percentage:
            #     # Update only Commission Amount
            #     # Assuming the Difference between same commission object
            #     # and percentage is due to manual user interference
            #     data['percentage'] = line.commission_percent
            #     line.commission_amount = line.commission_id.calculate_amount(data)
            #     continue
            rules = []
            sale_commission = self.env['sale.commission']
            if line.product_id.detailed_type == 'service':
                rep_rules = sale_commission.search(
                    [('sale_rep_id', '=', line.sale_rep_id.id), ('sale_partner_type', '=', 'sale_rep'),
                     ('product_ids', 'in', line.product_id.id),('product_ids.detailed_type','=','service')],
                    order='sequence') if line.sale_rep_id else sale_commission.browse()
                user_rules = sale_commission.search(
                    [('user_ids', 'in', line.user_id.id), ('sale_partner_type', '=', 'user'),
                    ('product_ids', 'in', line.product_id.id), ('product_ids.detailed_type', '=', 'service')
                     ],
                    order='sequence') if line.user_id else sale_commission.browse()
                team_rules = sale_commission.search(
                    [('sale_team_rep', '=', line.team_id.user_id.id), ('sale_partner_type', '=', 'sale_team'),
                     ('product_ids', 'in', line.product_id.id), ('product_ids.detailed_type', '=', 'service')
                     ],
                    order='sequence') if line.team_id else sale_commission.browse()

                for rule in rep_rules:
                    data['percentage'] = rule.percentage
                    amount = rule.calculate_amount(data)
                    if amount is not None:
                        line.commission_amount = amount
                        line.commission_id = rule.id if rule else False
                        line.commission_percent = rule.percentage
                        break
                else:
                    if line.manual_commission and line.commission_percent:
                        generic = line._get_generic_commission()
                        data['percentage'] = line.commission_percent
                        if generic:
                            amount = generic.calculate_amount(data)
                        else:
                            amount = 0.0
                        line.commission_id = generic.id if generic else False
                        line.commission_amount = amount
                    else:
                        line.commission_percent = 0.0
                        line.commission_id = False
                        line.commission_amount = 0.0
                    # ================= USER COMMISSION =================
                for user_rule in user_rules:
                    data['percentage'] = user_rule.percentage
                    amount = user_rule.calculate_amount(data)
                    if amount is not None:
                        line.in_commission_id = user_rule.id if user_rule else False
                        line.in_commission_percent = user_rule.percentage
                        line.in_commission_amount = amount
                        break
                else:
                    if line.manual_in_commission and line.in_commission_percent:
                        generic = line._get_generic_commission()
                        data['percentage'] = line.in_commission_percent
                        if generic:
                            amount = generic.calculate_amount(data)
                        else:
                            amount = 0.0
                        line.in_commission_id = generic.id if generic else False
                        line.in_commission_amount = amount
                    else:
                        line.in_commission_percent = 0
                        line.in_commission_id = False
                        line.in_commission_amount = 0.0

                    # ================= TEAM COMMISSION =================
                for team_rule in team_rules:
                    data['percentage'] = team_rule.percentage
                    amount = team_rule.calculate_amount(data)
                    if amount is not None:
                        line.out_commission_id = team_rule.id if team_rule else False
                        line.out_commission_percent = team_rule.percentage
                        line.out_commission_amount = amount
                        break
                else:
                    if line.manual_out_commission and line.out_commission_percent:
                        generic = line._get_generic_commission()
                        data['percentage'] = line.out_commission_percent
                        if generic:
                            amount = generic.calculate_amount(data)
                        else:
                            amount = 0.0
                        line.out_commission_id = generic.id if generic else False
                        line.out_commission_amount = amount
                    else:
                        line.out_commission_percent = 0
                        line.out_commission_id = False
                        line.out_commission_amount = 0.0

            else:
                rep_rules = sale_commission.search([('sale_rep_id', '=', line.sale_rep_id.id),('sale_partner_type','=','sale_rep')],
                                                   order='sequence') if line.sale_rep_id else sale_commission.browse()
                user_rules = sale_commission.search([('user_ids', 'in', line.user_id.id),('sale_partner_type','=','user')],
                                                    order='sequence') if line.user_id else sale_commission.browse()
                team_rules = sale_commission.search([('sale_team_rep', '=', line.team_id.user_id.id),('sale_partner_type','=','sale_team')],
                                                        order='sequence') if line.team_id else sale_commission.browse()
                for rule in rep_rules:
                    data['percentage'] = rule.percentage
                    amount = rule.calculate_amount(data)
                    if amount is not None:
                        line.commission_amount = amount
                        line.commission_id = rule.id if rule else False
                        line.commission_percent = rule.percentage
                        break
                else:
                    if line.manual_commission and line.commission_percent:
                        generic = line._get_generic_commission()
                        data['percentage'] = line.commission_percent
                        if generic:
                            amount = generic.calculate_amount(data)
                        else:
                            amount = 0.0
                        line.commission_id = generic.id if generic else False
                        line.commission_amount = amount
                    else:
                        line.commission_percent = 0.0
                        line.commission_id = False
                        line.commission_amount = 0.0
                        # ================= USER COMMISSION =================
                for user_rule in user_rules:
                    data['percentage'] = user_rule.percentage
                    amount = user_rule.calculate_amount(data)
                    if amount is not None:
                        line.in_commission_id = user_rule.id if user_rule else False
                        line.in_commission_percent = user_rule.percentage
                        line.in_commission_amount = amount
                        break
                else:
                    if line.manual_in_commission and line.in_commission_percent:
                        generic = line._get_generic_commission()
                        data['percentage'] = line.in_commission_percent
                        if generic:
                            amount = generic.calculate_amount(data)
                        else:
                            amount = 0.0
                        line.in_commission_id = generic.id if generic else False
                        line.in_commission_amount = amount
                    else:
                        line.in_commission_percent = 0
                        line.in_commission_id = False
                        line.in_commission_amount = 0.0

                        # ================= TEAM COMMISSION =================
                for team_rule in team_rules:
                    data['percentage'] = team_rule.percentage
                    amount = team_rule.calculate_amount(data)
                    if amount is not None:
                        line.out_commission_id = team_rule.id if team_rule else False
                        line.out_commission_percent = team_rule.percentage
                        line.out_commission_amount = amount
                        break
                else:
                    if line.manual_out_commission and line.out_commission_percent:
                        generic = line._get_generic_commission()
                        data['percentage'] = line.out_commission_percent
                        if generic:
                            amount = generic.calculate_amount(data)
                        else:
                            amount = 0.0
                        line.out_commission_id = generic.id if generic else False
                        line.out_commission_amount = amount
                    else:
                        line.out_commission_percent = 0
                        line.out_commission_id = False
                        line.out_commission_amount = 0.0

    def _prepare_invoice_line(self, **optional_values):
        res = super()._prepare_invoice_line(**optional_values)
        res.update({
            'commission_percent': self.commission_percent,
            'in_commission_percent': self.in_commission_percent,
            'out_commission_percent': self.out_commission_percent,
            'manual_commission': self.manual_commission,
            'manual_in_commission': self.manual_in_commission,
            'manual_out_commission': self.manual_out_commission,
            'commission_id': self.commission_id.id,
            'in_commission_id': self.in_commission_id.id,
            'out_commission_id': self.out_commission_id.id,
            'commission_amount':self.commission_amount,
            'in_commission_amount':self.in_commission_amount,
            'out_commission_amount':self.out_commission_amount,
        })
        return res
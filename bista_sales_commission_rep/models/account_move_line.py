# -*- coding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (https://www.bistasolutions.com)
#
##############################################################################

from odoo import api, fields, models

class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    sale_rep_id = fields.Many2one('res.partner', string='Sale Rep', related="move_id.sale_rep_id")
    manual_commission = fields.Boolean(string="Manual C% Man", copy=False)
    manual_in_commission = fields.Boolean(string="Manual C% In", copy=False)
    manual_out_commission = fields.Boolean(string="Manual C% Out", copy=False)

    def _get_generic_commission(self):
        return self.env['sale.commission'].search([('name', '=', 'Generic Commission')], limit=1)

    def _apply_manual_percent_to_commission(self, percent_field, amount_field, commission_field, sale_partner_type):
        """Recalculate one commission slot after a manual % edit on a draft invoice line."""
        self.ensure_one()
        percentage = self[percent_field] or 0.0
        if not percentage:
            self[commission_field] = False
            self[amount_field] = 0.0
            return

        sale_commission = self.env['sale.commission']
        data = self._get_commission_calc_data(False)
        data['percentage'] = percentage

        rules = sale_commission.browse()
        if sale_partner_type == 'sale_rep' and self.sale_rep_id:
            domain = [('sale_rep_id', '=', self.sale_rep_id.id), ('sale_partner_type', '=', 'sale_rep')]
            if self.product_id.detailed_type == 'service':
                domain += [('product_ids', 'in', self.product_id.id), ('product_ids.detailed_type', '=', 'service')]
            rules = sale_commission.search(domain, order='sequence')
        elif sale_partner_type == 'user' and self.sale_person_id:
            domain = [('user_ids', 'in', self.sale_person_id.id), ('sale_partner_type', '=', 'user')]
            if self.product_id.detailed_type == 'service':
                domain += [('product_ids', 'in', self.product_id.id), ('product_ids.detailed_type', '=', 'service')]
            rules = sale_commission.search(domain, order='sequence')
        elif sale_partner_type == 'sale_team' and self.team_id and self.team_id.user_id:
            domain = [('sale_team_rep', '=', self.team_id.user_id.id), ('sale_partner_type', '=', 'sale_team')]
            if self.product_id.detailed_type == 'service':
                domain += [('product_ids', 'in', self.product_id.id), ('product_ids.detailed_type', '=', 'service')]
            rules = sale_commission.search(domain, order='sequence')

        for rule in rules:
            data = self._get_commission_calc_data(rule)
            data['percentage'] = percentage
            amount = rule.calculate_amount(data)
            if amount is not None:
                self[commission_field] = rule.id
                self[amount_field] = amount
                return

        generic = self._get_generic_commission()
        if generic:
            data = self._get_commission_calc_data(generic)
            data['percentage'] = percentage
            amount = generic.calculate_amount(data)
            self[commission_field] = generic.id
            self[amount_field] = amount or 0.0
        else:
            # Keep the manual % even without a matching rule; amount = % of before-tax base.
            b_before, _b_after = self._get_commission_amount_bases()
            self[commission_field] = False
            self[amount_field] = (percentage * (b_before or 0.0)) / 100.0

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

    def _inverse_commission_amount(self):
        pass



    @api.depends(
        "sale_person_id", "team_id", "user_id", "sale_rep_id",
        "price_total", "price_subtotal", "partner_id", "product_id",
        "commission_percent", "in_commission_percent", "out_commission_percent",
        "manual_commission", "manual_in_commission", "manual_out_commission",
        "epd_paid_on_line", "move_id.epd_paid_total",
    )
    def _compute_commission_amount(self):
        for line in self:
            sale_commission = self.env['sale.commission']
            rules = []
            if line.product_id.detailed_type == 'service':
                rep_rules = sale_commission.search(
                    [('sale_rep_id', '=', line.sale_rep_id.id), ('sale_partner_type', '=', 'sale_rep'),
                     ('product_ids', 'in', line.product_id.id),('product_ids.detailed_type','=','service')],
                    order='sequence') if line.sale_rep_id else sale_commission.browse()
                user_rules = sale_commission.search(
                    [('user_ids', 'in', line.sale_person_id.id), ('sale_partner_type', '=', 'user'),
                    ('product_ids', 'in', line.product_id.id), ('product_ids.detailed_type', '=', 'service')
                     ],
                    order='sequence') if line.sale_person_id else sale_commission.browse()
                team_rules = sale_commission.search(
                    [('sale_team_rep', '=', line.team_id.user_id.id), ('sale_partner_type', '=', 'sale_team'),
                     ('product_ids', 'in', line.product_id.id), ('product_ids.detailed_type', '=', 'service')
                     ],
                    order='sequence') if line.team_id else sale_commission.browse()

                for rule in rep_rules:
                    data = line._get_commission_calc_data(rule)
                    data['percentage'] = line.commission_percent if line.manual_commission else rule.percentage
                    amount = rule.calculate_amount(data)
                    if amount:
                        line.commission_id = rule.id if rule else False
                        line.commission_percent = line.commission_percent if line.manual_commission else rule.percentage
                        line.commission_amount = amount
                        break
                else:
                    if line.manual_commission and line.commission_percent:
                        generic = line._get_generic_commission()
                        if generic:
                            data = line._get_commission_calc_data(generic)
                            data['percentage'] = line.commission_percent
                            amount = generic.calculate_amount(data)
                        else:
                            data = line._get_commission_calc_data(False)
                            data['percentage'] = line.commission_percent
                            amount = 0.0
                        line.commission_id = generic.id if generic else False
                        line.commission_amount = amount
                    else:
                        line.commission_percent = 0.0
                        # line.commission_id = False
                        line.commission_amount = 0.0

                for user_rule in user_rules:
                    data = line._get_commission_calc_data(user_rule)
                    data['percentage'] = line.in_commission_percent if line.manual_in_commission else user_rule.percentage
                    amount = user_rule.calculate_amount(data)
                    if amount:
                        line.in_commission_id = user_rule.id if user_rule else False
                        line.in_commission_percent = line.in_commission_percent if line.manual_in_commission else user_rule.percentage
                        line.in_commission_amount = amount
                        break
                else:
                    if line.in_commission_percent:
                        generic = line._get_generic_commission()
                        if generic:
                            data = line._get_commission_calc_data(generic)
                            data['percentage'] = line.in_commission_percent
                            amount = generic.calculate_amount(data)
                        else:
                            data = line._get_commission_calc_data(False)
                            data['percentage'] = line.in_commission_percent
                            amount = 0.0
                        line.in_commission_id = generic.id if generic else False
                        line.in_commission_amount = amount
                    else:
                        line.in_commission_percent = 0
                        # line.in_commission_id = False
                        line.in_commission_amount = 0.0
                    # ================= TEAM COMMISSION =================
                for team_rule in team_rules:
                    data = line._get_commission_calc_data(team_rule)
                    data['percentage'] = line.out_commission_percent if line.manual_out_commission else team_rule.percentage
                    amount = team_rule.calculate_amount(data)
                    if amount:
                        line.out_commission_id = team_rule.id if team_rule else False
                        line.out_commission_percent = line.out_commission_percent if line.manual_out_commission else team_rule.percentage
                        line.out_commission_amount = amount
                        break
                else:
                    # Preserve explicit C% OUT values even when no team rule is matched.
                    if line.out_commission_percent:
                        generic = line._get_generic_commission()
                        if generic:
                            data = line._get_commission_calc_data(generic)
                            data['percentage'] = line.out_commission_percent
                            amount = generic.calculate_amount(data)
                        else:
                            data = line._get_commission_calc_data(False)
                            data['percentage'] = line.out_commission_percent
                            amount = 0.0
                        line.out_commission_id = generic.id if generic else False
                        line.out_commission_amount = amount
                    else:
                        line.out_commission_percent = 0
                        # line.out_commission_id = False
                        line.out_commission_amount = 0.0


            else:
                rep_rules = sale_commission.search([('sale_rep_id', '=', line.sale_rep_id.id),('sale_partner_type','=','sale_rep')],
                                                   order='sequence') if line.sale_rep_id else sale_commission.browse()
                user_rules = sale_commission.search([('user_ids', 'in', line.sale_person_id.id),('sale_partner_type','=','user')],
                                                    order='sequence') if line.sale_person_id else sale_commission.browse()
                team_rules = sale_commission.search([('sale_team_rep', '=', line.team_id.user_id.id),('sale_partner_type','=','sale_team')],
                                                    order='sequence') if line.team_id else sale_commission.browse()

                for rule in rep_rules:
                    data = line._get_commission_calc_data(rule)
                    data['percentage'] = line.commission_percent if line.manual_commission else rule.percentage
                    amount = rule.calculate_amount(data)
                    if amount:
                        line.commission_id = rule.id if rule else False
                        line.commission_percent = line.commission_percent if line.manual_commission else rule.percentage
                        line.commission_amount = amount
                        break
                else:
                    if line.manual_commission and line.commission_percent:
                        generic = line._get_generic_commission()
                        if generic:
                            data = line._get_commission_calc_data(generic)
                            data['percentage'] = line.commission_percent
                            amount = generic.calculate_amount(data)
                        else:
                            data = line._get_commission_calc_data(False)
                            data['percentage'] = line.commission_percent
                            amount = 0.0
                        line.commission_id = generic.id if generic else False
                        line.commission_amount = amount
                    else:
                        line.commission_percent = 0.0
                        # line.commission_id = False
                        line.commission_amount = 0.0

                for user_rule in user_rules:
                    data = line._get_commission_calc_data(user_rule)
                    data['percentage'] = line.in_commission_percent if line.manual_in_commission else user_rule.percentage
                    amount = user_rule.calculate_amount(data)
                    if amount:
                        line.in_commission_id = user_rule.id if user_rule else False
                        line.in_commission_percent = line.in_commission_percent if line.manual_in_commission else user_rule.percentage
                        line.in_commission_amount = amount
                        break
                else:
                    if line.in_commission_percent:
                        generic = line._get_generic_commission()
                        if generic:
                            data = line._get_commission_calc_data(generic)
                            data['percentage'] = line.in_commission_percent
                            amount = generic.calculate_amount(data)
                        else:
                            data = line._get_commission_calc_data(False)
                            data['percentage'] = line.in_commission_percent
                            amount = 0.0
                        line.in_commission_id = generic.id if generic else False
                        line.in_commission_amount = amount
                    else:
                        line.in_commission_percent = 0
                        # line.in_commission_id = False
                        line.in_commission_amount = 0.0
                    # ================= TEAM COMMISSION =================
                for team_rule in team_rules:
                    data = line._get_commission_calc_data(team_rule)
                    data['percentage'] = line.out_commission_percent if line.manual_out_commission else team_rule.percentage
                    amount = team_rule.calculate_amount(data)
                    if amount:
                        line.out_commission_id = team_rule.id if team_rule else False
                        line.out_commission_percent = line.out_commission_percent if line.manual_out_commission else team_rule.percentage
                        line.out_commission_amount = amount
                        break
                else:
                    # Preserve explicit C% OUT values even when no team rule is matched.
                    if line.out_commission_percent:
                        generic = line._get_generic_commission()
                        if generic:
                            data = line._get_commission_calc_data(generic)
                            data['percentage'] = line.out_commission_percent
                            amount = generic.calculate_amount(data)
                        else:
                            data = line._get_commission_calc_data(False)
                            data['percentage'] = line.out_commission_percent
                            amount = 0.0
                        line.out_commission_id = generic.id if generic else False
                        line.out_commission_amount = amount
                    else:
                        line.out_commission_percent = 0
                        # line.out_commission_id = False
                        line.out_commission_amount = 0.0

            # if line.move_id.move_type == 'out_refund':
            #     amount = -amount
            # line.commission_amount = amount

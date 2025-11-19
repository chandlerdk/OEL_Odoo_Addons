# -*- coding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023
#
##############################################################################

from odoo import api, fields, models

class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    sale_rep_id = fields.Many2one('res.partner', string='Sale Rep', related="move_id.sale_rep_id")
    is_commission_billed = fields.Boolean(string="Commission Billed", default=False, copy=False)
    manual_commission = fields.Boolean(string="Manual C% Man", copy=False)
    manual_in_commission = fields.Boolean(string="Manual C% In", copy=False)
    manual_out_commission = fields.Boolean(string="Manual C% Out", copy=False)

    # ------------------------------------------------------------
    #  Fix: remove self.write() in all onchange functions
    # ------------------------------------------------------------

    @api.onchange('commission_percent')
    def _onchange_commission_percent(self):
        for line in self:
            # set in memory instead of writing to DB
            line.manual_commission = True

            sale_commission = self.env['sale.commission']
            data = {
                'product_id': line.product_id,
                'partner_id': line.partner_id,
                'quantity': line.quantity,
                'amount_after_tax': line.price_total,
                'amount_before_tax': line.price_subtotal,
                'percentage': line.commission_percent,
            }

            rep_rules = sale_commission.search(
                [('sale_rep_id', '=', line.sale_rep_id.id),
                 ('sale_partner_type', '=', 'sale_rep')],
                order='sequence') if line.sale_rep_id else sale_commission.browse()

            for rule in rep_rules:
                data['percentage'] = line.commission_percent if line.manual_commission else rule.percentage
                amount = rule.calculate_amount(data)
                if amount:
                    line.commission_id = rule.id or False
                    line.commission_percent = line.commission_percent if line.manual_commission else rule.percentage
                    line.commission_amount = amount
                    break

    @api.onchange('in_commission_percent')
    def _onchange_in_commission_percent(self):
        for line in self:
            line.manual_in_commission = True

            sale_commission = self.env['sale.commission']
            data = {
                'product_id': line.product_id,
                'partner_id': line.partner_id,
                'quantity': line.quantity,
                'amount_after_tax': line.price_total,
                'amount_before_tax': line.price_subtotal,
                'percentage': line.commission_percent,
            }

            user_rules = sale_commission.search(
                [('user_ids', 'in', line.sale_person_id.id),
                 ('sale_partner_type', '=', 'user')],
                order='sequence') if line.user_id else sale_commission.browse()

            for user_rule in user_rules:
                data['percentage'] = line.in_commission_percent if line.manual_in_commission else user_rule.percentage
                amount = user_rule.calculate_amount(data)
                if amount:
                    line.in_commission_id = user_rule.id or False
                    line.in_commission_percent = (
                        line.in_commission_percent if line.manual_in_commission else user_rule.percentage
                    )
                    line.in_commission_amount = amount
                    break

    @api.onchange('out_commission_percent')
    def _onchange_out_commission_percent(self):
        for line in self:
            line.manual_out_commission = True

            sale_commission = self.env['sale.commission']
            data = {
                'product_id': line.product_id,
                'partner_id': line.partner_id,
                'quantity': line.quantity,
                'amount_after_tax': line.price_total,
                'amount_before_tax': line.price_subtotal,
                'percentage': line.commission_percent,
            }

            team_rules = sale_commission.search(
                [('sale_team_rep', '=', line.team_id.user_id.id),
                 ('sale_partner_type', '=', 'sale_team')],
                order='sequence') if line.team_id else sale_commission.browse()

            for team_rule in team_rules:
                data['percentage'] = line.out_commission_percent if line.manual_out_commission else team_rule.percentage
                amount = team_rule.calculate_amount(data)
                if amount:
                    line.out_commission_id = team_rule.id or False
                    line.out_commission_percent = (
                        line.out_commission_percent if line.manual_out_commission else team_rule.percentage
                    )
                    line.out_commission_amount = amount
                    break

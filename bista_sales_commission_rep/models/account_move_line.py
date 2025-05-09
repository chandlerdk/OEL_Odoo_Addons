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
    is_commission_billed = fields.Boolean(string="Commission Billed", default=False,copy=False)

    @api.depends("sale_person_id", "team_id","user_id",
                 "sale_rep_id",
                 "commission_id",
                 "commission_percent",
                 "price_total",
                 "partner_id",
                 "product_id")
    def _compute_commission_amount(self):
        for line in self:
            # if not line.commission_percent:
            #     line.commission_amount = 0
            #     continue

            data = {
                'product_id': line.product_id,
                'partner_id': line.partner_id,
                'quantity': line.quantity,
                'amount_after_tax': line.price_total,
                'amount_before_tax': line.price_subtotal,
                'percentage': line.commission_percent
            }
            amount = 0
            if line.commission_id:
                amount = line.commission_id.calculate_amount(data)
            # else:
            sale_commission = self.env['sale.commission']
            rules = []
            # if line.sale_rep_id:
            #     rules = sale_commission.search([('sale_rep_id', '=', line.sale_rep_id.id)],
            #                                    order='priority desc')
            # elif line.sale_percent:
            #     user = line.sale_person_id
            #     rules = sale_commission.search([('user_ids', '=', user.id)],
            #                                    order='priority desc')
            rep_rules = sale_commission.search([('sale_rep_id', '=', line.sale_rep_id.id),('sale_partner_type','=','sale_rep')],
                                               order='sequence') if line.sale_rep_id else sale_commission.browse()
            user_rules = sale_commission.search([('user_ids', 'in', line.sale_person_id.id),('sale_partner_type','=','user')],
                                                order='sequence') if line.user_id else sale_commission.browse()
            team_rules = sale_commission.search([('sale_team_rep', '=', line.team_id.user_id.id),('sale_partner_type','=','sale_team')],
                                                order='sequence') if line.team_id else sale_commission.browse()

            for rule in rep_rules:
                data['percentage'] = rule.percentage
                amount = rule.calculate_amount(data)
                if amount:
                    line.commission_id = rule.id if rule else False
                    line.commission_percent = rule.percentage
                    line.commission_amount = amount
                    break
            else:
                line.commission_percent = 0.0
                line.commission_id = False
                line.commission_amount = 0.0

            for user_rule in user_rules:
                data['percentage'] = user_rule.percentage
                amount = user_rule.calculate_amount(data)
                if amount:
                    line.in_commission_id = user_rule.id if user_rule else False
                    line.in_commission_percent = user_rule.percentage
                    line.in_commission_amount = amount
                    break
            else:
                line.in_commission_percent = 0
                line.in_commission_id = False
                line.in_commission_amount = 0.0
                # ================= TEAM COMMISSION =================
            for team_rule in team_rules:
                data['percentage'] = team_rule.percentage
                amount = team_rule.calculate_amount(data)
                if amount:
                    line.out_commission_id = team_rule.id if team_rule else False
                    line.out_commission_percent = team_rule.percentage
                    line.out_commission_amount = amount
                    break
            else:
                line.out_commission_percent = 0
                line.out_commission_id = False
                line.out_commission_amount = 0.0

            # if line.move_id.move_type == 'out_refund':
            #     amount = -amount
            # line.commission_amount = amount

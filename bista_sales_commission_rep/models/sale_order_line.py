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

    def write(self, vals):
        if 'commission_percent' in vals:
            vals['manual_commission'] = True
        if 'in_commission_percent' in vals:
            vals['manual_in_commission'] = True
        if 'out_commission_percent' in vals:
            vals['manual_out_commission'] = True
        return super(SaleOrderLine, self).write(vals)

    @api.depends("user_id",
                 "sale_rep_id",
                 "team_id",
                 "commission_id",
                 "commission_percent",
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
                specific_commission_rule = self.env['sale.commission'].search([
                    ('product_ids', 'in', line.product_template_id.id),('product_ids.detailed_type','=','service')
                ],limit=1)
                if specific_commission_rule:
                    if line.sale_rep_id == specific_commission_rule.sale_rep_id:
                        line.commission_amount = 0
                        line.commission_id = specific_commission_rule.id
                        line.commission_percent = specific_commission_rule.percentage
                    elif line.user_id.id in specific_commission_rule.user_ids.ids:
                        line.commission_amount = 0
                        line.commission_id = specific_commission_rule.id
                        line.commission_percent = specific_commission_rule.percentage
                    continue
            rep_rules = sale_commission.search([('sale_rep_id', '=', line.sale_rep_id.id),('sale_partner_type','=','sale_rep')],
                                               order='sequence') if line.sale_rep_id else sale_commission.browse()
            user_rules = sale_commission.search([('user_ids', 'in', line.user_id.id),('sale_partner_type','=','user')],
                                                order='sequence') if line.user_id else sale_commission.browse()
            team_rules = sale_commission.search([('sale_team_rep', '=', line.team_id.user_id.id),('sale_partner_type','=','sale_team')],
                                                    order='sequence') if line.team_id else sale_commission.browse()
            for rule in rep_rules:
                data['percentage'] = rule.percentage if not line.manual_commission else line.commission_percent
                amount = rule.calculate_amount(data)
                if amount:
                    line.commission_amount = amount
                    line.commission_id = rule.id if rule else False
                    line.commission_percent = rule.percentage if not line.manual_commission else line.commission_percent
                    break
            else:
                line.commission_percent = 0.0
                line.commission_id = False
                line.commission_amount = 0.0
                    # ================= USER COMMISSION =================
            for user_rule in user_rules:
                data['percentage'] = user_rule.percentage if not line.manual_in_commission else line.in_commission_percent
                amount = user_rule.calculate_amount(data)
                if amount:
                    line.in_commission_id = user_rule.id if user_rule else False
                    line.in_commission_percent = user_rule.percentage if not line.manual_in_commission else line.in_commission_percent
                    line.in_commission_amount = amount
                    break
            else:
                line.in_commission_percent = 0
                line.in_commission_id = False
                line.in_commission_amount = 0.0

                    # ================= TEAM COMMISSION =================
            for team_rule in team_rules:
                data['percentage'] = team_rule.percentage if not line.manual_out_commission else line.out_commission_percent
                amount = team_rule.calculate_amount(data)
                if amount:
                    line.out_commission_id = team_rule.id if team_rule else False
                    line.out_commission_percent = team_rule.percentage if not line.manual_out_commission else line.out_commission_percent
                    line.out_commission_amount = amount
                    break
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
        })
        return res
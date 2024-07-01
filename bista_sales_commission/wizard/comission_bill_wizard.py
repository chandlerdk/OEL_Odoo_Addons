from odoo import models, fields, api
from collections import defaultdict
from datetime import datetime, date


class CommissionBillWizard(models.TransientModel):
    _name = 'commission.bill.wizard'
    _description = 'Commission Bill Wizard'

    start_date = fields.Date()
    end_date = fields.Date()
    sale_person_ids = fields.Many2many('res.users')
    partner_ids = fields.Many2many('res.partner')

    def _get_domain(self):
        domain = [('commission_id', '!=', False), ("commission_to_bill", "=", True)]
        if self.sale_person_ids:
            domain.append(("sale_person_id", "in", self.sale_person_ids.ids))
        if self.partner_ids:
            domain.append(("partner_id", "in", self.partner_ids.ids))
        if self.start_date:
            domain.append(("commission_date", ">=", self.start_date))
        if self.end_date:
            domain.append(("commission_date", "<=", self.end_date))
        return domain

    def _get_moves_by_user(self):
        moves = self.env['account.move.line'].search(self._get_domain())
        moves_by_user = defaultdict(list)
        for move in moves:
            sale_person = move.sale_person_id.partner_id
            commission = move.commission_id
            if commission and commission.sale_partner_type == 'sale_team' and commission.sale_team_rep:
                sale_person = commission.sale_team_rep.partner_id
            moves_by_user[sale_person].append(move)
        return moves_by_user

    def confirm(self):
        moves_by_user = self._get_moves_by_user()
        print("move by user ", moves_by_user)
        bill = self.env['account.move']
        bill_ids = []
        for user in moves_by_user:
            moves = moves_by_user.get(user, [])
            bill = bill.create({
                'move_type': 'in_invoice',
                'partner_id': user.id,
                'invoice_date': date.today(),
                'is_commission_bill': True,
                'invoice_line_ids': [(0, 0, {
                    'name': f"Com: {line.move_id.name}/{line.name}",
                    'quantity': 1,
                    'price_unit': line.commission_amount,
                    'account_id': line.commission_id.payout_account_id.id,
                    'commission_reverse_move_line_id': line.id
                }) for line in moves],
            })

            for rec in bill.invoice_line_ids:
                rec.commission_reverse_move_line_id.write({
                    'commission_move_line_id': rec.id,
                    'commission_move_id': bill.id
                })
            bill.action_post()
            bill_ids.append(bill.id)
        result = self.env['ir.actions.act_window']._for_xml_id('account.action_move_in_invoice_type')
        result.update({
            'domain': [('id', 'in', bill_ids)],
        })
        return result

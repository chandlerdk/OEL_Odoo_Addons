from odoo import models, fields, api
from collections import defaultdict
from datetime import datetime, date


class CommissionBillWizard(models.TransientModel):
    _inherit = 'commission.bill.wizard'

    sale_rep_id = fields.Many2one('res.partner', domain=[('is_sale_rep', '=', True)])

    def _get_domain(self):
        domain = super()._get_domain()
        if self.sale_rep_id:
            domain.append(("sale_rep_id", "=", self.sale_rep_id.id))
            for dom in domain:
                if dom[0] == 'sale_person_id':
                    domain.remove(dom)
        return domain

    def _get_moves_by_user(self):
        moves = self.env['account.move.line'].search(self._get_domain())
        moves_by_user = defaultdict(list)
        for move in moves:
            if move.sale_rep_id:
                sale_person = move.sale_rep_id
            else:
                sale_person = move.sale_person_id.partner_id
            commission = move.commission_id
            if commission and commission.sale_partner_type == 'sale_team' and commission.sale_team_rep:
                sale_person = commission.sale_team_rep.partner_id
            moves_by_user[sale_person].append(move)
        return moves_by_user

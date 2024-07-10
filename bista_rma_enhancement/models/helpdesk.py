# -*- coding: utf-8 -*-

from odoo import fields, models


class HelpdeskTicket(models.Model):
    _inherit = 'helpdesk.ticket'

    is_rma = fields.Boolean(
        string="RMA", related="team_id.is_rma"
    )
    rma_count = fields.Integer(
        'RMA Claims', compute='_compute_rma_count'
    )

    def _compute_rma_count(self):
        for rec in self:
            rec.rma_count = self.env['crm.claim.ept'].search_count(
                [('helpdesk_ticket_id', '=', rec.id)])

    def action_view_rma(self):
        rma = self.env['crm.claim.ept'].search([('helpdesk_ticket_id', '=', self.id)])
        if len(rma) == 1:
            claim_action = {
                'name': "RMA",
                'view_mode': 'form',
                'res_model': 'crm.claim.ept',
                'type': 'ir.actions.act_window',
                'res_id': rma.id,
                'context': {
                    'create': False
                }
            }
        else:
            claim_action = {
                'name': "RMA",
                'view_mode': 'tree,form',
                'res_model': 'crm.claim.ept',
                'type': 'ir.actions.act_window',
                'domain': [('id', 'in', rma.ids)],
                'context': {
                    'create': False
                }
            }
        return claim_action

    def create_rma(self):
        claim_action = {
            'name': "RMA",
            'view_mode': 'form',
            'res_model': 'crm.claim.ept',
            'type': 'ir.actions.act_window',
            'context': {
                'default_helpdesk_ticket_id': self.id,
                'default_partner_id': self.partner_id.id,
                'helpdesk_ticket_name': self.name,
            }
        }
        return claim_action


class HelpdeskTeam(models.Model):
    _inherit = 'helpdesk.team'

    is_rma = fields.Boolean(string="RMA")

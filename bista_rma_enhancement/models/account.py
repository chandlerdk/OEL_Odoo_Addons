# -*- coding: utf-8 -*-

from odoo import fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    claim_count = fields.Integer(compute="_claim_count")
    helpdesk_ticket_count = fields.Integer(compute='_compute_ticket_count')

    def _stock_account_prepare_anglo_saxon_out_lines_vals(self):
        if self.claim_id:
            return []
        return super()._stock_account_prepare_anglo_saxon_out_lines_vals()

    def _claim_count(self):
        for record in self:
            claim_count = self.env['crm.claim.ept'].search_count([
                ('id', '=', record.claim_id.id)
            ])
            record.claim_count = claim_count

    def action_view_claim(self):
        rma = self.env['crm.claim.ept'].search([('id', '=', self.claim_id.id)])

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

    def _compute_ticket_count(self):
        for record in self:
            ticket_count = self.env['helpdesk.ticket'].search_count([
                ('id', '=', record.claim_id.helpdesk_ticket_id.id)
            ])
            record.helpdesk_ticket_count = ticket_count

    def action_see_helpdesk_ticket(self):
        helpdesk_ticket_id = self.env['helpdesk.ticket'].search([('id', '=', self.claim_id.helpdesk_ticket_id.id)])

        if len(helpdesk_ticket_id) == 1:
            helpdesk_ticket_action = {
                'name': "Helpdesk Ticket",
                'view_mode': 'form',
                'res_model': 'helpdesk.ticket',
                'type': 'ir.actions.act_window',
                'res_id': helpdesk_ticket_id.id,
                'context': {
                    'create': False
                }
            }
        else:
            helpdesk_ticket_action = {
                'name': "Helpdesk Ticket",
                'view_mode': 'tree,form',
                'res_model': 'helpdesk.ticket',
                'type': 'ir.actions.act_window',
                'domain': [('id', 'in', helpdesk_ticket_id.ids)],
                'context': {
                    'create': False
                }
            }

        return helpdesk_ticket_action

# -*- coding: utf-8 -*-
from odoo import models, fields

class CommissionDashboard(models.Model):
    _name = 'commission.dashboard'
    _description = 'Sales Commission Dashboard'

    name = fields.Char(string='Dashboard', default='Sales Commission Review', readonly=True)

    def action_open_wizard(self):
        """Open the commission preparation wizard"""
        return {
            'name': 'Prepare Sales Commission Report',
            'type': 'ir.actions.act_window',
            'res_model': 'sales.commission.prepare.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }

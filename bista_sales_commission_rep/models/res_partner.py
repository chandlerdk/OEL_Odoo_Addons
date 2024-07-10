from odoo import api, fields, models, _


class Partner(models.Model):
    _inherit = 'res.partner'

    is_sale_rep = fields.Boolean(string='Sales Rep')
    sale_rep_id = fields.Many2one('res.partner', domain=[('is_sale_rep', '=', True)])


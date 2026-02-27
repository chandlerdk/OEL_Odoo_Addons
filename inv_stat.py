from odoo import api, fields, models

class ResPartner(models.Model):
    _inherit = 'res.partner'

    gln = fields.Char(string='GLN', required=False,readonly=False)
    dc = fields.Many2one(string='Distribution Center', comodel_name='res.partner', required=False)
    tp_id = fields.Many2one(string='Trading Partner', comodel_name='bsiedi.tradingpartner', required=False)

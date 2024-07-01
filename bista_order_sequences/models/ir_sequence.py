from odoo import api, models


class IrSequence(models.Model):
    _inherit = 'ir.sequence'

    @api.model
    def get_by_code(self, code):
        company_id = self.env.company.id
        return self.search([
            ('code', '=', code),
            ('company_id', 'in', [company_id, False])],
            order='company_id', limit=1)

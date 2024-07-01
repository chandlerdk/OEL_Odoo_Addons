from odoo import api, fields, models, Command


class AccountMove(models.Model):
    _inherit = 'account.move'

    sale_rep_id = fields.Many2one('res.partner', domain=[('is_sale_rep', '=', True)])

    @api.model
    def create(self, vals_list):
        ret = super(AccountMove, self).create(vals_list)
        sale_rep_id = ret.mapped("invoice_line_ids").mapped("sale_line_ids").mapped("sale_rep_id")
        if len(sale_rep_id):
            ret.sale_rep_id = sale_rep_id[0].id
        return ret

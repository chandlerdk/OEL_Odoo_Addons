from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    sale_rep_id = fields.Many2one('res.partner', domain=[('is_sale_rep', '=', True)])

    @api.model_create_multi
    def create(self, vals_list):
        moves = super(AccountMove, self).create(vals_list)
        for ret in moves:
            if not ret.sale_rep_id:
                sale_rep_id = ret.mapped("invoice_line_ids").mapped("sale_line_ids").mapped("sale_rep_id")
                if len(sale_rep_id):
                    ret.sale_rep_id = sale_rep_id[0].id
                else:
                    ret._get_sale_rep_id()
        return moves

    @api.onchange("partner_id")
    def _get_sale_rep_id(self):
        for order in self:
            if order.partner_id and order.partner_id.sale_rep_id:
                order.sale_rep_id = order.partner_id.sale_rep_id

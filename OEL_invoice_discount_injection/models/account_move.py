# -*- coding: utf-8 -*-
from odoo import api, fields, models

PARTNER_DISCOUNT_DEFAULT_CODE = 'PARTNER_DISCOUNT'

class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    is_global_discount_line = fields.Boolean(string="Is Global Discount Line", default=False, index=True)


class AccountMove(models.Model):
    _inherit = 'account.move'

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        for move in moves:
            move._apply_or_update_global_discount()
        return moves

    def write(self, vals):
        res = super().write(vals)
        trigger_fields = {'invoice_line_ids', 'partner_id', 'currency_id', 'company_id'}
        if trigger_fields.intersection(vals.keys()):
            for move in self:
                move._apply_or_update_global_discount()
        return res

    # ---------- Helpers ----------

    def _compute_discount_amount(self, partner):
        self.ensure_one()
        if partner.x_global_discount_type == 'percent':
            base = self.amount_untaxed
            return (partner.x_global_discount_value / 100.0) * base
        return partner.x_global_discount_value

    def _get_discount_product(self, partner):
        product = partner.x_global_discount_product_id
        if not product:
            product = self.env['product.product'].search(
                [('default_code', '=', PARTNER_DISCOUNT_DEFAULT_CODE)], limit=1
            )
        return product

    def _find_discount_line(self):
        self.ensure_one()
        return self.invoice_line_ids.filtered(lambda l: l.is_global_discount_line and not l.display_type)

    # ---------- Core logic ----------

    def _apply_or_update_global_discount(self):
        for move in self:
            if move.move_type not in ('out_invoice', 'out_refund'):
                continue

            partner = move.partner_id.commercial_partner_id
            dline = move._find_discount_line()

            if not partner.x_has_global_discount:
                if dline:
                    dline.with_context(check_move_validity=False).unlink()
                continue

            discount_amount = move._compute_discount_amount(partner)
            if not discount_amount or discount_amount == 0.0:
                if dline:
                    dline.with_context(check_move_validity=False).unlink()
                continue

            product = move._get_discount_product(partner)
            discount_taxes = product.taxes_id.filtered(lambda t: t.company_id == move.company_id) if product else self.env['account.tax']

            # Build dynamic description without Babel
            if partner.x_global_discount_type == 'percent':
                # show concise percentage (no trailing .0 if not needed)
                disp = f"{partner.x_global_discount_value:g}%"
            else:
                # show fixed amount with currency name; keep it simple and locale-agnostic
                currency = move.company_id.currency_id
                disp = f"{currency.symbol}{abs(partner.x_global_discount_value):,.2f}" if currency and currency.symbol else f"{abs(partner.x_global_discount_value):,.2f} {currency.name if currency else ''}"

            line_name = f"Partner Discount - {disp}"

            line_vals = {
                'move_id': move.id,
                'name': line_name,
                'quantity': 1.0,
                'price_unit': -abs(discount_amount),
                'is_global_discount_line': True,
                'tax_ids': [(6, 0, discount_taxes.ids)],
            }
            if product:
                line_vals['product_id'] = product.id

            if dline:
                dline.with_context(check_move_validity=False).write(line_vals)
            else:
                self.env['account.move.line'].with_context(check_move_validity=False).create(line_vals)

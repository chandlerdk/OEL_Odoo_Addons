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
        # Inject exactly one discount line on create if applicable
        for move in moves:
            move._create_partner_discount_line_if_needed()
        return moves

    def write(self, vals):
        res = super().write(vals)
        # Always update existing discount line after any write, since invoice line edits
        # may not include 'invoice_line_ids' in vals at the move level in Odoo 17
        for move in self:
            move._update_partner_discount_line_if_present()
        return res

    def action_post(self):
        # On confirm, only update existing discount line (no creation here)
        for move in self:
            move._update_partner_discount_line_if_present()
        return super().action_post()

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

    def _get_existing_discount_line(self):
        self.ensure_one()
        return self.invoice_line_ids.filtered(lambda l: l.is_global_discount_line and not l.display_type)[:1]

    def _build_discount_line_vals(self, partner, move, discount_amount):
        product = move._get_discount_product(partner)
        discount_taxes = product.taxes_id.filtered(lambda t: t.company_id == move.company_id) if product else self.env['account.tax']

        # Dynamic description
        if partner.x_global_discount_type == 'percent':
            disp = f"{partner.x_global_discount_value:g}%"
        else:
            currency = move.company_id.currency_id
            disp = f"{currency.symbol}{abs(partner.x_global_discount_value):,.2f}" if currency and currency.symbol else f"{abs(partner.x_global_discount_value):,.2f} {currency.name if currency else ''}"
        line_name = f"Partner Discount - {disp}"

        vals = {
            'move_id': move.id,
            'name': line_name,
            'quantity': 1.0,
            'price_unit': -abs(discount_amount),
            'is_global_discount_line': True,
            'tax_ids': [(6, 0, discount_taxes.ids)],
        }
        if product:
            vals['product_id'] = product.id
        return vals

    # ---------- Core logic (create vs update) ----------

    def _create_partner_discount_line_if_needed(self):
        """Called after create(). Create the discount line only if:
           - move_type is customer invoice/refund
           - partner flag is enabled
           - computed discount amount > 0
           - no existing discount line present
        """
        for move in self:
            if move.move_type not in ('out_invoice', 'out_refund'):
                continue

            # If a discount line already exists, do nothing (respect 'create only once')
            if move._get_existing_discount_line():
                continue

            partner = move.partner_id.commercial_partner_id
            if not partner.x_has_global_discount:
                continue

            discount_amount = move._compute_discount_amount(partner)
            if not discount_amount or abs(discount_amount) < 1e-9:
                continue

            line_vals = move._build_discount_line_vals(partner, move, discount_amount)
            self.env['account.move.line'].with_context(check_move_validity=False).create(line_vals)

    def _update_partner_discount_line_if_present(self):
        """Called on save/confirm. Only update or remove an existing discount line.
           - If toggle off or amount = 0: remove the line.
           - Else: update line values (name, price_unit, taxes/product).
           - Never create a new line here to avoid duplicates from multiple triggers.
        """
        for move in self:
            if move.move_type not in ('out_invoice', 'out_refund'):
                continue

            dline = move._get_existing_discount_line()
            if not dline:
                # No line exists: do nothing (creation is only allowed on create())
                continue

            partner = move.partner_id.commercial_partner_id
            discount_amount = move._compute_discount_amount(partner)

            if (not partner.x_has_global_discount) or (not discount_amount) or abs(discount_amount) < 1e-9:
                # Remove existing if toggle off or zero amount
                dline.with_context(check_move_validity=False).unlink()
                continue

            # Update existing line with new values
            line_vals = move._build_discount_line_vals(partner, move, discount_amount)
            # Don't allow move_id change in update
            line_vals.pop('move_id', None)
            dline.with_context(check_move_validity=False).write(line_vals)

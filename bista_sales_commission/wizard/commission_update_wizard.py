# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class AccountMoveCommissionUpdateWizard(models.TransientModel):
    _name = "account.move.commission.update.wizard"
    _description = "Update Invoice Commission After Payment"

    move_id = fields.Many2one(
        "account.move",
        string="Invoice",
        required=True,
        readonly=True,
    )
    sale_rep_id = fields.Many2one(
        "res.partner",
        string="Sale Rep",
    )
    currency_id = fields.Many2one(related="move_id.currency_id")
    payment_state = fields.Selection(related="move_id.payment_state")
    has_accrual = fields.Boolean(compute="_compute_has_accrual")
    line_ids = fields.One2many(
        "account.move.commission.update.wizard.line",
        "wizard_id",
        string="Commission Lines",
    )

    @api.depends("move_id.commission_accrual_move_id")
    def _compute_has_accrual(self):
        for wiz in self:
            wiz.has_accrual = bool(wiz.move_id.commission_accrual_move_id)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        move = self.env["account.move"].browse(self.env.context.get("active_id"))
        if not move or not move.is_invoice(include_receipts=True):
            return res
        res["move_id"] = move.id
        if "sale_rep_id" in move._fields:
            res["sale_rep_id"] = move.sale_rep_id.id
        lines = []
        for line in move.invoice_line_ids.filtered(lambda l: l.display_type == "product"):
            lines.append((0, 0, {
                "move_line_id": line.id,
                "name": line.name,
                "price_subtotal": line.price_subtotal,
                "epd_paid_on_line": line.epd_paid_on_line,
                "commission_percent": line.commission_percent,
                "in_commission_percent": line.in_commission_percent,
                "out_commission_percent": line.out_commission_percent,
                "commission_amount": line.commission_amount,
                "in_commission_amount": line.in_commission_amount,
                "out_commission_amount": line.out_commission_amount,
            }))
        res["line_ids"] = lines
        return res

    def action_apply(self):
        self.ensure_one()
        move = self.move_id
        if move.state != "posted":
            raise UserError(_("Commission can only be updated on a posted invoice."))

        posted_bills = move.invoice_line_ids.mapped("commission_vendor_bill_line_ids.move_id").filtered(
            lambda b: b.state == "posted"
        )
        if posted_bills:
            raise UserError(_(
                "Cannot update commission: post vendor commission bill(s) already exist (%s). "
                "Cancel/credit those bills first, then try again."
            ) % ", ".join(posted_bills.mapped("name")))

        if "sale_rep_id" in move._fields and self.sale_rep_id != move.sale_rep_id:
            move.sudo().write({"sale_rep_id": self.sale_rep_id.id})

        for wizard_line in self.line_ids:
            line = wizard_line.move_line_id
            if not line or line.move_id != move:
                continue
            vals = {
                "commission_percent": wizard_line.commission_percent,
                "in_commission_percent": wizard_line.in_commission_percent,
                "out_commission_percent": wizard_line.out_commission_percent,
            }
            if "manual_commission" in line._fields:
                vals["manual_commission"] = True
                vals["manual_in_commission"] = True
                vals["manual_out_commission"] = True
            line.sudo().write(vals)

        product_lines = move.invoice_line_ids.filtered(lambda l: l.display_type == "product")
        product_lines.invalidate_recordset([
            "epd_paid_on_line",
            "commission_amount",
            "in_commission_amount",
            "out_commission_amount",
        ])
        if hasattr(product_lines, "_compute_epd_paid_on_line"):
            product_lines._compute_epd_paid_on_line()
        product_lines._compute_commission_amount()

        move._replace_commission_accrual()

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Commission Updated"),
                "message": _(
                    "Commission percentages were saved and the accrual journal entry was refreshed for %s."
                ) % move.name,
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }


class AccountMoveCommissionUpdateWizardLine(models.TransientModel):
    _name = "account.move.commission.update.wizard.line"
    _description = "Update Invoice Commission Line"

    wizard_id = fields.Many2one(
        "account.move.commission.update.wizard",
        required=True,
        ondelete="cascade",
    )
    # Keep model-side editable; view marks readonly + force_save so Apply still sends IDs.
    move_line_id = fields.Many2one("account.move.line", required=True)
    name = fields.Char()
    currency_id = fields.Many2one(related="wizard_id.currency_id")
    price_subtotal = fields.Monetary(readonly=True, currency_field="currency_id")
    epd_paid_on_line = fields.Monetary(readonly=True, currency_field="currency_id", string="EPD Share")
    commission_percent = fields.Float(string="C% Man")
    in_commission_percent = fields.Float(string="C% In")
    out_commission_percent = fields.Float(string="C% Out")
    commission_amount = fields.Float(string="C Man Amount", readonly=True)
    in_commission_amount = fields.Float(string="C In Amount", readonly=True)
    out_commission_amount = fields.Float(string="C Out Amount", readonly=True)

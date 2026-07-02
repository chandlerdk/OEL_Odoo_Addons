from odoo import models, api

class AccountPartialReconcile(models.Model):
    _inherit = 'account.partial.reconcile'

    @api.model_create_multi
    def create(self, vals_list):
        res = super().create(vals_list)
        
        # Check affected invoices
        moves = res.mapped('debit_move_id.move_id') | res.mapped('credit_move_id.move_id')
        invoices = moves.filtered(lambda m: m.is_invoice(include_receipts=True))
        
        # Force compute payment state if not already computed
        invoices.flush_recordset(['payment_state'])
        
        for inv in invoices:
            if inv.payment_state in ('paid', 'in_payment') and not inv.commission_accrual_move_id:
                # Find the payment move that triggered this reconciliation
                # It is the other move in the reconciliation that is not the invoice
                reconciliations = res.filtered(lambda r: r.debit_move_id.move_id == inv or r.credit_move_id.move_id == inv)
                payment_move = False
                for rec in reconciliations:
                    other_move = rec.debit_move_id.move_id if rec.credit_move_id.move_id == inv else rec.credit_move_id.move_id
                    if other_move != inv:
                        payment_move = other_move
                        break
                inv._generate_commission_accrual_move(payment_move=payment_move)
                
        return res

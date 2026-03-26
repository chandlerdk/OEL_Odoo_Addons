from odoo import api, fields, models

class AccountMove(models.Model):
    _inherit = 'account.move'

    edi_invoice_sent = fields.Boolean(string='Invoice Sent?', required=False,readonly=True)
    edi_invoice_senddatetime = fields.Date(string='Invoice Send Date?', required=False,readonly=True)
    edi_invoice_ack = fields.Boolean(string='Invoice Accepted?', required=False,readonly=True)
    edi_invoice_rej = fields.Boolean(string='Invoice Rejected?', required=False,readonly=True)
    inv_send_requested = fields.Boolean(string='Send Invoice Requested?', required=False,readonly=True)
    hide_edi_info = fields.Boolean(string='Hide EDI Info', compute="_compute_hide_edi_info")
    hide_resend_810_button = fields.Boolean(string='Hide 810 Button', compute="_compute_hide_resend_810_button")
    
    @api.depends('partner_id')
    def _compute_hide_edi_info(self):
        # simple logic, but you can do much more here
        if self.move_type=='out_invoice':
            self.hide_edi_info = (not self.partner_id.tp_id.requires_edi_inv) or (self.state != 'posted')
        elif self.move_type=='out_refund':
            self.hide_edi_info = (not self.partner_id.tp_id.requires_edi_cmemo) or (self.state != 'posted')
        else:
            self.hide_edi_info = True
            
    @api.depends('partner_id')
    def _compute_hide_resend_810_button(self):
        # simple logic, but you can do much more here
        if self.move_type=='out_invoice':
            self.hide_resend_810_button = (not self.partner_id.tp_id.requires_edi_inv) or (self.state != 'posted') or (self.edi_invoice_sent == False and self.inv_send_requested == True)
        elif self.move_type=='out_refund':
            self.hide_resend_810_button = (not self.partner_id.tp_id.requires_edi_cmemo) or (self.state != 'posted') or (self.edi_invoice_sent == False and self.inv_send_requested == True)
        else:
            self.hide_resend_810_button = True

    def action_resend810(self):
        """ Mark the given draft quotation(s) as sent.

        :raise: UserError if any given SO is not in draft state.
        """
        """if any(order.state != 'purchase' for order in self):
            raise UserError(_("Only confirmed orders can be retransmitted via EDI."))
        """
        self.write({'edi_invoice_sent': False})
        self.write({'inv_send_requested': True})
        return

    def DoPost(self,record_id):
        record = self.env['account.move'].browse(record_id)
        return record.action_post()
        #return super().action_draft()

    def DoSetToDraft(self,record_id):
        record = self.env['account.move'].browse(record_id)

        if record.state not in ('cancel', 'posted'):
            raise UserError(_("Only posted/cancelled journal entries can be reset to draft."))
        if record.need_cancel_request:
            raise UserError(_("You can't reset to draft those journal entries. You need to request a cancellation instead."))

        record._check_draftable()
        # We remove all the analytics entries for this journal
        record.mapped('line_ids.analytic_line_ids').unlink()
        record.mapped('line_ids').remove_move_reconcile()
        record.state = 'draft'

        record._detach_attachments()
        record.write({"state":"draft","line_ids":record['line_ids']})
        return True        
        
        return record.button_draft()
        #return super().action_draft()

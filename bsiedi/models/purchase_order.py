from odoo import api, fields, models

class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    edi_orig_send_date = fields.Datetime(string='Original Send Date', required=False,readonly=True)
    edi_chg_send_date = fields.Datetime(string='Change Send Date', required=False,readonly=True)
    edi_order_ack = fields.Boolean(string='Acknowledged?', required=False,readonly=True)
    edi_order_rej = fields.Boolean(string='Rejected?', required=False,readonly=True)
    purch_ord_tags = fields.Many2many('crm.tag',string='Tags')
    edi_purch_order_sent = fields.Boolean(string='Sent via EDI?', required=False,readonly=True)
    po_send_requested = fields.Boolean(string='Send PO Requested?', required=False,readonly=True)
    hide_edi_info = fields.Boolean(string='Hide EDI Info', compute="_compute_hide_edi_info")
    hide_850_info = fields.Boolean(string='Hide 850 Info', compute="_compute_hide_850_info")
    hide_850_resend_button = fields.Boolean(string='Hide 850 resend button', compute="_compute_hide_850_button")

    @api.depends('partner_id')
    def _compute_hide_edi_info(self):
        # simple logic, but you can do much more here
        self.hide_edi_info = (not self.partner_id.tp_id)

    @api.depends('partner_id')
    def _compute_hide_850_info(self):
        self.hide_850_info = (self.state != 'purchase') or (not self.partner_id.tp_id.requires_edi_po)
    
    @api.depends('partner_id')
    def _compute_hide_850_button(self):
        self.hide_850_resend_button = (self.state != 'purchase') or (not self.partner_id.tp_id.requires_edi_po) or (self.po_send_requested)

    def action_resend850(self):
        """ Mark the given draft quotation(s) as sent.

        :raise: UserError if any given SO is not in draft state.
        """
        if any(order.state != 'purchase' for order in self):
            raise UserError(_("Only confirmed orders can be retransmitted via EDI."))
        self.write({'po_send_requested': True})
        return

    def DoConfirm(self,record_id,skip_backorder2,cancel_backorder2):
        record = self.env['purchase.order'].browse(record_id)
        return record.with_context(skip_backorder=skip_backorder2,skip_immediate=True,cancel_backorder=cancel_backorder2).button_confirm()
        #return super().action_draft()

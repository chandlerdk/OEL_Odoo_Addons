from odoo import api, fields, models

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    edi_order = fields.Boolean(string='EDI Order?', required=False,readonly=True)
    alt_oe_po_no = fields.Char(string='Alternate Purchase Order', required=False,readonly=False)
    alt_oe_po_no2 = fields.Char(string='Alternate Purchase Order 2', required=False,readonly=False)
    dealer_order_number = fields.Char(string='Dealer Order Order', required=False,readonly=False)
    orig_ship_via_cd = fields.Char(string='Original Ship Via from EDI', required=False,readonly=True)
    edi_order_reject = fields.Boolean(string='Reject?', required=False)
    edi_acknowledgement_sent = fields.Boolean(string='Acknowledgement Sent?', required=False,readonly=True)
    ack_send_requested = fields.Boolean(string='Send Acknowledgement Requested?', required=False,readonly=True)
    edi_warehouseorder_sent = fields.Boolean(string='Warehouse Order Sent?', required=False,readonly=True)
    edi_warehouseshipmentconf_received = fields.Boolean(string='Warehouse Shipment Confirmation Recevied?', required=False,readonly=True)
    whsord_send_requested = fields.Boolean(string='Send Warehouse Order Requested?', required=False,readonly=True)
    hide_edi_info = fields.Boolean(string='Hide EDI Info', compute="_compute_hide_edi_info")
    hide_940_info = fields.Boolean(string='Hide 940 Info', compute="_compute_hide_940_info")
    hide_855_info = fields.Boolean(string='Hide 855 Info', compute="_compute_hide_855_info")
    hide_940_send_button = fields.Boolean(string='Hide 940 Send Button', compute="_compute_hide_940_send_button")
    hide_855_send_button = fields.Boolean(string='Hide 855 Send Button', compute="_compute_hide_855_send_button")

    isa_control_number = fields.Char(string='EDI ISA Control Number', required=False,readonly=False)
    gs_control_number = fields.Char(string='EDI GS Control Numbner', required=False,readonly=False)
    st_control_number = fields.Char(string='EDI ST Control Number', required=False,readonly=False)

    @api.depends('partner_id')
    def _compute_hide_edi_info(self):
        # simple logic, but you can do much more here
        self.hide_edi_info = (not self.partner_id.tp_id)

    @api.depends('partner_id')
    def _compute_hide_940_info(self):
        # simple logic, but you can do much more here
        self.hide_940_info = (not self.warehouse_id.contract_warehouse) or (self.state != "sale")

    @api.depends('partner_id')
    def _compute_hide_855_info(self):
        # simple logic, but you can do much more here
        self.hide_855_info = (not self.partner_id.tp_id.requires_edi_ack) or (self.state != 'sale' and self.state != 'cancel')
    
    @api.depends('partner_id')
    def _compute_hide_940_send_button(self):
        # simple logic, but you can do much more here
        self.hide_940_send_button = (not self.warehouse_id.contract_warehouse) or (self.state != "sale") or (self.whsord_send_requested)

    @api.depends('partner_id')
    def _compute_hide_855_send_button(self):
        # simple logic, but you can do much more here
        self.hide_855_send_button = (not self.partner_id.tp_id.requires_edi_ack) or (self.state != 'sale') or (self.ack_send_requested)
    
    def action_resend940(self):
        """ Mark the given draft quotation(s) as sent.

        :raise: UserError if any given SO is not in draft state.
        """
        if any(order.state != 'sale' for order in self):
            raise UserError(_("Only confirmed orders can be retransmitted via EDI."))
        self.write({'edi_warehouseorder_sent': False})
        self.write({'whsord_send_requested': True})
        return
        
    def action_resend855(self):
        """ Mark the given draft quotation(s) as sent.

        :raise: UserError if any given SO is not in draft state.
        """
        """if any(order.state != 'sale' for order in self):
            raise UserError(_("Only confirmed orders can be retransmitted via EDI."))
        """
        self.write({'edi_acknowledgement_sent': False})
        self.write({'ack_send_requested': True})
        return

    def DoSetToDraft(self,record_id):
        record = self.env['sale.order'].browse(record_id)
        return record.action_draft()
        #return super().action_draft()

    def DoCancel(self,record_id):
        record = self.env['sale.order'].browse(record_id)
        return record._action_cancel()
        #return super().action_draft()

    def DoConfirm(self,record_id):
        record = self.env['sale.order'].browse(record_id)
        return record.action_confirm()
        #return super().action_draft()

    def DoUnlock(self,record_id):
        record = self.env['sale.order'].browse(record_id)
        return record.action_unlock()
        #return super().action_draft()

    def DoLock(self,record_id):
        record = self.env['sale.order'].browse(record_id)
        return record.action_lock()
        #return super().action_draft()

    def DoCreateInvoice(self,record_id):
        record = self.env['sale.order'].browse(record_id)
        if record.invoice_status == 'to invoice':
            return record._create_invoices()
        #return super().action_draft()

    def DoCreateAndPostInvoice(self,record_id):
        record = self.env['sale.order'].browse(record_id)
        if record.invoice_status == 'to invoice':
            invoice = record._create_invoices()
            if invoice:
                invoice.action_post()
                return invoice
        #return super().action_draft()

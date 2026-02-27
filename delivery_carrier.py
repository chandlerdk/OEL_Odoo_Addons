from odoo import api, fields, models

class StockPicking(models.Model):
    _inherit = 'stock.picking'

    bill_of_lading_ref = fields.Char(string='Bill of Lading', required=False,readonly=False)
    carton_label_request_queued = fields.Boolean(string='Carton Labels Queued?', required=False,readonly=True)
    carton_labels_ready = fields.Boolean(string='Carton Labels Ready?', required=False,readonly=True)
    asn_transmitted = fields.Boolean(string='ASN Transmitted?', required=False,readonly=True)
    carton_label_requested = fields.Boolean(string='Carton Labels Requested?', required=False,readonly=True)
    asn_send_requested = fields.Boolean(string='Send ASN Requested?', required=False,readonly=True)
    hide_asn_button = fields.Boolean(string='Hide ASN Button', compute="_compute_hide_asn_button")
    hide_label_button = fields.Boolean(string='Hide Label Button', compute="_compute_hide_label_button")
    hide_asn_info = fields.Boolean(string='Hide ASN Info', compute="_compute_hide_asn_info")
    hide_label_info = fields.Boolean(string='Hide Label Info', compute="_compute_hide_label_info")
    hide_routereq_button = fields.Boolean(string='Hide RouteRequerst Button', compute="_compute_hide_routereq_button")
    hide_routereq_info = fields.Boolean(string='Hide RouteRequest Info', compute="_compute_hide_routereq_info")
    route_send_requested = fields.Boolean(string='Send Request for Routing?', required=False,readonly=True)
    route_req_sent = fields.Boolean(string='Routing Request Sent?', required=False,readonly=True)
    route_resp_received = fields.Boolean(string='Routing Response Received?', required=False,readonly=True)
    pickup_appt_number = fields.Char(string='Pick up Appointment Number', required=False,readonly=False)
    pickup_date_early = fields.Datetime('Pick up date (early)', required=False)
    pickup_date_late = fields.Datetime('Pick up date (late)', required=False)
    carrier_scac = fields.Char(string='Carrier SCAC', required=False,readonly=False)
    trailer_number = fields.Char(string='Trailer Number', required=False,readonly=False)
    seal_number = fields.Char(string='Seal Number', required=False,readonly=False)
    est_volume = fields.Float('Estimated Lading Volume',digits=(16, 6), required=False)    
    est_weight = fields.Float('Estimated Weight',digits=(16, 6), required=False)    
    est_lading_qty = fields.Integer('Estimated Lading Quantity',required=False)    

    @api.depends('partner_id')
    def _compute_hide_routereq_button(self):
        tpid = self.pulltp()
        if tpid is None:
            self.hide_routereq_button=True
        else:
            self.hide_routereq_button = (not tpid.requires_edi_routereq) or (self.state != "done") or (self.route_send_requested == True)

    @api.depends('partner_id')
    def _compute_hide_asn_button(self):
        tpid = self.pulltp()
        if tpid is None:
            self.hide_asn_button=True
        else:
            self.hide_asn_button = (not tpid.requires_edi_asn) or (self.state != "done") or (self.asn_send_requested == True)

    @api.depends('partner_id')
    def _compute_hide_label_button(self):
        tpid = self.pulltp()
        if tpid is None:
            self.hide_label_button=True
        else:
            self.hide_label_button = (not tpid.requires_compliant_labels) or (self.state != "done") or (self.carton_label_requested and not self.carton_label_request_queued)

    @api.depends('partner_id')
    def _compute_hide_routereq_info(self):
        tpid = self.pulltp()
        if tpid is None:
            self.hide_routereq_info=True
        else:
            self.hide_routereq_info = (not tpid.requires_edi_routereq)

    @api.depends('partner_id')
    def _compute_hide_asn_info(self):
        tpid = self.pulltp()
        if tpid is None:
            self.hide_asn_info=True
        else:
            self.hide_asn_info = (not tpid.requires_edi_asn)

    @api.depends('partner_id')
    def _compute_hide_label_info(self):
        tpid = self.pulltp()
        if tpid is None:
            self.hide_label_info=True
        else:
            self.hide_label_info = (not tpid.requires_compliant_labels)

    def pulltp(self):
        if self.partner_id.tp_id.id != 0:
            return self.partner_id.tp_id
        else:
            if self.partner_id.parent_id.id != 0:
                return self.partner_id.parent_id.tp_id
            
    
    def action_requestroute(self):
        self.write({'route_resp_received': False})
        self.write({'route_req_sent': False})
        self.write({'route_send_requested': True})
        return
    
    def action_requestlabels(self):
        self.write({'carton_label_request_queued': False})
        self.write({'carton_label_requested': True})
        return

    def action_resent856(self):
        self.write({'asn_transmitted': False})
        self.write({'asn_send_requested': True})
        return

    def DoConfirm(self,record_id):
        record = self.env['stock.picking'].browse(record_id)
        return record.action_confirm()
        #return super().action_draft()

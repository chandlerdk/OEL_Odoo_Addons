from odoo import models, fields, api
from datetime import date, timedelta

class Queueing(models.Model):
    _name = 'bsiedi.queueing'
    _description = 'EDI Transaction Queuing'

    name = fields.Char(string='Name')
    value = fields.Integer(string='Value')
    
    @api.model
    def get_queue_castlegatewhsord_dataset(self,tp_rec_id:int,offset:int,limit:int):
        # Logic to retrieve or compute the dataset

        tp_record = self.env['bsiedi.tradingpartner'].search([("id",'=',tp_rec_id)])
        whsord_locs = tp_record.whsord_locs

        whsord_locs2 = []
        for whs in whsord_locs:
            whsord_locs2.append(whs.id)
        records = self.env['sale.order'].search([("warehouse_id",'in',whsord_locs2),
                                                 ("whsord_send_requested",'=',True),
                                                 ("state",'=','sale')])
        #,offset=0,limit=200
        dataset = []
        for record in records:
            dataset.append({
                'id': record.id,
                'name': record.name,
                'retailer_id': record.partner_id.castlegate_retailer_id,
                'edi_warehouseorder_sent': record.edi_warehouseorder_sent,
                'whsord_send_requested': record.whsord_send_requested
            })
        return dataset

    @api.model
    def get_queue_castlegatewhsord_autosend_dataset(self,tp_rec_id:int,offset:int,limit:int):
        # Logic to retrieve or compute the dataset

        tp_record = self.env['bsiedi.tradingpartner'].search([("id",'=',tp_rec_id)])
        whsord_locs = tp_record.whsord_locs

        whsord_locs2 = []
        for whs in whsord_locs:
            whsord_locs2.append(whs.id)
        records = self.env['sale.order'].search([("warehouse_id",'in',whsord_locs2),
                                                 ("whsord_send_requested",'=',True),
                                                 ("state",'=','sale')])
        #,offset=0,limit=200
        dataset = []
        for record in records:
            tp_id = self.pulltp(record.partner_id)
            if tp_id != 0:
                whsord_locs_tp = []
                for whs in tp_id.whsord_locs_autosend:
                    whsord_locs_tp.append(whs.id)
                if record.warehouse_id in whsord_locs_tp:
                    dataset.append({
                        'id': record.id,
                        'name': record.name,
                        'retailer_id': record.partner_id.castlegate_retailer_id,
                        'edi_warehouseorder_sent': record.edi_warehouseorder_sent,
                        'whsord_send_requested': record.whsord_send_requested
                    })
        return dataset

    @api.model
    def get_queue_invoices_dataset(self,days_back:int,offset:int,limit:int):
        # Logic to retrieve or compute the dataset

        date_begin = date.today()
        date_begin = date_begin + timedelta(days=(0-days_back))
        records = self.env['account.move'].search([("invoice_date",'>=',date_begin),
                                                 ("move_type",'=','out_invoice'),
                                                 ("state",'=','posted')])
        #,offset=0,limit=200
        dataset = []
        for record in records:
            tp_id = self.pulltp(record.partner_id)
            if tp_id != 0:
                if tp_id.requires_edi_inv==True:
                    dataset.append({
                        'id': record.id,
                        'name': record.name,
                        'ord_no': record.invoice_origin,
                        'inv_dt': record.invoice_date,
                        'inv_send_requested': record.inv_send_requested,
                        'tp_id_id': tp_id.id,
                        'tp_id': tp_id.name
                    })

        records = self.env['account.move'].search([("inv_send_requested",'=',True),
                                                 ("move_type",'=','out_invoice'),
                                                 ("state",'=','posted')])
        for record in records:
            tp_id = self.pulltp(record.partner_id)
            if tp_id != 0:
                if tp_id.requires_edi_inv==True:
                    dataset.append({
                        'id': record.id,
                        'name': record.name,
                        'ord_no': record.invoice_origin,
                        'inv_dt': record.invoice_date,
                        'inv_send_requested': record.inv_send_requested,
                        'tp_id_id': tp_id.id,
                        'tp_id': tp_id.name
                    })
        
        return dataset

    @api.model
    def get_queue_cmemo_dataset(self,days_back:int,offset:int,limit:int):
        # Logic to retrieve or compute the dataset

        date_begin = date.today()
        date_begin = date_begin + timedelta(days=(0-days_back))
        records = self.env['account.move'].search([("invoice_date",'>=',date_begin),
                                                 ("move_type",'=','out_refund'),
                                                 ("state",'=','posted')])
        #,offset=0,limit=200
        dataset = []
        for record in records:
            tp_id = self.pulltp(record.partner_id)
            if tp_id != 0:
                if tp_id.requires_edi_cmemo==True:
                    dataset.append({
                        'id': record.id,
                        'name': record.name,
                        'ord_no': record.invoice_origin,
                        'inv_dt': record.invoice_date,
                        'inv_send_requested': record.inv_send_requested,
                        'tp_id_id': tp_id.id,
                        'tp_id': tp_id.name
                    })

        records = self.env['account.move'].search([("inv_send_requested",'=',True),
                                                 ("move_type",'=','out_refund'),
                                                 ("state",'=','posted')])
        for record in records:
            tp_id = self.pulltp(record.partner_id)
            if tp_id != 0:
                if tp_id.requires_edi_cmemo==True:
                    dataset.append({
                        'id': record.id,
                        'name': record.name,
                        'ord_no': record.invoice_origin,
                        'inv_dt': record.invoice_date,
                        'inv_send_requested': record.inv_send_requested,
                        'tp_id_id': tp_id.id,
                        'tp_id': tp_id.name
                    })
        
        return dataset

    @api.model
    def get_queue_poa_dataset(self,days_back:int,offset:int,limit:int):
        # Logic to retrieve or compute the dataset

        date_begin = date.today()
        date_begin = date_begin + timedelta(days=(0-days_back))
        records = self.env['sale.order'].search([("date_order",'>=',date_begin)])
        #,offset=0,limit=200
        dataset = []
        for record in records:
            tp_id = self.pulltp(record.partner_id)
            if tp_id != 0 and (record.state=='done' or record.state=='sale' or record.state=='cancel'):
                if tp_id.requires_edi_ack==True:
                    dataset.append({
                        'id': record.id,
                        'name': record.name,
                        'state': record.state,
                        'partner_id': record.partner_id.id,
                        'partner_shipping_id': record.partner_shipping_id.id,
                        'edi_acknowledgement_sent': record.edi_acknowledgement_sent,
                        'ack_send_requested': record.ack_send_requested,
                        'create_date': record.create_date,
                        'tp_id_id': tp_id.id,
                        'tp_id': tp_id.name
                    })

        records = self.env['sale.order'].search([("ack_send_requested",'=',True)])
        for record in records:
            tp_id = self.pulltp(record.partner_id)
            if tp_id != 0 and (record.state=='done' or record.state=='sale' or record.state=='cancel'):
                if tp_id.requires_edi_ack==True:
                    dataset.append({
                        'id': record.id,
                        'name': record.name,
                        'state': record.state,
                        'partner_id': record.partner_id.id,
                        'partner_shipping_id': record.partner_shipping_id.id,
                        'edi_acknowledgement_sent': record.edi_acknowledgement_sent,
                        'ack_send_requested': record.ack_send_requested,
                        'create_date': record.create_date,
                        'tp_id_id': tp_id.id,
                        'tp_id': tp_id.name
                    })
        
        return dataset

    @api.model
    def get_queue_asn_dataset(self,days_back:int,offset:int,limit:int):
        # Logic to retrieve or compute the dataset

        date_begin = date.today()
        date_begin = date_begin + timedelta(days=(0-days_back))
        records = self.env['stock.picking'].search([("date_done",'>=',date_begin),
                                                   ("state",'=','done'),
                                                   ("picking_type_code",'=','outgoing')])
        #,offset=0,limit=200
        dataset = []
        for record in records:
            tp_id = self.pulltp(record.partner_id)
            if tp_id != 0:
                if tp_id.requires_edi_asn==True:
                    dataset.append({
                        'id': record.id,
                        'name': record.name,
                        'partner_id': record.partner_id.id,
                        'asn_send_requested': record.asn_send_requested,
                        'tp_id_id': tp_id.id,
                        'tp_id': tp_id.name
                    })

        records = self.env['stock.picking'].search([("asn_send_requested",'=',True),
                                                   ("state",'=','done'),
                                                   ("picking_type_code",'=','outgoing')])
        for record in records:
            tp_id = self.pulltp(record.partner_id)
            if tp_id != 0:
                if tp_id.requires_edi_asn==True:
                    dataset.append({
                        'id': record.id,
                        'name': record.name,
                        'partner_id': record.partner_id.id,
                        'asn_send_requested': record.asn_send_requested,
                        'tp_id_id': tp_id.id,
                        'tp_id': tp_id.name
                    })

        return dataset

    @api.model
    def get_queue_label_dataset(self,days_back:int,offset:int,limit:int):
        # Logic to retrieve or compute the dataset

        date_begin = date.today()
        date_begin = date_begin + timedelta(days=(0-days_back))
        records = self.env['stock.picking'].search([("date_done",'>=',date_begin),
                                                   ("state",'=','done'),
                                                   ("picking_type_code",'=','outgoing')])
        #,offset=0,limit=200
        dataset = []
        for record in records:
            tp_id = self.pulltp(record.partner_id)
            if tp_id != 0:
                if tp_id.requires_compliant_labels==True:
                    dataset.append({
                        'id': record.id,
                        'name': record.name,
                        'partner_id': record.partner_id.id,
                        'carton_label_requested': record.carton_label_requested,
                        'tp_id_id': tp_id.id,
                        'tp_id': tp_id.name
                    })

        records = self.env['stock.picking'].search([("carton_label_requested",'=',True),
                                                   ("state",'=','done'),
                                                   ("picking_type_code",'=','outgoing')])
        for record in records:
            tp_id = self.pulltp(record.partner_id)
            if tp_id != 0:
                if tp_id.requires_compliant_labels==True:
                    dataset.append({
                        'id': record.id,
                        'name': record.name,
                        'partner_id': record.partner_id.id,
                        'carton_label_requested': record.carton_label_requested,
                        'tp_id_id': tp_id.id,
                        'tp_id': tp_id.name
                    })

        return dataset

    @api.model
    def get_queue_routereq_dataset(self,days_back:int,offset:int,limit:int):
        # Logic to retrieve or compute the dataset

        date_begin = date.today()
        date_begin = date_begin + timedelta(days=(0-days_back))
        records = self.env['stock.picking'].search([("state",'!=','cancel'),
                                                    ("route_send_requested",'=',True),
                                                     ("picking_type_code",'=','outgoing')])
        #,offset=0,limit=200
        dataset = []
        for record in records:
            tp_id = self.pulltp(record.partner_id)
            if tp_id != 0:
                if tp_id.requires_edi_routereq==True:
                    dataset.append({
                        'id': record.id,
                        'name': record.name,
                        'partner_id': record.partner_id.id,
                        'tp_id_id': tp_id.id,
                        'tp_id': tp_id.name
                    })

        return dataset
    
    @api.model
    def get_queue_po_dataset(self,days_back:int,offset:int,limit:int):
        # Logic to retrieve or compute the dataset

        date_begin = date.today()
        date_begin = date_begin + timedelta(days=(0-days_back))
        records = self.env['purchase.order'].search([("create_date",'>=',date_begin),
                                                   ("state",'in',['purchase','draft'])])
        #,offset=0,limit=200
        dataset = []
        for record in records:
            tp_id = self.pulltp(record.partner_id)
            if tp_id != 0:
                if tp_id.requires_edi_po==True:
                    dataset.append({
                        'id': record.id,
                        'name': record.name,
                        'partner_id': record.partner_id.id,
                        'po_send_requested': record.po_send_requested,
                        'state': record.state,
                        'tp_id_id': tp_id.id,
                        'tp_id': tp_id.name
                    })

        records = self.env['purchase.order'].search([("po_send_requested",'=',True),
                                                   ("state",'in',['purchase','draft'])])
        for record in records:
            tp_id = self.pulltp(record.partner_id)
            if tp_id != 0:
                if tp_id.requires_edi_po==True:
                    dataset.append({
                        'id': record.id,
                        'name': record.name,
                        'partner_id': record.partner_id.id,
                        'po_send_requested': record.po_send_requested,
                        'state': record.state,
                        'tp_id_id': tp_id.id,
                        'tp_id': tp_id.name
                    })

        return dataset
    
    @api.model
    def pulltp(self,partner_id):
        for i in range(1, 11):
            if partner_id.tp_id.id != 0:
                return partner_id.tp_id
            else:
                partner_id = partner_id.parent_id
        return 0

from odoo import models, fields, api

class Shipment(models.Model):
    _name = 'bsiedi.shipment'
    _description = 'Shipment Data Extract for EDI'

    name = fields.Char(string='Name')
    value = fields.Integer(string='Value')
    
    @api.model
    def get_dataset(self,stock_pick_id:int,offset:int,limit:int):
        # Logic to retrieve or compute the dataset

        dataset = []
        
        s = self.env['stock.picking'].search([("id",'=',stock_pick_id)])
        for mv in s.move_ids:
            for mv_l in mv.move_line_ids:

                exp_dt = ""
                use_dt = ""
                if 'expiration_date' in self.env['stock.lot']._fields:
                    exp_dt = mv_l.lot_id.expiration_date
                    use_dt = mv_l.lot_id.use_date
                    
                dataset.append({
                    'stock_picking_id': s.id,
                    'stock_move_id': mv.id,
                    'stock_move_line_id': mv_l.id,
                    'stock_picking_name': s.name,
                    'stock_picking_has_packages': s.has_packages,
                    'stock_picking_location_id': s.location_id.id,
                    'stock_picking_location_name': s.location_id.name,
                    'sale_id': s.sale_id.id,
                    'sale_id_name': s.sale_id.name,
                    'sale_line_id': mv.sale_line_id.id,
                    'scheduled_date': s.scheduled_date,
                    'partner_id': s.partner_id.id,
                    'stock_picking_state': s.state,
                    'carrier_id': s.carrier_id.id,
                    'carrier_name': s.carrier_id.name,
                    'carrier_tracking_ref': s.carrier_tracking_ref,
                    'bill_of_lading_ref': s.bill_of_lading_ref,
                    'shipping_weight': s.shipping_weight,
                    'pickup_appt_number': s.pickup_appt_number,
                    'carrier_scac': s.carrier_scac,
                    'trailer_number': s.trailer_number,
                    'seal_number': s.seal_number,
                    'product_id': mv_l.product_id.id,
                    'product_name': mv_l.product_id.name,
                    'product_default_code': mv_l.product_id.default_code,
                    'product_uom_id': mv_l.product_uom_id.id,
                    'product_uom_name': mv_l.product_uom_id.name,
                    'quantity': mv_l.quantity,
                    'lot_id': mv_l.lot_id.id,
                    'lot_name': mv_l.lot_id.name,
                    'lot_exp': exp_dt,
                    'lot_bestby': use_dt,
                    'lot_mfgdt': mv_l.lot_id.create_date,
                    'package_id': mv_l.package_id.id,
                    'package_name': mv_l.package_id.name,
                    'result_package_id': mv_l.result_package_id.id,
                    'result_package_name': mv_l.result_package_id.name,
                    'parent_package_id': mv_l.parent_package_id.id,
                    'parent_package_name': mv_l.parent_package_id.name
                })
        return dataset


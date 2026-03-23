from odoo import models, fields, api

class InvStat(models.Model):
    _name = 'bsiedi.invstat'
    _description = 'Inventory Status for EDI'

    name = fields.Char(string='Name')
    value = fields.Integer(string='Value')
    
    @api.model
    def get_dataset(self,tp_rec_id:int,cus_id:int,offset:int,limit:int):
        # Logic to retrieve or compute the dataset

        tp_record = self.env['bsiedi.tradingpartner'].search([("id",'=',tp_rec_id)])
        inv_feed_locs = tp_record.inv_feed_locs
        if len(inv_feed_locs)==0:
            inv_feed_locs = self.env['ir.config_parameter'].get_param('bsiedi.inv_feed_locs')
        
        records = self.env['bsiedi.itemxref'].search([("trading_partner.id",'=',tp_record.id),
                                                    '|',("effective_date",'<=',fields.Date.today()),("effective_date",'=',False),
                                                    '|',("expiration_date",'>=',fields.Date.today()),("expiration_date",'=',False),
                                                    ("product_id.active","=",True)])
        #,offset=0,limit=200
        dataset = []
        loc_string = format(",".join([str(i.id) for i in inv_feed_locs]))
        warehouse_string = format(",".join([str(i.warehouse_id.id) for i in inv_feed_locs]))
        for record in records:
            
            qty_on_hand = 0.0
            qty_allocated = 0.0
            qty_on_order = 0.0
            earliest_date_available = fields.Datetime(default='2050-01-01')
            whs_list = []
            if record.product_id.is_kits or record.product_id.product_tmpl_id.is_kits:
                qty_on_hand,whs_list = self.get_kit_quant(record.product_id.id,record.product_id.product_tmpl_id.id,loc_string)
            elif record.product_id.detailed_type!="product":
                qty_on_hand = 999999
            else:
                quant_data,qty_on_hand,qty_allocated = self.get_quant(record.product_id.id,loc_string)
                for qrec in quant_data:
                    whs_list.append([qrec[0],qrec[1],qrec[2]])
                qty_on_order,earliest_date_available = self.get_prodfutureqtyavail(record.product_id.id)

            unit_price = self.get_prodprice(record.product_id.id,cus_id)
            
            dataset.append({
                'id': record.id,
                'item_no_in': record.item_no_in,
                'product_id': record.product_id.id,
                'product_tmpl_id': record.product_id.product_tmpl_id.id,
                'qty_on_hand': qty_on_hand,
                'qty_allocated': qty_allocated,
                'is_kit': record.product_id.is_kits,
                'warehouse_list': whs_list,
                'unit_price': unit_price,
                'color_code': record.color_code,
                'size_code': record.size_code,
                'config_code': record.config_code,
                'extra_id_1': record.extra_id_1,
                'extra_id_2': record.extra_id_2,
                'extra_id_3': record.extra_id_3,
                'buyer_style_number': record.buyer_style_number,
                'buyer_sku': record.buyer_sku,
                'gtin_12': record.gtin_12,
                'gtin_14': record.gtin_14,
                'upc': record.upc,
                'ndc': record.ndc,
                'ean': record.ean,
                'isbn': record.isbn,
                'out_uom': record.out_uom,
                'out_conv_factor': record.out_conv_factor,
                'out_conv_type': record.out_conv_type,
                'inner_pack_quantity': record.inner_pack_quantity,
                'inners_per_outer': record.inners_per_outer,
                'cartons_per_pallet': record.cartons_per_pallet,
                'layers_per_pallet': record.layers_per_pallet,
                'pack': record.pack,
                'pack_size': record.pack_size,
                'pack_uom': record.pack_uom,
                'unit_volume': record.unit_volume,
                'volume_uom': record.volume_uom,
                'out_price_conv': record.out_price_conv,
                'product_barcode': record.product_id.barcode,
                'product_description': record.product_id.description,
                'product_description_sale': record.product_id.description_sale,
                'product_name': record.product_id.name,
                'product_default_code': record.product_id.default_code,
                'product_categ_id': record.product_id.categ_id.id,
                'product_tmpl_barcode': record.product_id.product_tmpl_id.barcode,
                'product_tmpl_description': record.product_id.product_tmpl_id.description,
                'product_tmpl_description_sale': record.product_id.product_tmpl_id.description_sale,
                'product_tmpl_name': record.product_id.product_tmpl_id.name,
                'product_tmpl_default_code': record.product_id.product_tmpl_id.default_code,
                'product_tmpl_categ_id': record.product_id.product_tmpl_id.categ_id.id,
                'suppress_on_feed': record.suppress_on_feed,
                'force_to_zero': record.force_to_zero,
                'obsolete_flag': record.obsolete_flag,
                'min_qty_report': record.min_qty_report,
                'max_qty_report': record.max_qty_report,
                'qty_on_order': qty_on_order,
                'earliest_date_available': earliest_date_available,
                'check_in': tp_rec_id
            })
        return dataset

    @api.model
    def get_kit_quant(self,product_id:int,product_tmpl_id:int,loc_string):
        bom_hdr_records = self.env['mrp.bom'].search([("product_id.id",'=',product_id),("type", '=', "phantom")])
        whs_list = []
        for bom_hdr_record in bom_hdr_records:
            dblParAvail = 9999999.00
            bom_comp_records = self.env['mrp.bom.line'].search([("bom_id",'=',bom_hdr_record.id)])
            for bom_comp_record in bom_comp_records:
                
                qty_on_hand = 0.0
                qty_allocated = 0.0
                dblQtyPer = bom_comp_record.product_qty / bom_hdr_record.product_qty

                if bom_comp_record.product_id.detailed_type!="product":
                    qty_on_hand = 999999
                else:
                    quant_data,qty_on_hand,qty_allocated = self.get_quant(bom_comp_record.product_id.id,loc_string)
                    for qrec in quant_data:
                        whs_list.append([qrec[0],qrec[1] / dblQtyPer,qrec[2] / dblQtyPer])

                dblMaxProd = (qty_on_hand - qty_allocated) / dblQtyPer
                if dblMaxProd < dblParAvail:
                    dblParAvail = dblMaxProd
            return dblParAvail,whs_list

        bom_hdr_records = self.env['mrp.bom'].search([("product_tmpl_id.id",'=',product_tmpl_id),("type", '=', "phantom")])
        for bom_hdr_record in bom_hdr_records:
            dblParAvail = 9999999.00
            bom_comp_records = self.env['mrp.bom.line'].search([("bom_id",'=',bom_hdr_record.id)])
            for bom_comp_record in bom_comp_records:
                
                qty_on_hand = 0.0
                qty_allocated = 0.0
                dblQtyPer = bom_comp_record.product_qty / bom_hdr_record.product_qty

                if bom_comp_record.product_id.detailed_type!="product":
                    qty_on_hand = 999999
                else:
                    quant_data,qty_on_hand,qty_allocated = self.get_quant(bom_comp_record.product_id.id,loc_string)
                    for qrec in quant_data:
                        whs_list.append([qrec[0],qrec[1] / dblQtyPer,qrec[2] / dblQtyPer])

                dblMaxProd = (qty_on_hand - qty_allocated) / dblQtyPer
                if dblMaxProd < dblParAvail:
                    dblParAvail = dblMaxProd
            return dblParAvail,whs_list
           
        return 0.0

    @api.model
    def get_quant(self,product_id:int,loc_string):
        sql_query = """ SELECT sl.warehouse_id,COALESCE(SUM(q.quantity),0.0) as quantity,COALESCE(SUM(q.reserved_quantity),0.0) as reserved
                       FROM stock_quant as q
                       JOIN stock_location as sl on sl.id = q.location_id
                       WHERE
                           product_id = %s AND q.location_id in ("""+loc_string+""")
                       GROUP BY sl.warehouse_id     
                    """
        params = (product_id,)
        self.env.cr.execute(sql_query, params)
        result = self.env.cr.fetchall()
        qty_on_hand = 0.0
        qty_allocated = 0.0
        for rec in result:
            qty_on_hand = qty_on_hand + rec[1]
            qty_allocated = qty_allocated + rec[2]
        return result,qty_on_hand,qty_allocated

    @api.model
    def get_quant2(self,product_id:int,loc:int):
        locs = [6,8]
        loc_string = format(",".join([str(i) for i in locs]))
        sql_query = """ SELECT SUM(quantity) as quantity,SUM(reserved_quantity) as reserved
                       FROM stock_quant 
                       WHERE
                           product_id = %s AND location_id in ("""+loc_string+""")"""
        params = (product_id,)
        self.env.cr.execute(sql_query, params)
        result = self.env.cr.fetchall()
        return result

    @api.model
    def get_prodprice(self,product_id:int,customer_id:int):
        cust_record = self.env['res.partner'].search([("id",'=',customer_id)])
        product_record = self.env['product.product'].search([("id",'=',product_id)])

        pricelistitems_records = self.env['product.pricelist.item'].search([("pricelist_id",'=',cust_record.property_product_pricelist.id),
                                                                           '|',("product_id",'=',product_id),("product_tmpl_id",'=',product_record.product_tmpl_id.id),
                                                                           '|',("date_start",'<=',fields.Date.today()),("date_start",'=',False),
                                                                           '|',("date_end",'>=',fields.Date.today()),("date_end",'=',False),
                                                                           ("min_quantity",'>=',0)])
        
        for pricelistitem_record in pricelistitems_records:
            if pricelistitem_record.compute_price=="fixed":
                return pricelistitem_record.fixed_price
            elif pricelistitem_record.compute_price=="percentage":
                return product_record.lst_price * (100 - pricelistitem_record.percent_price) / 100.00
            elif pricelistitem_record.compute_price=="formula":
                return product_record.lst_price * (100 - pricelistitem_record.percent_price) / 100 + pricelistitem_record.price_surcharge
            else:
                return 9999999

        return product_record.lst_price

    @api.model
    def get_prodfutureqtyavail(self,product_id:int):
        on_order = 0.0
        earliest_available = '20500101'
        
        pol_records = self.env['purchase.order.line'].search([("product_id.id",'=',product_id),
                                                              ("date_planned",'>',fields.Date.today()),
                                                              ("order_id.state",'=',"purchase")], order="date_planned")
        #pol_records.sorted(lambda o: o.date_planned, reverse=False)
        for pol_record in pol_records:
            tmpPOLBalance = pol_record.product_qty - pol_record.qty_received
            if tmpPOLBalance>0:
                on_order = on_order + tmpPOLBalance
                if pol_record.date_planned.strftime("%Y%m%d")<earliest_available:
                    earliest_available = pol_record.date_planned.strftime("%Y%m%d")

        mfg_records = self.env['mrp.production'].search([("product_id.id",'=',product_id),
                                                              ("date_finished",'>',fields.Date.today()),
                                                              ("state",'=',"confirmed")], order="date_finished")
        for mfg_record in mfg_records:
            if mfg_record.product_qty>0:
                on_order = on_order + mfg_record.product_qty
                if mfg_record.date_finished.strftime('%Y%m%d')<earliest_available:
                    earliest_available = mfg_record.date_finished.strftime('%Y%m%d')

        return on_order,earliest_available


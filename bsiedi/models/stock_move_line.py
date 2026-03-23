from odoo import api, fields, models

class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    parent_package_id = fields.Many2one(string='Parent Package ID', comodel_name='stock.quant.package', required=False)

    def splitby(self,record_id,split_value):
        record = self.env['stock.move.line'].browse(record_id)
        if record['quantity']>split_value:
            hold_quantity = record['quantity']
            ttl = 0
            for hold_quantity in range(int(split_value),int(record['quantity'])+1,int(split_value)):
                tmp_value = 0
                if (record['quantity']-hold_quantity)>split_value:
                    tmp_value = split_value
                else:
                    tmp_value = record['quantity']-hold_quantity
                if tmp_value>0:
                    new_record = self.env['stock.move.line'].browse(record_id).copy({'quantity':tmp_value})
                ##new_record = record.copy()
                ##new_record['quantity'] = tmp_value
                ##new_record.write({'quantity':tmp_value})
                ttl += tmp_value
            ##record['quantity'] = record['quantity'] - ttl
            record.write({"quantity":(record['quantity'] - ttl)})
        return True
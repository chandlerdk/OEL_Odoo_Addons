from odoo import models, fields, api

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        """
        Override action_confirm to automatically promote prospects to customers
        when their first sales order is confirmed.
        """
        res = super(SaleOrder, self).action_confirm()
        
        for order in self:
            partner = order.partner_id
            # Check if the partner (or their parent company) is a prospect
            if partner.org_status == 'prospect':
                partner.write({'org_status': 'customer'})
            elif partner.parent_id and partner.parent_id.org_status == 'prospect':
                # If this is a contact under a company, promote the company too
                partner.parent_id.write({'org_status': 'customer'})
        
        return res

from odoo import models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def _report_order_date(self):
        self.ensure_one()
        if not self.scheduled_date:
            return ''
        return self.scheduled_date.strftime('%m-%d-%Y')

    def _report_sale_rep_name(self):
        """Sales rep comes from the commission modules, which may not be installed."""
        self.ensure_one()
        sale = self.sale_id
        if sale and 'sale_rep_id' in sale._fields and sale.sale_rep_id:
            return sale.sale_rep_id.name or ''
        return ''

    def _report_customer_number(self):
        """Partner reference comes from the partner sequence module, which may not be installed."""
        self.ensure_one()
        partner = self.sale_id.partner_id or self.partner_id
        if partner and 'partner_reference_no' in partner._fields:
            return partner.partner_reference_no or ''
        return ''

    def _report_internal_note(self):
        """Internal note comes from the RMA module, which may not be installed."""
        self.ensure_one()
        if 'internal_delivery_note' in self._fields:
            return self.internal_delivery_note or ''
        return ''

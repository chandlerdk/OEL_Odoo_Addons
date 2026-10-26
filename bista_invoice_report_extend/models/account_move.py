# -*- coding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (https://www.bistasolutions.com)
#
##############################################################################

from odoo import models


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _report_sale_order(self):
        self.ensure_one()
        sale_order = self.sudo().invoice_line_ids.sale_line_ids.order_id[:1]
        if not sale_order and self.invoice_origin:
            sale_order = self.env['sale.order'].sudo().search([('name', '=', self.invoice_origin)], limit=1)
        return sale_order

    def _report_pickings(self):
        self.ensure_one()
        return self._report_sale_order().picking_ids

    def _report_sale_rep_name(self):
        self.ensure_one()
        if 'sale_rep_id' in self._fields and self.sale_rep_id:
            return self.sale_rep_id.name
        sale_order = self._report_sale_order()
        if 'sale_rep_id' in sale_order._fields and sale_order.sale_rep_id:
            return sale_order.sale_rep_id.name
        return False

    def _report_customer_number(self):
        self.ensure_one()
        partner = self._report_sale_order().partner_id or self.partner_id
        if 'partner_reference_no' in partner._fields:
            return partner.partner_reference_no
        return False

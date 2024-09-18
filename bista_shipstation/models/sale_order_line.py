# -*- coding: utf-8 -*-
###############################################################################
#    License, author and contributors information in:                         #
#    __manifest__.py file at the root folder of this module.                  #
###############################################################################

from odoo import models, fields, api, _
from datetime import timedelta


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    tracking_ref = fields.Html(copy=False)

    def _get_tracking_ref(self):
        for x in self:
            picking_ids = x.mapped("move_ids").mapped("picking_id")
            if not picking_ids:
                continue
            done_picking = picking_ids.filtered(lambda rec: rec.state == 'done' and rec.carrier_tracking_ref)
            tracking_refs = []
            for picking in done_picking:
                ref = f"<span style='color: gray; font-style: italic'>{picking.date_done.strftime('%m/%d/%Y')}" \
                      f"</span><p><b>{picking.carrier_tracking_ref}</b></p>"
                if ref not in tracking_refs:
                    tracking_refs.append(ref)
            if len(tracking_refs):
                x.tracking_ref = "".join(tracking_refs)

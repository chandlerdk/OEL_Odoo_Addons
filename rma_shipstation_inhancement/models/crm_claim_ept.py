# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

from odoo import fields, models, api, _


RES_PARTNER = 'res.partner'
SALE_ORDER = 'sale.order'
STOCK_PICKING = 'stock.picking'
ACCOUNT_MOVE = 'account.move'
CRM_CLAIM_EPT = 'crm.claim.ept'
IR_WINDOW_ACTION = 'ir.actions.act_window'
PROCUREMENT_GROUP = 'procurement.group'


class CrmClaimEpt(models.Model):
    _inherit = "crm.claim.ept"

    rma_ship_via = fields.Char(string="Ship Via")
    no_ship_cost_synced = fields.Boolean(copy=False, string="No Shipping Cost Sync")

    bill_account = fields.Char('Account No')
    bill_postal_code = fields.Char('Postal Code')
    bill_country_code = fields.Many2one('res.country', string="Country")
    rma_carrier_id = fields.Many2one('shipstation.delivery.carrier')
    service_id = fields.Many2one('shipstation.carrier.service')


    def _prepare_delivery_line_vals(self, carrier, price_unit):
        # context = {}
        # if self.partner_id:
            # set delivery detail in the customer language
            # context['lang'] = self.partner_id.lang
            # carrier = carrier.with_context(lang=self.partner_id.lang)

        # Apply fiscal position
        # taxes = carrier.product_id.taxes_id._filter_taxes_by_company(self.company_id)
        # taxes_ids = taxes.ids
        # if self.partner_id and self.fiscal_position_id:
        #     taxes_ids = self.fiscal_position_id.map_tax(taxes).ids

        # Create the sales order line

        if carrier.product_id.description_sale:
            so_description = '%s: %s' % (carrier.name,
                                        carrier.product_id.description_sale)
        else:
            so_description = carrier.name
        values = {
            'claim_id': self.id,
            # 'name': so_description,
            # 'price_unit': price_unit,
            'done_qty': 1,
            # 'product_uom': carrier.product_id.uom_id.id,
            'product_id': carrier.product_id.id,
            # 'tax_id': [(6, 0, taxes_ids)],
            # 'is_delivery': True,
        }
        if carrier.free_over and self.currency_id.is_zero(price_unit) :
            values['name'] += '\n' + _('Free Shipping')
        # if self.claim_line_ids:
        #     values['sequence'] = self.claim_line_ids[-1].sequence + 1
        # del context
        return values

    def _create_delivery_line(self, carrier, price_unit):
        values = self._prepare_delivery_line_vals(carrier, price_unit)
        return self.env['claim.line.ept'].sudo().create(values)

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        for line in self:
            if line.partner_id:
                line.rma_ship_via = line.partner_id.ship_via

    @api.onchange('partner_id')
    def _onchange_shipstation_third_acc(self):
        for rec in self:
            if rec.partner_id:
                rec.update({
                    'bill_account': rec.partner_id.bill_account,
                    'bill_postal_code': rec.partner_id.bill_postal_code,
                    'bill_country_code': rec.partner_id.bill_country_code,
                    'rma_carrier_id': rec.partner_id.carrier_id,
                    'service_id': rec.partner_id.service_id,
                })
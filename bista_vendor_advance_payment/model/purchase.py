# -*- encoding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (http://www.bistasolutions.com)
#
##############################################################################

from itertools import groupby

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.fields import Command
from odoo.tools import float_is_zero


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    @api.depends('state', 'order_line.invoice_status', 'order_line.qty_to_invoice')
    def _get_invoiced(self):
        for order in self:
            if order.state != 'purchase':
                order.invoice_status = 'no'
                continue
            line_invoice_status = order.order_line.filtered(
                lambda line: not line.is_prepayment and not line.display_type
            ).mapped('invoice_status')
            if 'to invoice' in line_invoice_status:
                order.invoice_status = 'to invoice'
            elif line_invoice_status and all(status == 'invoiced' for status in line_invoice_status):
                order.invoice_status = 'invoiced'
            else:
                order.invoice_status = 'no'

    def _prepare_invoice(self):
        invoice_vals = super()._prepare_invoice()
        journal = self.env['account.journal'].search([
            *self.env['account.journal']._check_company_domain(self.company_id),
            ('type', '=', 'purchase'),
        ], limit=1)
        if not journal:
            raise UserError(_(
                "Please define an accounting purchase journal for the company %(company)s (%(id)s).",
                company=self.company_id.name, id=self.company_id.id))
        invoice_vals.update({
            'ref': '',
            'journal_id': journal.id,
            'invoice_user_id': self.user_id.id,
            'partner_shipping_id': self.dest_address_id.id
                                   or self.picking_type_id.warehouse_id.partner_id.id
                                   or self.partner_id.id,
            'payment_reference': self.name,
        })
        return invoice_vals

    def _get_invoice_grouping_keys(self):
        return ['company_id', 'partner_id', 'currency_id']

    def _create_invoices(self, grouped=False, final=False):
        """
        Create the bills of the POs.
        :param grouped: if True, bills are grouped by PO id. If False,
        bills are grouped by (company, partner, currency)
        :param final: if True, pre payments are deducted (refunds generated if necessary)
        :returns: created bills
        """
        if not self.env['account.move'].has_access('create'):
            if not self.has_access('write'):
                return self.env['account.move']

        precision = self.env['decimal.precision'].precision_get('Product Unit')

        invoice_vals_list = []
        for order in self:
            order = order.with_company(order.company_id)
            pending_section = None
            invoice_vals = order._prepare_invoice()
            for line in order.order_line:
                if line.display_type in ('line_section', 'line_subsection'):
                    pending_section = line
                    continue
                if line.display_type or float_is_zero(line.qty_to_invoice, precision_digits=precision):
                    continue
                if line.qty_to_invoice > 0 or (line.qty_to_invoice < 0 and final):
                    if pending_section:
                        invoice_vals['invoice_line_ids'].append(
                            Command.create(pending_section._prepare_invoice_line()))
                        pending_section = None
                    invoice_vals['invoice_line_ids'].append(Command.create(line._prepare_invoice_line()))

            if not invoice_vals['invoice_line_ids']:
                raise UserError(_("There is no invoiceable line. If a product has"
                                  " a Delivered quantities invoicing policy, please"
                                  " make sure that a quantity has been delivered."))
            invoice_vals_list.append(invoice_vals)

        if not grouped:
            new_invoice_vals_list = []
            invoice_grouping_keys = self._get_invoice_grouping_keys()
            for _grouping_keys, invoices in groupby(
                    invoice_vals_list,
                    key=lambda x: [x.get(grouping_key) for grouping_key in invoice_grouping_keys]):
                origins = set()
                payment_refs = set()
                refs = set()
                ref_invoice_vals = None
                for invoice_vals in invoices:
                    if not ref_invoice_vals:
                        ref_invoice_vals = invoice_vals
                    else:
                        ref_invoice_vals['invoice_line_ids'] += invoice_vals['invoice_line_ids']
                    origins.add(invoice_vals['invoice_origin'])
                    payment_refs.add(invoice_vals['payment_reference'])
                    refs.add(invoice_vals['ref'])
                ref_invoice_vals.update({
                    'ref': ', '.join(filter(None, refs))[:2000],
                    'invoice_origin': ', '.join(origins),
                    'payment_reference': len(payment_refs) == 1 and payment_refs.pop() or False,
                })
                new_invoice_vals_list.append(ref_invoice_vals)
            invoice_vals_list = new_invoice_vals_list

        # Created in sudo so that purchase users without billing rights can
        # still bill a purchase order.
        moves = self.env['account.move'].sudo().with_context(
            default_move_type='in_invoice', call_from_down_or_prepayment=True,
        ).create(invoice_vals_list)
        if final:
            moves.filtered(lambda m: m.currency_id.round(m.amount_total) < 0).action_switch_move_type()
        for move in moves:
            move.message_post_with_source(
                'mail.message_origin_link',
                render_values={'self': move, 'origin': move.line_ids.purchase_line_ids.order_id},
                subtype_xmlid='mail.mt_note',
            )
        return moves

    def copy_data(self, default=None):
        vals_list = super().copy_data(default=default)
        if default and 'order_line' in default:
            return vals_list
        for order, vals in zip(self, vals_list):
            lines = order.order_line.filtered(lambda line: not line.is_prepayment)
            vals['order_line'] = [Command.create(line_vals) for line_vals in lines.copy_data()]
        return vals_list

    def action_view_invoice_advance_payment(self):
        invoices = self.invoice_ids
        action = self.env['ir.actions.act_window']._for_xml_id('account.action_move_in_invoice_type')
        if len(invoices) > 1:
            action['domain'] = [('id', 'in', invoices.ids)]
        elif len(invoices) == 1:
            form_view = [(self.env.ref('account.view_move_form').id, 'form')]
            if 'views' in action:
                action['views'] = form_view + [(state, view) for state, view in action['views'] if view != 'form']
            else:
                action['views'] = form_view
            action['res_id'] = invoices.id
        else:
            action = {'type': 'ir.actions.act_window_close'}

        context = {
            'default_move_type': 'in_invoice',
        }
        if len(self) == 1:
            context.update({
                'default_partner_id': self.partner_id.id,
                'default_partner_shipping_id': self.partner_id.id,
                'default_invoice_payment_term_id':
                    self.payment_term_id.id
                    or self.partner_id.property_supplier_payment_term_id.id
                    or self.env['account.move'].default_get(['invoice_payment_term_id']).get('invoice_payment_term_id'),
                'default_invoice_origin': self.name,
                'default_invoice_user_id': self.user_id.id,
            })
        action['context'] = context
        return action


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    is_prepayment = fields.Boolean(
        string="Is a pre payment", help="Pre payments are made when"
                                        " creating invoices from a purchase order."
                                        " They are not copied when duplicating a purchase order.")
    invoice_status = fields.Selection([
        ('invoiced', 'Fully Invoiced'),
        ('to invoice', 'To Invoice'),
        ('no', 'Nothing to Invoice')
    ], string='Invoice Status', compute='_compute_invoice_status',
        store=True, readonly=True, default='no')

    @api.depends('state', 'product_uom_qty', 'qty_received', 'qty_to_invoice', 'qty_invoiced')
    def _compute_invoice_status(self):
        """
        - no: the PO is not confirmed, or nothing is left to bill.
        - to invoice: ordered quantity is not fully billed, or a quantity is
          still to bill (see `qty_to_invoice`).
        - invoiced: nothing is left to bill and the PO has bills.
        """
        precision = self.env['decimal.precision'].precision_get('Product Unit')
        for line in self:
            if line.state != 'purchase':
                line.invoice_status = 'no'
            elif line.product_qty - line.qty_invoiced > 0:
                line.invoice_status = 'to invoice'
            elif not float_is_zero(line.qty_to_invoice, precision_digits=precision):
                line.invoice_status = 'to invoice'
            elif line.order_id.invoice_ids:
                line.invoice_status = 'invoiced'
            else:
                line.invoice_status = 'no'

    def _prepare_invoice_line(self):
        self.ensure_one()
        res = self._prepare_account_move_line()
        res.update({
            'sequence': self.sequence,
            'purchase_line_ids': [Command.link(self.id)],
        })
        return res

    def _check_line_unlink(self):
        """
        Lines cannot be deleted if the order is confirmed; prepayment
        lines that have not been billed yet bypass that exception.
        :returns: set of lines that cannot be deleted
        """
        return self.filtered(lambda line: line.state == 'purchase'
                                          and (line.invoice_lines or not line.is_prepayment))

    @api.ondelete(at_uninstall=False)
    def _unlink_except_purchase(self):
        lines = self.filtered(lambda line: not line.display_type)
        if lines._check_line_unlink():
            raise UserError(_(
                "You can not remove an order line once the purchase order"
                " is confirmed.\nYou should rather set the quantity to 0."))

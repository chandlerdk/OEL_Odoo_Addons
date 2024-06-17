# -*- encoding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (http://www.bistasolutions.com)
#
##############################################################################

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_is_zero
from itertools import groupby


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    @api.depends('state', 'order_line.qty_invoiced',
                 'order_line.qty_received',
                 'order_line.product_qty')
    def _get_invoiced(self):
        unconfirmed_orders = self.filtered(
            lambda so: so.state not in ['purchase', 'done'])
        unconfirmed_orders.invoice_status = 'no'
        confirmed_orders = self - unconfirmed_orders
        if not confirmed_orders:
            return
        line_invoice_status_all = [
            (d['order_id'][0], d['invoice_status'])
            for d in self.env['purchase.order.line'].read_group([
                ('order_id', 'in', confirmed_orders.ids),
                ('is_prepayment', '=', False),
                ('display_type', '=', False),
            ],
                ['order_id', 'invoice_status'],
                ['order_id', 'invoice_status'], lazy=False)]
        for order in confirmed_orders:
            line_invoice_status = [d[1]
                                   for d in line_invoice_status_all if d[0] == order.id]
            if order.state not in ('purchase', 'done'):
                order.invoice_status = 'no'
            elif any(invoice_status == 'to invoice'
                     for invoice_status in line_invoice_status):
                order.invoice_status = 'to invoice'
            elif line_invoice_status and all(invoice_status == 'invoiced'
                                             for invoice_status in line_invoice_status):
                order.invoice_status = 'invoiced'
            else:
                order.invoice_status = 'no'

    def _prepare_invoice(self):
        """
        Prepare the dict of values to create the new invoice for a purchase order.
        This method may be overridden to implement custom invoice generation
        (making sure to call super() to establish a clean extension chain).
        """
        self.ensure_one()
        # ensure a correct context for the _get_default_journal method and
        # company-dependent fields

        # Commented below lines as _get_default_journal() function is not available in v16
        # self = self.with_context(
        #     default_company_id=self.company_id.id, force_company=self.company_id.id)
        # journal = self.env['account.move'].with_context(
        #     default_move_type='in_invoice')._get_default_journal()
        self = self.with_context(
            default_company_id=self.company_id.id).with_company(self.company_id.id)
        journal = self.env['account.journal'].search([('type', '=', 'purchase'),
                                                      ('company_id', '=', self.company_id.id)], limit=1)
        if not journal:
            raise UserError(_("Please define an accounting purchase "
                              "journal for the company %s (%s).") % (
                                self.company_id.name, self.company_id.id))

        invoice_vals = {
            'ref': '',
            'move_type': 'in_invoice',
            # 'narration': self.internal_communication,
            'currency_id': self.currency_id.id,
            # 'campaign_id': self.campaign_id.id,
            # 'medium_id': self.medium_id.id,
            # 'source_id': self.source_id.id,
            'invoice_user_id': self.user_id and self.user_id.id,
            # 'team_id': self.team_id.id,
            'partner_id': self.partner_id.id,
            'partner_shipping_id': self.dest_address_id.id
                                   or self.picking_type_id.warehouse_id.partner_id.id
                                   or self.partner_id.id,
            'partner_bank_id': self.company_id.partner_id.bank_ids[:1].id,
            'fiscal_position_id': self.fiscal_position_id.id
                                  or self.partner_id.property_account_position_id.id,
            'journal_id': journal.id,  # company comes from the journal
            'invoice_origin': self.name,
            'invoice_payment_term_id': self.payment_term_id.id,
            'payment_reference': self.name,
            # 'document_ids': [(6, 0, self.document_ids.ids)],
            # 'transaction_ids': [(6, 0, self.transaction_ids.ids)],
            'invoice_line_ids': [],
            'company_id': self.company_id.id,
            # 'project_id': self.project_id.id,
        }
        return invoice_vals

    def _get_invoice_grouping_keys(self):
        return ['company_id', 'partner_id', 'currency_id']

    def _create_invoices(self, grouped=False, final=False):
        """
        Create the invoice associated to the PO.
        :param grouped: if True, invoices are grouped by PO id. If False,
        invoices are grouped by (partner_invoice_id, currency)
        :param final: if True, refunds will be generated if necessary
        :returns: list of created invoices
        """
        if not self.env['account.move'].check_access_rights('create', False):
            try:
                self.check_access_rights('write')
                self.check_access_rule('write')
            except AccessError:
                return self.env['account.move']

        precision = self.env['decimal.precision'].precision_get(
            'Product Unit of Measure')

        # 1) Create invoices.
        invoice_vals_list = []
        for order in self:
            pending_section = None

            # Invoice values.
            invoice_vals = order._prepare_invoice()

            # Invoice line values (keep only necessary sections).
            for line in order.order_line:
                if line.display_type == 'line_section':
                    pending_section = line
                    continue
                if float_is_zero(line.qty_to_invoice, precision_digits=precision):
                    continue
                if line.qty_to_invoice > 0 or (line.qty_to_invoice < 0 and final):
                    if pending_section:
                        invoice_vals['invoice_line_ids'].append(
                            (0, 0, pending_section._prepare_invoice_line()))
                        pending_section = None
                    invoice_vals['invoice_line_ids'].append(
                        (0, 0, line._prepare_invoice_line()))

            if not invoice_vals['invoice_line_ids']:
                raise UserError(_("There is no invoiceable line. If a product has"
                                  " a Delivered quantities invoicing policy, please"
                                  " make sure that a quantity has been delivered."))

            invoice_vals_list.append(invoice_vals)

        if not invoice_vals_list:
            raise UserError(_(
                "There is no invoiceable line. If a product has a Delivered "
                "quantities invoicing policy, please make sure that a "
                "quantity has been delivered."))

        # 2) Manage 'grouped' parameter: group by (partner_id, currency_id).
        if not grouped:
            new_invoice_vals_list = []
            invoice_grouping_keys = self._get_invoice_grouping_keys()
            for grouping_keys, invoices in groupby(invoice_vals_list,
                                                   key=lambda x: [x.get(grouping_key) for grouping_key in
                                                                  invoice_grouping_keys]):
                origins = set()
                payment_refs = set()
                refs = set()
                ref_invoice_vals = None
                for invoice_vals in invoices:
                    if not ref_invoice_vals:
                        ref_invoice_vals = invoice_vals
                    else:
                        ref_invoice_vals['invoice_line_ids'] += \
                            invoice_vals['invoice_line_ids']
                    origins.add(invoice_vals['invoice_origin'])
                    payment_refs.add(invoice_vals['payment_reference'])
                    refs.add(invoice_vals['ref'])
                ref_invoice_vals.update({
                    'ref': ', '.join(refs)[:2000],
                    'invoice_origin': ', '.join(origins),
                    'payment_reference': len(payment_refs) == 1 and
                                         payment_refs.pop() or False,
                })
                new_invoice_vals_list.append(ref_invoice_vals)
            invoice_vals_list = new_invoice_vals_list

        # 3) Create invoices.
        # Manage the creation of invoices in sudo because a salesperson
        # must be able to generate an invoice from a
        # purchase order without "billing" access rights. However, he should
        # not be able to create an invoice from scratch.
        moves = self.env['account.move'].sudo().with_context(
            default_move_type='in_invoice', call_from_down_or_prepayment=True). \
            create(invoice_vals_list)
        # 4) Some moves might actually be refunds: convert them if the
        # total amount is negative
        # We do this after the moves have been created since we need taxes,
        # etc. to know if the total
        # is actually negative or not
        if final:
            moves.sudo().filtered(lambda m: m.amount_total <
                                            0).action_switch_move_type()
        for move in moves:
            move.message_post_with_source('mail.message_origin_link',
                                        render_values={'self': move, 'origin':
                                            move.line_ids.mapped(
                                                'purchase_line_ids.order_id')},
                                        subtype_xmlid='mail.mt_note',
                                        )
        return moves

    def copy_data(self, default=None):
        if default is None:
            default = {}
        if 'order_line' not in default:
            default['order_line'] = [(0, 0, line.copy_data(
            )[0]) for line in self.order_line.filtered(lambda l: not l.is_prepayment)]
        return super(PurchaseOrder, self).copy_data(default)

    def action_view_invoice_advance_payment(self):
        invoices = self.mapped('invoice_ids')
        action = self.env.ref('account.action_move_in_invoice_type').read()[0]
        if len(invoices) > 1:
            action['domain'] = [('id', 'in', invoices.ids)]
        elif len(invoices) == 1:
            form_view = [(self.env.ref('account.view_move_form').id, 'form')]
            if 'views' in action:
                action['views'] = form_view + \
                                  [(state, view)
                                   for state, view in action['views'] if view != 'form']
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
                    self.payment_term_id.id or
                    self.partner_id.property_payment_term_id.id
                    or self.env['account.move'].default_get(['invoice_payment_term_id']).get('invoice_payment_term_id'),
                'default_invoice_origin': self.mapped('name'),
                'default_user_id': self.user_id.id,
            })
        action['context'] = context
        return action



class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    @api.depends('state', 'product_uom_qty', 'qty_received',
                 'qty_to_invoice', 'qty_invoiced')
    def _compute_invoice_status(self):
        """
        Compute the invoice status of a SO line. Possible statuses:
        - no: if the SO is not in status 'purchase' or 'done', we
        consider that there is nothing to invoice. This is also hte
        default value if the conditions of no other status is met.
        - to invoice: we refer to the quantity to invoice of the line.
        Refer to method `_get_to_invoice_qty()` for more information
        on how this quantity is calculated. - upselling: this is possible
        only for a product invoiced on ordered quantities for which we
        delivered more than expected. The could arise if, for example,
        a project took more time than expected but we decided not to
        invoice the extra cost to the client. This occurs onyl in state
        'purchase', so that when a SO is set to done, the upselling opportunity
          is removed from the list. - invoiced: the quantity invoiced
          is larger or equal to the quantity ordered.
        """
        precision = self.env['decimal.precision'].precision_get(
            'Product Unit of Measure')
        for line in self:
            if line.state not in ('purchase', 'done'):
                line.invoice_status = 'no'
            elif line.product_qty - line.qty_invoiced > 0:
                line.invoice_status = 'to invoice'
            elif not float_is_zero(line.qty_to_invoice, precision_digits=precision):
                line.invoice_status = 'to invoice'

            elif float_is_zero(line.qty_to_invoice, precision_digits=precision) and line.order_id.invoice_ids:
                line.invoice_status = 'invoiced'
            else:
                line.invoice_status = 'no'

    @api.depends('qty_invoiced', 'qty_received', 'product_qty', 'order_id.state')
    def _get_to_invoice_qty(self):
        """
        Compute the quantity to invoice. If the invoice policy is order,
        the quantity to invoice is
        calculated from the ordered quantity. Otherwise, the quantity
        delivered is used.
        """
        for line in self:
            if line.order_id.state in ['purchase', 'done']:
                if line.product_id.purchase_method in ['purchase']:
                    # invoice_policy == 'order'
                    line.qty_to_invoice = line.product_qty - line.qty_invoiced
                else:
                    line.qty_to_invoice = line.qty_received - line.qty_invoiced
            else:
                line.qty_to_invoice = 0

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

    qty_to_invoice = fields.Float(
        compute='_get_to_invoice_qty', string='To Invoice Quantity',
        store=True, readonly=True,
        digits='Product Unit of Measure')

    def _prepare_invoice_line(self):
        """
        Prepare the dict of values to create the new invoice line for a
        purchase order line.
        :param qty: float quantity to invoice
        """
        self.ensure_one()
        res = {
            # 'display_type': self.display_type,
            'sequence': self.sequence,
            'name': self.name,
            'product_id': self.product_id.id,
            'product_uom_id': self.product_uom.id,
            'quantity': self.qty_to_invoice,
            # 'discount': self.discount,
            'price_unit': self.price_unit,
            'tax_ids': [(6, 0, self.taxes_id.ids)],
            # Commented below fields as analytic_account_id and analytic_tag_ids is no longer available in v16
            # 'analytic_account_id': self.account_analytic_id.id,
            # 'analytic_tag_ids': [(6, 0, self.analytic_tag_ids.ids)],
            'purchase_line_ids': [(4, self.id)],
            'purchase_line_id': self.id,
        }
        if self.display_type:
            res['account_id'] = False
            res['display_type'] = self.display_type
        return res

    def _check_line_unlink(self):
        """
        Check wether a line can be deleted or not.

        Lines cannot be deleted if the order is confirmed; prepayment
        lines who have not yet been invoiced bypass that exception.
        :rtype: recordset purchase.order.line
        :returns: set of lines that cannot be deleted
        """
        return self.filtered(lambda line: line.state in ('purchase', 'done')
                                          and (line.invoice_lines or not line.is_prepayment))

    def unlink(self):
        for record in self:
            if record._check_line_unlink():
                raise UserError(_(
                    "You can not remove an order line once the purchase order"
                    " is confirmed.\nYou should rather set the quantity to 0."))
            self.env.cr.execute(
                "delete from purchase_order_line where id = %s", (record.id,))
        return True

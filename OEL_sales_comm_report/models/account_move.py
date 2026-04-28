from odoo import models, fields, api


class AccountMove(models.Model):
    _inherit = 'account.move'

    # Stored, no compute => install/upgrade does NOT recompute across history
    x_commission_payment_date = fields.Date(
        string="Commission Payment Date",
        index=True,
        copy=False,
        readonly=True,
        help="Materialized payment date (last payment date) used for commission reporting."
    )

    x_freight_total = fields.Monetary(
        string="Freight Total",
        currency_field='currency_id',
        copy=False,
        readonly=True,
        help="Total of freight lines (Delivery - Freight Out) on the invoice."
    )

    x_amount_commissionable = fields.Monetary(
        string="Commissionable Total",
        currency_field='currency_id',
        copy=False,
        readonly=True,
        help="Untaxed amount minus Freight Total."
    )

    @api.model
    def _commission_prepare_for_range(self, date_from, date_to, freight_product_name="Delivery - Freight Out"):
        """
        Prepare (materialize) commission reporting fields for invoices whose LAST payment date
        falls within [date_from, date_to).

        - Finds paid invoices where last payment journal entry date is in range
        - Writes x_commission_payment_date
        - Computes freight total and commissionable total for those invoices

        Returns: list of invoice IDs that match.
        """
        company_ids = self.env.companies.ids
        cr = self.env.cr

        # 1) Update x_commission_payment_date for invoices whose LAST payment date is in range
        #    We use partial reconciles on receivable lines and take MAX(payment_move.date) for move_type='entry'
        #    (excludes credit notes/refunds from being considered a "payment")
        sql_update_paid_in_range = """
            WITH inv_lines AS (
                SELECT aml.id AS aml_id, aml.move_id AS invoice_id
                FROM account_move_line aml
                JOIN account_move inv ON inv.id = aml.move_id
                JOIN account_account aa ON aa.id = aml.account_id
                WHERE inv.move_type = 'out_invoice'
                  AND inv.state = 'posted'
                  AND inv.company_id = ANY(%s)
                  AND aml.display_type IS NULL
                  AND aa.account_type = 'asset_receivable'
            ),
            pairs AS (
                SELECT il.invoice_id, pm.date AS pay_date
                FROM account_partial_reconcile pr
                JOIN inv_lines il ON pr.debit_move_id = il.aml_id
                JOIN account_move_line pml ON pml.id = pr.credit_move_id
                JOIN account_move pm ON pm.id = pml.move_id
                WHERE pm.state = 'posted'
                  AND pm.move_type = 'entry'

                UNION ALL

                SELECT il.invoice_id, pm.date AS pay_date
                FROM account_partial_reconcile pr
                JOIN inv_lines il ON pr.credit_move_id = il.aml_id
                JOIN account_move_line pml ON pml.id = pr.debit_move_id
                JOIN account_move pm ON pm.id = pml.move_id
                WHERE pm.state = 'posted'
                  AND pm.move_type = 'entry'
            ),
            agg AS (
                SELECT invoice_id, MAX(pay_date) AS last_pay_date
                FROM pairs
                GROUP BY invoice_id
            ),
            res AS (
                SELECT agg.invoice_id, agg.last_pay_date
                FROM agg
                JOIN account_move inv ON inv.id = agg.invoice_id
                WHERE agg.last_pay_date >= %s
                  AND agg.last_pay_date < %s
                  AND inv.payment_state = 'paid'
                  AND inv.company_id = ANY(%s)
                  AND inv.state = 'posted'
                  AND inv.move_type = 'out_invoice'
            )
            UPDATE account_move am
               SET x_commission_payment_date = res.last_pay_date
              FROM res
             WHERE am.id = res.invoice_id
         RETURNING am.id;
        """
        cr.execute(sql_update_paid_in_range, (company_ids, date_from, date_to, company_ids))
        invoice_ids = [r[0] for r in cr.fetchall()]

        if not invoice_ids:
            return []

        # 2) Find freight product ids (by template name) to be robust with variants
        tmpl = self.env['product.template'].search([('name', '=', freight_product_name)], limit=1)
        freight_product_ids = tmpl.product_variant_ids.ids if tmpl else []

        # 3) Set defaults for the month invoices
        cr.execute(
            """
            UPDATE account_move
               SET x_freight_total = 0,
                   x_amount_commissionable = amount_untaxed
             WHERE id = ANY(%s)
            """,
            (invoice_ids,)
        )

        # 4) Update freight totals + commissionable for those invoices (only if product exists)
        if freight_product_ids:
            cr.execute(
                """
                WITH f AS (
                    SELECT aml.move_id AS invoice_id,
                           COALESCE(SUM(aml.price_subtotal), 0) AS freight_total
                      FROM account_move_line aml
                     WHERE aml.move_id = ANY(%s)
                       AND aml.display_type IS NULL
                       AND aml.product_id = ANY(%s)
                     GROUP BY aml.move_id
                )
                UPDATE account_move am
                   SET x_freight_total = f.freight_total,
                       x_amount_commissionable = am.amount_untaxed - f.freight_total
                  FROM f
                 WHERE am.id = f.invoice_id
                """,
                (invoice_ids, freight_product_ids)
            )

        return invoice_ids

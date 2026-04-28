from odoo import models, fields
from datetime import date


class SalesCommissionPrepareWizard(models.TransientModel):
    _name = 'sales.commission.prepare.wizard'
    _description = 'Prepare Sales Commission Review'

    month = fields.Selection(
        [(str(m), date(2000, m, 1).strftime('%B')) for m in range(1, 13)],
        string="Month",
        required=True,
        default='12',
    )
    year = fields.Integer(
        string="Year",
        required=True,
        default=lambda self: fields.Date.today().year,
    )

    def action_prepare_and_open(self):
        self.ensure_one()

        m = int(self.month)
        y = int(self.year)

        date_from = date(y, m, 1)
        if m == 12:
            date_to = date(y + 1, 1, 1)
        else:
            date_to = date(y, m + 1, 1)

        # Prepare only that month’s invoices (fast SQL + limited scope)
        self.env['account.move']._commission_prepare_for_range(
            date_from=date_from,
            date_to=date_to,
            freight_product_name="Delivery - Freight Out",
        )

        action = self.env.ref('sales_commission_review.action_commission_review_report').read()[0]
        action['domain'] = [
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('payment_state', '=', 'paid'),
            ('x_commission_payment_date', '>=', date_from),
            ('x_commission_payment_date', '<', date_to),
        ]
        action['context'] = {
            **(self.env.context or {}),
            'search_default_group_by_team': 1,
            'search_default_group_by_salesperson': 1,
        }
        return action

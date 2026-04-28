# -*- coding: utf-8 -*-
from odoo import models


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _get_mail_template(self):
        """Override to use Credit Memo email template for out_refund moves."""
        if self.move_type == 'out_refund':
            return 'OEL_credit_memo_report.email_template_credit_memo'
        return super()._get_mail_template()

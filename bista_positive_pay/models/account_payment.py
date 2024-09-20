import base64

from odoo import models
from odoo.exceptions import UserError
import datetime


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    def report_positive_pay(self):
        file_content = "\n".join(payment.get_line() for payment in self)
        file_data = base64.b64encode(file_content.encode('utf-8'))
        today = datetime.date.today().strftime("%m-%d-%Y")
        attachment = self.env['ir.attachment'].create({
            'name': f'Positive Pay {today}.txt',
            'type': 'binary',
            'datas': file_data,
            'store_fname': 'positive_pay.txt',
            'mimetype': 'text/plain',
        })
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true' % (attachment.id),
            'target': 'new',
        }

    def get_line(self):
        if not self.check_number:
            raise UserError(f"Payment does not have a check number. {self.name}")
        if not self.partner_id:
            raise UserError(f"Payment does not have a a partner assigned. {self.name}")
        if not self.amount:
            raise UserError(f"Payment amount should not be zero. {self.name}")
        account_number = self.journal_id.bank_account_id.acc_number
        check = self.check_number
        date = self.date.strftime("%m%d%y")
        amount = str(float(self.amount)).replace(".", "")
        partner = self.partner_id.name
        check_type = "I" if self.state == 'posted' else "V"
        check_number = check + str(date)

        reserve_1_4 = "    "
        available_characters = 22 - (len(account_number) + len(check_number))
        if available_characters < 0:
            if len(check_number) > 15:
                raise UserError("Check number is exceeding the 9 digit limit")
            if len(account_number) > 17:
                raise UserError("Account number is exceeding the 17 digit limit")
        if len(str(amount)) > 9:
            raise UserError("Amount is exceeding the 9 digit limit")

        reserve_27_29 = "  "
        amount_padding = (9 - len(str(amount)))
        amount_holder = (amount_padding * "0") + str(amount)[:9]
        reserve_28 = " "
        return (reserve_1_4 + account_number[:17] + str(
            available_characters * "0") + check_number + reserve_27_29 + amount_holder +
                reserve_28 + check_type + partner[:128])

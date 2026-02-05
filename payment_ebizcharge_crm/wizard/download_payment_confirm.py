from odoo import fields, models, api, _


class MessageConfirmWizard(models.TransientModel):
    _name = 'message.confirm.wizard'
    _description = "Message confirm Wizard"

    name = fields.Char('Message Name', readonly=True)
    text = fields.Text('Message', readonly=True)
    wizard_id = fields.Many2one('ebiz.download.payments', readonly=True)
    is_sale = fields.Boolean(string='Sales Order')


    def action_confirm(self):
        self.wizard_id.with_context({'from_confirm_wizard': True}).fetch_again()


    def action_apply_pay(self):
        self.env.context.get('kwargs_values')[0]['payment_on_account'] = True
        self.wizard_id.mark_as_applied(self.env.context.get('kwargs_values'))
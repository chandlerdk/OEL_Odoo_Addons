# -*- coding: utf-8 -*-

from odoo import api, fields, models
from datetime import datetime
from odoo.exceptions import UserError
from .ebiz_charge import message_wizard


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    def get_timezone(self):
        return self.env.user.tz

    next_execution_date_invoice = fields.Datetime(string='Next Execution Date Invoice', config_parameter='payment_ebizcharge_crm.next_execution_date_invoice')
    interval_number_invoice = fields.Integer(string="Interval", config_parameter='payment_ebizcharge_crm.interval_number_invoice' , default=1)
    interval_unit_invoice = fields.Selection(string="Frequency", selection=[
        ('minutes', 'Minutes'),
        ('hours', 'Hours')], config_parameter='payment_ebizcharge_crm.interval_unit_invoice', required=True)
    time_zone = fields.Char(string='Time Zone', readonly=True,  default=get_timezone)
    scheduler_act_deact = fields.Boolean(string='Scheduler Check', default=False, config_parameter='payment_ebizcharge_crm.scheduler_act_deact')
    invoice_cron_job = fields.Boolean('Download and apply payments', default=False, config_parameter='payment_ebizcharge_crm.invoice_cron_job')

    @api.model
    def default_get(self, fields):
        res = super(ResConfigSettings, self).default_get(fields)
        profile = self.env['ebizcharge.instance.config'].search([], limit=1)
        if profile:
            profile.action_update_profiles('ebizcharge.instance.config')
        return res

    def activate_invoice_cron_job(self):
        done = False
        while not done:
            try:
                scheduler = self.env.ref('payment_ebizcharge_crm.received_payments')
                if scheduler:
                    scheduler.active = True
                    scheduler.nextcall = self.next_execution_date_invoice if self.next_execution_date_invoice else datetime.now()
                    scheduler.interval_number = self.interval_number_invoice
                    scheduler.interval_type = self.interval_unit_invoice
                    self.env['ir.config_parameter'].set_param('payment_ebizcharge_crm.scheduler_act_deact', True)
                    self.env['ir.config_parameter'].set_param('payment_ebizcharge_crm.time_zone', self.env.user.tz)
                    self.scheduler_act_deact = True
                self.env.cr.commit()
                done = True
            except Exception as e:
                raise UserError(str(e))
        else:
            return message_wizard('Scheduler Activated!')

    def deactivate_invoice_cron_job(self):
        done = False
        while not done:
            try:
                scheduler = self.env.ref('payment_ebizcharge_crm.received_payments')
                if scheduler:
                    scheduler.active = False
                    self.env['ir.config_parameter'].set_param('payment_ebizcharge_crm.scheduler_act_deact', False)
                    self.env['ir.config_parameter'].set_param('payment_ebizcharge_crm.time_zone', self.env.user.tz)
                    self.scheduler_act_deact = False

                self.env.cr.commit()
                done = True
            except Exception as e:
                raise UserError(str(e))
        else:
            return message_wizard('Scheduler Deactivated!')




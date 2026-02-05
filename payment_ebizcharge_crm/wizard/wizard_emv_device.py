from odoo import fields, models, _, api

class EmvDeviceWizard(models.TransientModel):
    _name = 'wizard.emv.device'
    _description = "EMV Device Configure"

    is_default_emv = fields.Boolean(string="Set as Default")
    emv_device_id = fields.Many2one("ebizcharge.emv.device", "Device Name")
    pin = fields.Char(string="EMV Pin")
    merchant_id = fields.Many2one("ebizcharge.instance.config", "Merchant")
    source_key = fields.Char(string="EMV Source Key", readonly=True)
    emv_device_key = fields.Char(string="EMV Device Key", readonly=True)

    def action_add_device(self):
        if self.emv_device_id:
            if self.emv_device_id:
                default_device = False
                selected_devices = self.env['ebizcharge.emv.device'].search([('merchant_id','=', self.merchant_id.id),('is_default_emv', '=', True)])
                if self.is_default_emv:
                    for rec in selected_devices:
                        rec.is_default_emv = False
                    default_device = self.emv_device_id.is_default_emv = True
                sequence = 2
                if not self.merchant_id.emv_device_ids:
                    default_device = True
                    sequence = 1
                self.emv_device_id.update({
                    'merchant_id': self.merchant_id.id,
                    'is_default_emv': default_device,
                    'sequence': sequence,
                })
                self.merchant_id.is_emv_enabled=True




    @api.onchange('emv_device_id')
    def onchange_device_id(self):
        if self.emv_device_id:
            self.emv_device_key = self.emv_device_id.source_key

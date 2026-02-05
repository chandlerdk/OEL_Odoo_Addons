from odoo import fields, models,api
import json


class MultiSyncMessage(models.TransientModel):
    _name = 'wizard.multi.sync.message'
    _description = "Wizard Multi Sync Message"

    name = fields.Char("Name")
    success_count = fields.Integer("Success Count")
    failed_count = fields.Integer("Failed Count")
    customer_lines_ids = fields.One2many('wizard.multi.sync.customer.line', 'message_id')
    lines_ids = fields.One2many('wizard.multi.sync.message.line', 'message_id')
    order_lines_ids = fields.One2many('wizard.multi.sync.order.line', 'message_id')
    invoice_lines_ids = fields.One2many('wizard.multi.sync.invoice.line', 'message_id')
    total = fields.Integer('Total')

    def _compute_success_failed(self):
        success = 0
        failed = 0
        for item in self.lines_ids:
            if item.record_message in ['Success', 'Record already exists']:
                success += 1
            else:
                failed += 1

        self.total = len(self.lines_ids)
        self.success_count = success
        self.failed_count = failed


class MultiSyncMessageLine(models.TransientModel):
    _name = "wizard.multi.sync.message.line"
    _description = "Wizard Multi Sync Message Line"

    message_id = fields.Many2one('wizard.multi.sync.message')
    record_name = fields.Char("Product")
    record_message = fields.Char("Status")


class MultiSyncMessageCustomerLine(models.TransientModel):
    _name = "wizard.multi.sync.customer.line"
    _description = "Wizard Multi Sync Customer Line"

    message_id = fields.Many2one('wizard.multi.sync.message')
    customer_name = fields.Char("Customer")
    customer_id = fields.Char("Customer ID")
    record_message = fields.Char("Status")


class MultiSyncOrderLine(models.TransientModel):
    _name = "wizard.multi.sync.order.line"
    _description = "Wizard Multi Sync Order Line"

    message_id = fields.Many2one('wizard.multi.sync.message')
    customer_name = fields.Char("Customer")
    customer_id = fields.Char("Customer ID")
    order_number = fields.Char("Order Number")
    record_message = fields.Char("Status")


class MultiSyncInvoiceLine(models.TransientModel):
    _name = "wizard.multi.sync.invoice.line"
    _description = "Wizard Multi Sync Invoice Line"

    message_id = fields.Many2one('wizard.multi.sync.message')
    customer_name = fields.Char("Customer")
    customer_id = fields.Char("Customer ID")
    invoice_number = fields.Char("Number")
    record_message = fields.Char("Status")

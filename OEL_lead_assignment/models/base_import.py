from odoo import models

class BaseImport(models.TransientModel):   # FIX: TransientModel not Model
    _inherit = 'base_import.import'

    def execute_import(self, fields, columns, options, dryrun=False):
        """Override to pass dryrun flag to context so crm_lead can detect it."""
        context = dict(self.env.context, import_dryrun=dryrun)
        return super(BaseImport, self.with_context(context)).execute_import(fields, columns, options, dryrun)

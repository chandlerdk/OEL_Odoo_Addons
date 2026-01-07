from odoo import models


class BaseImport(models.TransientModel):
    _inherit = 'base_import.import'

    def execute_import(self, fields, columns, options, dryrun=False):
        """Pass import context flags so crm.lead can detect real import vs other creates."""
        context = dict(
            self.env.context,
            import_dryrun=dryrun,
            lead_created_via_import=True,  # <-- NEW: true for BOTH dryrun and real import
        )
        return super(BaseImport, self.with_context(context)).execute_import(fields, columns, options, dryrun=dryrun)

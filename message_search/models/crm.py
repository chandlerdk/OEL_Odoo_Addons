from odoo import models, fields, api


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    @api.model_create_multi
    def create(self, vals_list):
        res = super().create(vals_list)
        for lead in res:
            if lead.partner_id:
                lead.message_subscribe(partner_ids=[lead.partner_id.id])
        return res

    def write(self, vals):
        res = super().write(vals)
        if 'partner_id' in vals:
            for lead in self.filtered('partner_id'):
                lead.message_subscribe(partner_ids=[lead.partner_id.id])
        return res
from odoo import models, fields, api


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    @api.model_create_multi
    def create(self, vals_list):
        res = super(CrmLead, self).create(vals_list)
        for lead in res:
            if lead.partner_id:
                lead.message_subscribe(partner_ids=[lead.partner_id.id])
        return res

    def write(self, vals):
        res = super(CrmLead, self).write(vals)
        for lead in self:
            if 'partner_id' in vals:
                lead.message_subscribe(partner_ids=[lead.partner_id.id])
        return res
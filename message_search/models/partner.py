from odoo import models, fields, api, _


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def action_show_related_chatter(self):
       # get followers of this partner and messages related to those followers
        for partner in self:
            partner_ids = self.env['res.partner'].search([('parent_id', 'child_of', partner.id)])
            self._cr.execute("""
                SELECT mm.id
                FROM res_partner l
                JOIN mail_followers mf ON mf.partner_id = l.id
                JOIN mail_message mm ON (mf.res_id = mm.res_id AND mf.res_model = mm.model)
                WHERE l.id in %s
            """, (tuple(partner_ids.ids),))
            message_ids = self._cr.fetchall()
            message_ids = [x[0] for x in message_ids]
            print(message_ids)
            action = self.env.ref('message_search.action_user_message').read()[0]
            action['domain'] = [('id', 'in', message_ids)]
            return action




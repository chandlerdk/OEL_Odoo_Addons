from odoo import models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def action_show_related_chatter(self):
        """Messages of every record followed by this partner or its contacts."""
        self.ensure_one()
        partners = self.env['res.partner'].search([('id', 'child_of', self.id)])
        self.env.cr.execute("""
                 SELECT mm.id
                  FROM res_partner l
                  JOIN mail_followers mf ON mf.partner_id = l.id
                  JOIN mail_message mm ON (mf.res_id = mm.res_id AND mf.res_model = mm.model)
                  WHERE l.id in %s
        """, (tuple(partners.ids),))
        message_ids = [row[0] for row in self.env.cr.fetchall()]
        action = self.env['ir.actions.act_window']._for_xml_id('message_search.action_user_message')
        action['domain'] = [('id', 'in', message_ids)]
        return action

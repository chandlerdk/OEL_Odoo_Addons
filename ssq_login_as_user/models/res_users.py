from odoo import models


class ResUsers(models.Model):
    _inherit = "res.users"

    def _is_impersonate_user(self):
        self.ensure_one()
        return self.has_group("ssq_login_as_user.impersonate_user_group")

    def impersonate_user(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "target": "self",
            "url": "/web/impersonate?uid={}".format(self.id),
        }

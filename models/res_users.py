# Part of Odoo. See COPYRIGHT & LICENSE files for full copyright and licensing details.

from odoo import api, models, fields
from odoo.osv import expression


class Users(models.Model):
    _inherit = "res.users"

    @api.depends("groups_id")
    def _compute_model_access(self):
        """
        Calculate model access base on user
        """
        for record in self:
            access_ids = (
                self.env["ir.model.access"].sudo().search([("group_id", "=", False)])
            )
            record.model_access = [(5,)]
            if access_ids:
                record.model_access = [(4, access_id) for access_id in access_ids.ids]

    model_access = fields.Many2many(
        "ir.model.access",
        "user_model_access_rel",
        "user_id",
        "model_access_id",
        string="Access Controls",
        compute="_compute_model_access",
        copy=True,
        store=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        """
        User creation time set model access
        """
        records = super(Users, self).create(vals_list)
        if records:
            records._compute_model_access()
        return records

    @api.model
    def name_search(self, name="", args=None, operator="ilike", limit=100):
        """
        Search user base on the dashboard access
        """
        args = list(args or [])
        context = dict(self.env.context)
        if context.get("user_group_ids") and context.get("access_by") == "access_group":
            group_ids = self.env["res.groups"].browse(context["user_group_ids"])
            domain = [
                ("id", "!=", self.env.user.id),
                ("id", "in", group_ids.mapped("users").ids),
                ("share", "=", False),
            ]
            if context.get("shared_user_ids"):
                domain = [("id", "in", context["shared_user_ids"])]
            args = expression.AND([domain, args])
        if context.get("access_by") == "user" and context.get("user_ids"):
            domain = [("id", "!=", self.env.user.id), ("share", "=", False)]
            args = expression.AND([domain, args])
        return super(Users, self).name_search(
            name=name, args=args, operator=operator, limit=limit
        )

    def has_read_access(self, model_id):
        """
        Check user has read access for the object or not
        """
        # return self.env[model_id.model].with_user(self.id).has_access("read")
        # check_access_rule :return: None if the operation is allowed
        has_access = (
            self.env[model_id.model].with_user(self.id).check_access_rule("read")
        )
        if not has_access:
            return True
        return False

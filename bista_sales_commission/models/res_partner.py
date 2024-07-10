from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import email_normalize


class Partner(models.Model):
    _inherit = 'res.partner'

    is_portal_user = fields.Boolean()

    @api.model
    def create(self, vals_list):
        ret = super(Partner, self).create(vals_list)
        if ret.is_portal_user:
            ret._grant_portal_access()
        return ret

    def _grant_portal_access(self):
        self.ensure_one()
        group_portal = self.env.ref('base.group_portal')
        group_public = self.env.ref('base.group_public')
        user_sudo = self.user_id.sudo()
        if not self.email:
            self.email = self.name
        self._assert_user_email_uniqueness()
        if not user_sudo:
            company = self.company_id or self.env.company
            user_sudo = self.sudo().with_company(company.id)._create_user()
            user_sudo.write({'active': True, 'groups_id': [(4, group_portal.id), (3, group_public.id)]})

    def _create_user(self):
        """
            Create a portal user
        """
        login = (self.email or self.name).lower()
        return self.env['res.users'].with_context(no_reset_password=True)._create_user_from_template({
            'email': login,
            'login': login,
            'partner_id': self.id,
            'company_id': self.env.company.id,
            'company_ids': [(6, 0, self.env.company.ids)],
        })

    def _assert_user_email_uniqueness(self):
        """Check that the email can be used to create a new user."""
        if not self.email:
            raise UserError(_("Please enter the email address or type a unique username in the email field.."))

        partner = self.env['res.partner'].search_count(
            [('email', '=', self.email),
             ('id', '!=', self.id),
             ('active', 'in', [False, True])], limit=1)

        if partner:
            raise UserError(_('The contact "%s" has the same email as an existing user', self.name))

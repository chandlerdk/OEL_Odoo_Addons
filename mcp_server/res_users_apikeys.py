"""Let users mint ``mcp``-scoped API keys straight from the standard wizard.

Core's "New API Key" wizard hardcodes ``_generate(None, ...)`` -- every key it
creates is a global (NULL-scope) key with full XML-RPC/JSON-RPC access. We add a
``scope_mode`` choice to the wizard: picking "MCP only" flows an
``mcp_api_key_scope`` context flag into an overridden ``_generate`` so the stored
scope becomes ``'mcp'`` -- a key that authenticates only on ``/mcp`` (see
``auth.get_user_from_api_key``). No core method body is copied.
"""

from odoo import fields, models

from odoo.addons.base.models.res_users import check_identity


class ResUsersApikeysDescription(models.TransientModel):
    _inherit = "res.users.apikeys.description"

    scope_mode = fields.Selection(
        selection=[
            ("global", "All APIs (default)"),
            ("mcp", "MCP only"),
        ],
        string="Access",
        default="global",
        required=True,
        help="Choose what this key can do.\n"
        "- All APIs (default): a normal Odoo API key with full RPC access "
        "(XML-RPC, JSON-RPC and the MCP endpoint).\n"
        "- MCP only: the key authenticates ONLY on the MCP endpoint "
        "(/mcp). It cannot be used for general RPC, so a smaller blast "
        "radius if the key is ever leaked.",
    )

    @check_identity
    def make_key(self):
        if self.scope_mode == "mcp":
            self = self.with_context(mcp_api_key_scope="mcp")
        return super().make_key()


class ResUsersApikeys(models.Model):
    _inherit = "res.users.apikeys"

    def _generate(self, scope, name):
        scope = scope or self.env.context.get("mcp_api_key_scope")
        return super()._generate(scope, name)

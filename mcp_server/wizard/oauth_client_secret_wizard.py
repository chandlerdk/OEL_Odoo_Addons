"""Admin wizard: create a confidential OAuth client, or give one a new secret.

The wizard holds only its inputs. The secret is shown once through the
table-less ``mcp.oauth.client.secret.show`` model, as core shows a new API key,
and the client stores only its hash.
"""

import logging

from authlib.common.security import generate_token

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.base.models.res_users import check_identity

from ..controllers.oauth_server import (
    _MAX_CLIENT_STRING_LENGTH,
    _MAX_REDIRECT_URI_LENGTH,
    _MAX_REDIRECT_URIS,
    CONFIDENTIAL_CLIENT_AUTH_METHODS,
    SUPPORTED_SCOPES,
    _is_allowed_redirect_uri,
)

_logger = logging.getLogger(__name__)


class McpOauthClientSecretWizard(models.TransientModel):
    _name = "mcp.oauth.client.secret.wizard"
    _description = "Confidential OAuth Client Wizard"

    client_id = fields.Many2one(
        "mcp.oauth.client",
        string="Client",
        ondelete="cascade",
        help="The client that gets a new secret. Empty to create a new client.",
    )
    client_name = fields.Char(
        string="Name",
        help="Shown on the consent screen and in the OAuth Clients list.",
    )
    redirect_uris = fields.Text(
        string="Redirect URIs",
        help="One URI per line. Use https, or http only for 127.0.0.1 or " "localhost.",
    )
    scope = fields.Char(
        default="mcp",
        help="Space-separated scopes the client may request: mcp, mcp:read or "
        "mcp:write.",
    )

    def _ensure_mcp_admin(self):
        """Gate the ``sudo()`` work below on real MCP-admin status.

        The wizard ACL already limits it to MCP administrators; this re-checks
        the real user before any sudo(), as ``mcp.oauth.client`` does.
        """
        if not self.env.user.has_group("mcp_server.group_mcp_admin"):
            raise AccessError(
                _("Only MCP administrators may manage OAuth client secrets.")
            )

    @check_identity
    def action_create(self):
        """Create the client after a password check, as for core API keys."""
        return self._create_confidential_client()

    @check_identity
    def action_regenerate(self):
        """Replace the client secret after a password check."""
        return self._regenerate_client_secret()

    def _create_confidential_client(self):
        self.ensure_one()
        self._ensure_mcp_admin()
        name = (self.client_name or "").strip()
        uris = (self.redirect_uris or "").split()
        scopes = (self.scope or "").split()
        # The same caps and redirect rules as dynamic client registration.
        if not name or len(name) > _MAX_CLIENT_STRING_LENGTH:
            raise UserError(
                _(
                    "Enter a name of at most %s characters.",
                    _MAX_CLIENT_STRING_LENGTH,
                )
            )
        if not uris or len(uris) > _MAX_REDIRECT_URIS:
            raise UserError(
                _("Enter 1 to %s redirect URIs, one per line.", _MAX_REDIRECT_URIS)
            )
        for uri in uris:
            if len(uri) > _MAX_REDIRECT_URI_LENGTH or not _is_allowed_redirect_uri(uri):
                raise UserError(
                    _(
                        "The redirect URI %s is not allowed. Use https, or http "
                        "only for 127.0.0.1 or localhost, with no user name and "
                        "no fragment.",
                        uri[:100],
                    )
                )
        if not scopes or not set(scopes) <= set(SUPPORTED_SCOPES):
            raise UserError(_("Enter a scope from: %s.", ", ".join(SUPPORTED_SCOPES)))
        # sudo: the admin ACL on clients is read-only; access checked above.
        client = (
            self.env["mcp.oauth.client"]
            .sudo()
            .create(
                {
                    "client_id": generate_token(42),
                    "client_name": name,
                    "redirect_uris": "\n".join(uris),
                    "scope": " ".join(scopes),
                    "token_endpoint_auth_method": CONFIDENTIAL_CLIENT_AUTH_METHODS,
                    "created_via": "manual",
                }
            )
        )
        return self._show_secret(client, client._generate_secret(), "created")

    def _regenerate_client_secret(self):
        self.ensure_one()
        self._ensure_mcp_admin()
        client = self.client_id
        if client.created_via != "manual":
            # A DCR client belongs to the program that registered it; a new
            # secret would break that program.
            raise UserError(
                _(
                    "Only a client that an administrator created can get a new "
                    "secret. A registered client belongs to the program that "
                    "registered it."
                )
            )
        return self._show_secret(client, client._generate_secret(), "regenerated")

    def _show_secret(self, client, secret, event):
        """Log the change, then open the one-time view of the secret.

        The secret travels in the action context only; the log line names the
        client and the acting user, never the secret.
        """
        _logger.info(
            "OAuth client %s: secret %s by user %s",
            client.client_id,
            event,
            self.env.uid,
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "mcp.oauth.client.secret.show",
            "name": _("Client Secret"),
            "views": [(False, "form")],
            "target": "new",
            "context": {
                "default_client_identifier": client.client_id,
                "default_client_secret": secret,
            },
        }


class McpOauthClientSecretShow(models.AbstractModel):
    """Shows a new client secret once. No table, like ``res.users.apikeys.show``."""

    _name = "mcp.oauth.client.secret.show"
    _description = "Show OAuth Client Secret"

    # The field 'id' is necessary for the onchange that returns the values.
    id = fields.Id()
    client_identifier = fields.Char(string="Client ID", readonly=True)
    client_secret = fields.Char(readonly=True)

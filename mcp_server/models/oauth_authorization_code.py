"""Short-lived OAuth 2.1 authorization codes for the native MCP endpoint.

A code is the one-time credential a client exchanges for tokens. Codes are
stored SHA-256-hashed, alongside their PKCE ``code_challenge`` and the resource
they are bound to (RFC 8707), and expire within a minute. The model implements
Authlib's ``AuthorizationCodeMixin`` so the authorization server reads the
redirect URI, scope and PKCE parameters straight off the record.
"""

from datetime import timedelta

from authlib.oauth2.rfc6749 import AuthorizationCodeMixin

from odoo import api, fields, models
from odoo.http import request

from .oauth_utils import sha256_hex as _sha256

# Authorization codes are single-use and very short-lived (RFC 9700): a leaked
# code is useless within a minute and after one redemption.
_CODE_TTL_SECONDS = 60


class OauthAuthorizationCode(models.Model, AuthorizationCodeMixin):
    _name = "mcp.oauth.authorization.code"
    _description = "OAuth Authorization Code"
    _rec_name = "client"
    _order = "create_date desc"

    code_hash = fields.Char(
        string="Code Hash",
        required=True,
        index=True,
        copy=False,
        help="SHA-256 hash of the authorization code; the raw code is never " "stored.",
    )
    client = fields.Many2one(
        "mcp.oauth.client",
        string="Client",
        required=True,
        index=True,
        ondelete="cascade",
        help="Client the code was issued to.",
    )
    user_id = fields.Many2one(
        "res.users",
        string="User",
        required=True,
        index=True,
        ondelete="cascade",
        help="User who approved the authorization request.",
    )
    redirect_uri = fields.Char(
        string="Redirect URI",
        help="Redirect URI the code was issued for; must match exactly at "
        "redemption.",
    )
    scope = fields.Char(
        string="Scope",
        help="Scope granted with this code.",
    )
    code_challenge = fields.Char(
        string="Code Challenge",
        help="PKCE code challenge supplied at authorize time.",
    )
    code_challenge_method = fields.Char(
        string="Code Challenge Method",
        default="S256",
        help="PKCE transform used for the code challenge (S256 only).",
    )
    resource = fields.Char(
        string="Resource",
        help="RFC 8707 resource indicator the code is bound to (token audience).",
    )
    expires_at = fields.Datetime(
        string="Expires At",
        required=True,
        index=True,
        help="Instant after which the code can no longer be redeemed.",
    )
    used = fields.Boolean(
        default=False,
        copy=False,
        help="Set once the code has been redeemed; a single-use replay is then "
        "detectable.",
    )
    refresh_family_id = fields.Char(
        string="Refresh Family",
        copy=False,
        help="Token family this code produced at redemption; lets a code replay "
        "revoke exactly that family and nothing else.",
    )

    # ------------------------------------------------------------------
    # Authorization-server persistence helpers
    # ------------------------------------------------------------------
    @api.model
    def _save_code(self, code, oauth2_request):
        """Persist a freshly issued authorization code (stored hashed)."""
        data = oauth2_request.payload.data
        vals = {
            "code_hash": _sha256(code),
            "client": oauth2_request.client.id,
            "user_id": oauth2_request.user.id,
            "redirect_uri": oauth2_request.payload.redirect_uri,
            # The consent step (oauth_server.authorize) stashes the scope actually
            # granted by the resource owner; fall back to the requested scope when
            # absent (e.g. a code minted outside that flow).
            "scope": getattr(request, "_mcp_granted_scope", None)
            or oauth2_request.scope
            or "",
            "code_challenge": data.get("code_challenge"),
            "code_challenge_method": data.get("code_challenge_method") or "S256",
            "resource": data.get("resource"),
            "expires_at": fields.Datetime.now() + timedelta(seconds=_CODE_TTL_SECONDS),
        }
        return self.create(vals)

    @api.model
    def _get_valid_code(self, code, client):
        """Return the unredeemed, unexpired code for ``client`` (or empty).

        The matching row is locked ``FOR UPDATE`` so two concurrent redemptions
        of the same code serialize: the second blocks until the first marks the
        code ``used`` and commits, then re-evaluates the ``used = FALSE``
        qualification, finds nothing and fails -- closing the single-use TOCTOU
        window that a plain read would leave open.
        """
        self.env.cr.execute(
            """
            SELECT id FROM mcp_oauth_authorization_code
            WHERE code_hash = %s AND client = %s
              AND used = FALSE AND expires_at > %s
            LIMIT 1
            FOR UPDATE
            """,
            (_sha256(code), client.id, fields.Datetime.now()),
        )
        row = self.env.cr.fetchone()
        return self.browse(row[0]) if row else self.browse()

    def _consume(self):
        """Mark the code redeemed (kept, not deleted, so replays are visible)."""
        self.write({"used": True})

    @api.model
    def _detect_reuse(self, code):
        """Revoke a token family when its already-redeemed code is replayed.

        Only a *genuinely consumed* code (``used=True``) signals reuse, so an
        expired-but-never-redeemed code (e.g. a slow legitimate client) simply
        fails with no revocation. The blast radius is exactly the token family
        the code produced -- not every token of the client+user -- so a replay
        cannot force-log-out unrelated sessions (RFC 6749 4.1.2 / RFC 9700).
        """
        spent = self.search(
            [("code_hash", "=", _sha256(code)), ("used", "=", True)], limit=1
        )
        if spent and spent.refresh_family_id:
            self.env["mcp.oauth.token"]._revoke_family(spent.refresh_family_id)

    # ------------------------------------------------------------------
    # Authlib AuthorizationCodeMixin interface
    # ------------------------------------------------------------------
    def get_redirect_uri(self):
        return self.redirect_uri

    def get_scope(self):
        return self.scope or ""

    def get_auth_time(self):
        return None

    def get_nonce(self):
        return None

"""Issued OAuth 2.1 access/refresh tokens for the native MCP endpoint.

Tokens are opaque; only their SHA-256 hashes are persisted. The model implements
Authlib's ``TokenMixin`` so the authorization and resource servers can check
client binding, scope, expiry and revocation off the stored fields. Refresh
tokens rotate on every use and share a ``refresh_family_id`` so a replayed
(already rotated) refresh token can be traced to its whole chain for revocation.
"""

import logging
import uuid
from datetime import timedelta

from authlib.oauth2.rfc6749 import TokenMixin
from authlib.oauth2.rfc6749.errors import InvalidRequestError

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from ..controllers.oauth_server import resource_url
from .oauth_utils import sha256_hex as _sha256

_logger = logging.getLogger(__name__)

# Refresh tokens rotate on every use, but still carry a hard expiry as a
# backstop so an abandoned token cannot be redeemed indefinitely.
_REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 3600

# A dynamically registered client with no live token for this long is treated as
# abandoned and garbage-collected.
_DCR_CLIENT_TTL_DAYS = 30

# GC unlink batch size: bound how many rows (plus their cascade + ORM cache) a
# single catch-up run materializes at once (mirrors mcp.log.cleanup_old_logs).
_GC_BATCH_SIZE = 1000


class OauthToken(models.Model, TokenMixin):
    _name = "mcp.oauth.token"
    _description = "OAuth Token"
    _rec_name = "client"
    _order = "create_date desc"

    access_token_hash = fields.Char(
        string="Access Token Hash",
        index=True,
        copy=False,
        help="SHA-256 hash of the access token; the raw token is never stored.",
    )
    refresh_token_hash = fields.Char(
        string="Refresh Token Hash",
        index=True,
        copy=False,
        help="SHA-256 hash of the refresh token; the raw token is never stored.",
    )
    client = fields.Many2one(
        "mcp.oauth.client",
        string="Client",
        required=True,
        index=True,
        ondelete="cascade",
        help="Client the token was issued to.",
    )
    user_id = fields.Many2one(
        "res.users",
        string="User",
        required=True,
        index=True,
        ondelete="cascade",
        help="User the token acts on behalf of.",
    )
    scope = fields.Char(
        string="Scope",
        help="Scope granted to this token.",
    )
    audience = fields.Char(
        string="Audience",
        help="RFC 8707 resource the token is bound to; checked when the token "
        "is presented.",
    )
    access_expires_at = fields.Datetime(
        string="Access Token Expiry",
        index=True,
        help="Instant after which the access token is no longer valid.",
    )
    refresh_expires_at = fields.Datetime(
        string="Refresh Token Expiry",
        index=True,
        help="Instant after which the refresh token can no longer be redeemed.",
    )
    revoked = fields.Boolean(
        default=False,
        copy=False,
        index=True,
        help="Set when the token is revoked (rotation, admin action or replay "
        "detection).",
    )
    refresh_family_id = fields.Char(
        string="Refresh Family",
        index=True,
        copy=False,
        help="Shared ID across a rotated refresh-token chain; enables reuse "
        "detection.",
    )
    # Redeclared to add a DB index: create_date backs the _order
    # ("create_date desc") for the admin Tokens list over this table.
    create_date = fields.Datetime(index=True)

    # ------------------------------------------------------------------
    # Authorization-server persistence helpers
    # ------------------------------------------------------------------
    @api.model
    def _resolve_audience_and_family(self, oauth2_request):
        """Resolve the ``(audience, refresh_family_id)`` for a token being issued.

        Audience (RFC 8707) is fixed at authorize time, so on the auth-code path
        it is taken from the redeemed code's stored ``resource`` -- mirroring how
        the scope is taken from the code -- falling back to the token-request
        value only when the code carried none. A token-step ``resource`` that
        contradicts the code's binding is rejected. On a refresh exchange the new
        token inherits the rotated-from token's family and audience.

        When neither the code nor the token request binds a resource, the
        audience defaults to this server's canonical resource URL rather than
        ``False``: a ``False`` audience fails closed at ``/mcp``
        (``_mcp_audience_matches``), so the flow would otherwise complete into a
        token that 401s on every call. This AS serves a single resource, so the
        default is unambiguous and cannot enable cross-resource passthrough.
        """
        old_refresh = getattr(oauth2_request, "refresh_token", None)
        auth_code = getattr(oauth2_request, "authorization_code", None)
        requested_resource = oauth2_request.payload.data.get("resource")

        if auth_code is not None:
            code_resource = auth_code.resource
            if (
                code_resource
                and requested_resource
                and requested_resource.rstrip("/") != code_resource.rstrip("/")
            ):
                raise InvalidRequestError(
                    "The 'resource' does not match the authorization request."
                )
            audience = code_resource or requested_resource or resource_url()
        elif old_refresh is not None:
            audience = old_refresh.audience
        else:
            audience = requested_resource or resource_url()

        family_id = old_refresh.refresh_family_id if old_refresh else uuid.uuid4().hex
        return audience, family_id

    @api.model
    def _save_token(self, token, oauth2_request):
        """Persist a freshly issued token pair (access/refresh stored hashed).

        Audience and refresh-token family are resolved by
        :meth:`_resolve_audience_and_family`.

        Scope is sourced AUTHORITATIVELY from the redeemed authorization code
        (or, on a refresh exchange, the rotated-from token) -- mirroring how the
        audience is taken from the code -- rather than from Authlib's ``token``
        dict. That dict carries the scope after ``client.get_allowed_scope``
        narrowing, which subsets the granted scope against the client's
        DCR-registered ``scope``; a client registered only ``mcp`` would have a
        consented ``mcp:read`` stripped to ``""``, and ``""`` reads as legacy
        full access at ``/mcp`` -- a silent fail-open. Reading the scope off
        the code/old-refresh persists exactly what consent granted, on both the
        code-exchange and refresh-rotation paths.
        """
        now = fields.Datetime.now()
        auth_code = getattr(oauth2_request, "authorization_code", None)
        old_refresh = getattr(oauth2_request, "refresh_token", None)
        audience, family_id = self._resolve_audience_and_family(oauth2_request)
        if auth_code is not None:
            scope = auth_code.scope or ""
        elif old_refresh is not None:
            scope = old_refresh.scope or ""
        else:
            scope = token.get("scope") or ""
        vals = {
            "access_token_hash": _sha256(token["access_token"]),
            "client": oauth2_request.client.id,
            "user_id": oauth2_request.user.id,
            "scope": scope,
            "audience": audience,
            "access_expires_at": now + timedelta(seconds=token.get("expires_in", 0)),
            "refresh_family_id": family_id,
        }
        refresh_token = token.get("refresh_token")
        if refresh_token:
            vals["refresh_token_hash"] = _sha256(refresh_token)
            vals["refresh_expires_at"] = now + timedelta(
                seconds=_REFRESH_TOKEN_TTL_SECONDS
            )
        new_token = self.create(vals)
        oauth2_request.client._mark_token_issued()
        # Link the code to the family it produced so a later code replay revokes
        # exactly that family (see mcp.oauth.authorization.code._detect_reuse).
        if auth_code is not None:
            auth_code.refresh_family_id = family_id
        return new_token

    @api.model
    def _get_valid_access_token(self, access_token):
        """Return the unrevoked, unexpired token for ``access_token`` (or empty).

        The resource-server counterpart of :meth:`_get_valid_refresh_token`:
        the raw bearer is hashed and matched against the stored access-token
        hash so the presented value itself is never compared in the clear.
        """
        return self.search(
            [
                ("access_token_hash", "=", _sha256(access_token)),
                ("revoked", "=", False),
                ("access_expires_at", ">", fields.Datetime.now()),
            ],
            limit=1,
        )

    @api.model
    def _get_valid_refresh_token(self, refresh_token):
        """Return the unrevoked, unexpired token for ``refresh_token`` (or empty).

        The matching row is locked ``FOR UPDATE`` so two concurrent redemptions
        of the same refresh token serialize: the second blocks until the first
        rotates (revokes) it and commits, then re-evaluates the ``revoked =
        FALSE`` qualification, finds nothing and routes into reuse detection --
        closing the rotation TOCTOU that would otherwise fork the family into two
        independently live chains.
        """
        self.env.cr.execute(
            """
            SELECT id FROM mcp_oauth_token
            WHERE refresh_token_hash = %s
              AND revoked = FALSE AND refresh_expires_at > %s
            LIMIT 1
            FOR UPDATE
            """,
            (_sha256(refresh_token), fields.Datetime.now()),
        )
        row = self.env.cr.fetchone()
        return self.browse(row[0]) if row else self.browse()

    def _revoke(self):
        """Revoke this token (used by rotation, admin revoke and GC)."""
        self.write({"revoked": True})

    @api.model
    def _revoke_family(self, family_id):
        """Revoke every still-live token sharing ``family_id`` (reuse detection).

        A refresh-token family is the rotation chain descended from one
        authorization code. Revoking the whole family is how a replay (of either
        the code or an already-rotated refresh token) is contained.
        """
        if not family_id:
            return
        self.search(
            [("refresh_family_id", "=", family_id), ("revoked", "=", False)]
        )._revoke()

    @api.model
    def _detect_refresh_reuse(self, refresh_token):
        """Revoke the whole family when an already-rotated refresh token reappears.

        Rotation revokes each refresh token as its successor issues, so a hash
        that matches an already-revoked record is a replay (RFC 6749 4.1.2 /
        RFC 9700): the live descendant of that family must be revoked too.
        """
        spent = self.search(
            [
                ("refresh_token_hash", "=", _sha256(refresh_token)),
                ("revoked", "=", True),
            ],
            limit=1,
        )
        if spent:
            self._revoke_family(spent.refresh_family_id)

    def action_revoke(self):
        """Admin action (token form header): revoke the selected token(s).

        Routed through ``sudo()`` because the admin ACL on this model is
        read-only (a delegated MCP admin must not be able to write token rows
        directly -- that would allow forging a bearer bound to any user); revoke
        is the one sanctioned state change, so it is performed with elevated
        rights from this vetted server action rather than a direct ORM write.

        The MCP-admin group is checked against the *real* user first: this is a
        public method, and ``/web/dataset/call_kw`` invokes public methods with
        no ``ir.model.access`` pre-check, so without this guard any authenticated
        user could reach the ``sudo()`` below and revoke arbitrary tokens.
        """
        if not self.env.user.has_group("mcp_server.group_mcp_admin"):
            raise AccessError(_("Only MCP administrators may revoke OAuth tokens."))
        self.sudo()._revoke()  # sudo: sanctioned admin revoke; model ACL is read-only

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    @api.model
    def _gc_oauth(self):
        """Garbage-collect spent OAuth credentials (run from an ``ir.cron``).

        Deletes only credentials that can no longer authenticate anything:
        expired authorization codes that no longer guard a live token family,
        fully expired tokens (whether revoked or simply abandoned), and stale
        dynamically-registered clients with no live token (a confidential one
        only if it never received a token). Anything still usable is left
        untouched. Returns ``(codes, tokens, clients)`` counts.

        Deletes in bounded batches so a large catch-up backlog (a delayed,
        failed or disabled cron, or a high token-rotation instance) never
        materializes one giant unlink + cascade in a single step -- mirrors
        ``mcp.log.cleanup_old_logs``.
        """
        now = fields.Datetime.now()

        # Live tokens (unrevoked and still within at least one expiry) are read
        # once and reused below: their refresh families decide which expired
        # codes to keep, and their clients pin which DCR clients are still in
        # use. Read before any delete, so this snapshot stays consistent for the
        # code/token/client selections it drives (which touch disjoint rows).
        live_tokens = self.search(
            [
                ("revoked", "=", False),
                "|",
                ("access_expires_at", ">", now),
                ("refresh_expires_at", ">", now),
            ]
        )

        # Authorization codes are single-use and ~60s-lived. An expired code is
        # normally collected, but a *redeemed* (used=True) code whose
        # refresh_family_id still has at least one live (unrevoked) token is
        # KEPT: it is the only row a later code replay can match to revoke that
        # family (mcp.oauth.authorization.code._detect_reuse), so dropping it
        # would silently break replay containment for a still-live family.
        # Never-redeemed codes, and redeemed codes whose family is fully dead or
        # absent, mint/guard nothing and are collected. The keep-rule is a Python
        # predicate (not a domain), so the pre-filtered set is unlinked in
        # id-slices rather than the re-search loop used for the domains below.
        live_family_ids = set(live_tokens.mapped("refresh_family_id"))
        # Collect expired authorization codes in bounded id-keyset pages,
        # applying the keep-rule per page so the whole expired set is never
        # materialized at once (one code row per authorize request). Keyset
        # pagination (id > last_id) is robust to the in-loop deletes and to the
        # Python keep-filter -- a plain re-search loop could not paginate it,
        # since the kept rows would reappear every pass.
        code_model = self.env["mcp.oauth.authorization.code"]
        code_count = 0
        last_id = 0
        while True:
            batch = code_model.search(
                [("expires_at", "<", now), ("id", ">", last_id)],
                limit=_GC_BATCH_SIZE,
                order="id",
            )
            if not batch:
                break
            last_id = batch[-1].id
            to_delete = batch.filtered(
                lambda c: not (
                    c.used
                    and c.refresh_family_id
                    and c.refresh_family_id in live_family_ids
                )
            )
            code_count += len(to_delete)
            to_delete.sudo().unlink()  # sudo: GC; OAuth tables are ACL read-only

        # Tokens: past every expiry. A token past both its access and refresh
        # expiries can never authenticate or be refreshed again, so it is dead
        # whether it was explicitly revoked (rotation/admin/replay) or simply
        # abandoned (issued, used, then never revoked or refreshed) -- both are
        # collected. Liveness above (revoked=False AND still unexpired) mirrors
        # this, so an abandoned expired token no longer keeps its family alive.
        token_count = self._gc_unlink_batched(
            self.env["mcp.oauth.token"],
            [
                ("access_expires_at", "<", now),
                "|",
                ("refresh_expires_at", "=", False),
                ("refresh_expires_at", "<", now),
            ],
        )

        # Stale DCR clients: dynamically registered, older than the retention
        # threshold and with no live (unrevoked, unexpired) token. A confidential
        # client that ever received a token is kept: a Microsoft 365 agent
        # registers once at provisioning and cannot register again by itself.
        # A token needs a user's consent, so spam registrations never get one.
        # Deleting a client cascades to its already-dead tokens and codes.
        client_count = self._gc_unlink_batched(
            self.env["mcp.oauth.client"].with_context(active_test=False),
            [
                ("created_via", "=", "dcr"),
                ("create_date", "<", now - timedelta(days=_DCR_CLIENT_TTL_DAYS)),
                ("id", "not in", live_tokens.client.ids),
                "|",
                ("client_secret_hash", "=", False),
                ("last_token_at", "=", False),
            ],
        )

        counts = (code_count, token_count, client_count)
        _logger.info(
            "OAuth GC removed %s authorization codes, %s tokens, %s stale clients",
            *counts,
        )
        return counts

    @staticmethod
    def _gc_unlink_batched(model, domain, batch_size=_GC_BATCH_SIZE):
        """Unlink rows of ``model`` matching ``domain`` in bounded batches (sudo).

        Re-searches each pass so deleted rows never reappear and no more than
        ``batch_size`` rows (plus their cascade + ORM cache) are held at once.
        Only valid for a pure-domain delete-set -- a Python keep-filter would
        re-select the kept rows every pass and never terminate.
        """
        count = 0
        while True:
            batch = model.search(domain, limit=batch_size)
            if not batch:
                break
            count += len(batch)
            batch.sudo().unlink()  # sudo: GC; OAuth tables are ACL read-only
        return count

    # ------------------------------------------------------------------
    # Authlib TokenMixin interface
    # ------------------------------------------------------------------
    def check_client(self, client):
        return self.client.id == client.id

    def get_scope(self):
        return self.scope or ""

    def get_expires_in(self):
        if not self.access_expires_at:
            return 0
        return int((self.access_expires_at - fields.Datetime.now()).total_seconds())

    def is_expired(self):
        if not self.access_expires_at:
            return False
        return self.access_expires_at < fields.Datetime.now()

    def is_revoked(self):
        return self.revoked

    def get_user(self):
        return self.user_id

    def get_client(self):
        return self.client

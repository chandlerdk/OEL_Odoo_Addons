"""HTTP layer extensions for the native MCP protocol endpoint.

Adds a custom ``auth='mcp'`` method that authenticates MCP clients with an
``Authorization: Bearer <token>`` header (the ``Bearer `` scheme may be
omitted) -- accepting either an mcp- or rpc-scope Odoo API key or an OAuth 2.1
access token issued by the in-module authorization server -- and renders
internal errors on MCP-routed requests as JSON-RPC responses. A rejected
request carries the RFC 9728 protected-resource pointer so browser clients can
discover where to obtain a token.
"""

import re

from werkzeug.datastructures import WWWAuthenticate
from werkzeug.exceptions import Forbidden, HTTPException, Unauthorized

from odoo import models
from odoo.http import request

from ..controllers import auth, jsonrpc, oauth_server, utils
from ..controllers.error_sanitizer import GENERIC_ERROR_MESSAGE
from ..controllers.rate_limiting import SlidingWindowLimiter

# Routing type used by the native MCP dispatcher.
MCP_ROUTING_TYPE = "mcp"

# Per-IP cap on the (unauthenticated) bearer-failure audit write: that write
# checks out a fresh cursor + fsync per attempt and runs before the per-user
# request limiter, so without a cap an invalid-bearer flood on /mcp amplifies
# into DB load. In-memory => per worker.
_BEARER_FAILURE_MAX = 20
_BEARER_FAILURE_WINDOW_SECONDS = 60
_bearer_failure_limiter = SlidingWindowLimiter(_BEARER_FAILURE_WINDOW_SECONDS)

# HTTP auth-scheme names that must not be mistaken for a bare token when a
# client sends a scheme word without credentials (e.g. a lone
# ``Authorization: Bearer``).
_AUTH_SCHEME_NAMES = {"bearer", "basic", "digest", "negotiate", "ntlm"}


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _auth_method_mcp(cls):
        """Authenticate an MCP request via ``Authorization: Bearer <token>``.

        The ``Bearer `` scheme may be omitted (``Authorization: <token>``) as a
        tolerance for hand-written client configs; see
        :meth:`_extract_bearer_token` for the accepted grammar.

        Two front doors, tried in order:

        1. an mcp- or rpc-scope Odoo API key, resolved through the module's
           :func:`get_user_from_api_key` helper (``res.users.apikeys.
           _check_credentials`` tried with ``scope='mcp'`` then ``scope='rpc'``);
           then
        2. an OAuth 2.1 access token issued by the in-module authorization
           server (looked up by SHA-256 hash, audience-bound per RFC 8707).

        Either path binds ``request`` to the resolved user; only when both fail
        is the request rejected with a 401 carrying the RFC 9728 pointer.

        Whichever door resolves the user, membership in the MCP access group
        (``auth.user_has_mcp_access``) is required before binding: a valid
        credential whose user is not opted in is refused with a 403 (see
        :meth:`_mcp_group_forbidden`), never a 401.
        """
        header = request.httprequest.headers.get("Authorization")
        token = cls._extract_bearer_token(header)
        if not token:
            if header:
                # A credential was presented but is not a usable Bearer token
                # (wrong scheme / malformed) -- audit it as a failed attempt.
                # A request with NO Authorization header is the normal RFC 9728
                # discovery probe (the client expects the 401 challenge to locate
                # the authorization server), so it is deliberately not logged.
                auth._log_auth_failure(
                    "Malformed Authorization header", api_key_used=False
                )
            raise cls._mcp_unauthorized(
                "Missing credentials; provide an "
                "'Authorization: Bearer <token>' header."
            )

        # Front door 1: mcp- or rpc-scope API key. This is the only caller that
        # widens ``allowed_scopes`` to accept an ``mcp``-scope key, so an ``mcp``
        # key stays confined to ``/mcp``. Suppress its failure audit row: an OAuth
        # access token is not an API key and would log a spurious failure before
        # the OAuth door below succeeds; a genuine failure is logged once at the end.
        user = auth.get_user_from_api_key(
            token, allowed_scopes=("mcp", "rpc"), log_failure=False
        )
        if user:
            if not auth.user_has_mcp_access(user):
                raise cls._mcp_group_forbidden(user, api_key_used=True)
            # Attribute the per-operation audit rows (mcp.log) to the API-key door.
            request._mcp_auth_method = "api_key"
            cls._bind_mcp_user(user.id)
            return

        # Front door 2: OAuth 2.1 access token from the in-module AS.
        oauth_user_id = cls._resolve_oauth_token(token)
        if oauth_user_id:
            oauth_user = (
                request.env["res.users"]
                .sudo()  # sudo: read group membership pre-bind, like the key door
                .browse(oauth_user_id)
            )
            if not auth.user_has_mcp_access(oauth_user):
                raise cls._mcp_group_forbidden(oauth_user, api_key_used=False)
            cls._bind_mcp_user(oauth_user_id)
            return

        # Both doors failed: record a single auth failure. api_key_used=False --
        # the bearer was neither a valid API key nor a valid OAuth token. Throttle
        # the audit write per-IP: past the cap, skip it (the 401 below is
        # unaffected) so an invalid-bearer flood cannot amplify via fsync-per-write.
        if not _bearer_failure_limiter.is_limited(
            (request.db, request.httprequest.remote_addr), _BEARER_FAILURE_MAX
        ):
            auth._log_auth_failure("Invalid or expired credentials", api_key_used=False)
        raise cls._mcp_unauthorized("Invalid or expired credentials.")

    @staticmethod
    def _extract_bearer_token(header):
        """Extract the credential from an ``Authorization`` header value.

        Accepts the canonical ``Bearer <token>`` form (RFC 6750) and, as a
        tolerance for hand-written client configs, a bare single-word token
        sent without the scheme. Anything else returns ``None`` (malformed):
        a different scheme (``Basic ...``), a lone scheme word (a ``Bearer``
        with no credential must not be looked up as the literal token
        "bearer"), or a scheme-less value with embedded whitespace.
        """
        if not header:
            return None
        match = re.match(r"^bearer\s+(.+)$", header, re.IGNORECASE)
        if match:
            return match.group(1)
        bare = header.strip()
        if (
            bare
            and not re.search(r"\s", bare)
            and bare.lower() not in _AUTH_SCHEME_NAMES
        ):
            return bare
        return None

    @classmethod
    def _bind_mcp_user(cls, uid):
        """Bind the current MCP request to ``uid`` (stateless: no saved session)."""
        request.update_env(user=uid)
        # ``update_env(user=...)`` reuses the pre-auth context, and the framework
        # only injects ``lang`` (never ``tz``). Recompute the bound user's
        # ``lang``/``tz`` so ``_()`` and datetime bucketing (``aggregate_records``
        # -> ``formatted_read_group``, grouped by ``context['tz']``) match the
        # user. Runs after ``update_env`` so ``context_get`` reads the bound user.
        request.update_context(**request.env["res.users"].context_get())
        request.session.can_save = False

    @classmethod
    def _resolve_oauth_token(cls, raw_token):
        """Resolve a bearer as an OAuth access token, enforcing audience binding.

        Returns the user id to bind when ``raw_token`` is a live (unrevoked,
        unexpired) ``mcp.oauth.token`` whose RFC 8707 ``audience`` matches this
        resource; otherwise ``None``. The raw token is never logged.
        """
        # OAuth is an optional front door (on by default): when it is disabled,
        # access tokens are not accepted at all -- fall through to the API-key
        # path / 401.
        if not utils.is_oauth_enabled(request.env):
            return None
        token = (
            request.env["mcp.oauth.token"]
            .sudo()  # sudo: resolve a hashed token before any user is bound
            ._get_valid_access_token(raw_token)
        )
        if not token:
            return None
        # RFC 8707: the token MUST be bound to this resource. A token minted for
        # a different audience is rejected here (no cross-resource passthrough).
        if not cls._mcp_audience_matches(token.audience):
            return None
        # An archived/deactivated user is cut off at token use, not just at
        # issuance: an outstanding token whose bound user is no longer active is
        # rejected here (read via the token's existing sudo env; no new bypass).
        if not token.user_id.active:
            return None
        # Same for the token's client: deactivating an OAuth client (backend UI)
        # blocks new authorize/token exchanges (query_client filters active), but
        # must ALSO cut off every access token it already issued. Read via the
        # token's existing sudo env (Many2one navigation, no new ACL bypass);
        # fail closed -> 401.
        if not token.client.active:
            return None
        # The read/write scope gate applies to OAuth tokens only. Stash the
        # granted scope on ``request`` so the /mcp write gate (_write_allowed)
        # can read it -- an empty string means a legacy token (issued before
        # scopes) and grants full access. The API-key door stashes nothing, so
        # absence of this attribute == full access and keeps API keys ungated.
        request._mcp_oauth_scope = token.scope or ""
        # Attribute the per-operation audit rows (mcp.log) to this OAuth client +
        # granted scope.
        request._mcp_auth_method = "oauth"
        request._mcp_oauth_client_id = token.client.id
        return token.user_id.id

    @staticmethod
    def _mcp_audience_matches(audience):
        """Whether ``audience`` identifies this MCP resource (trailing-/ tolerant)."""
        if not audience:
            return False
        return audience.rstrip("/") in {
            url.rstrip("/") for url in oauth_server.accepted_resource_urls()
        }

    @staticmethod
    def _mcp_group_forbidden(user, api_key_used):
        """Build the 403 for a valid credential whose user lacks the MCP group.

        Deliberately NOT a 401: a 401 + ``WWW-Authenticate`` challenge sends a
        spec-following MCP client back into the OAuth flow -- which succeeds
        again (the credential IS valid) and loops. Authenticated-but-
        unauthorized is a plain 403 the client surfaces instead of retrying.
        The audit row carries the refused user id and rides
        ``auth._log_auth_failure``'s per-IP throttle.
        """
        auth.log_mcp_group_denied(user, api_key_used=api_key_used)
        return Forbidden(
            "Your user is not authorized for MCP access. Ask your Odoo "
            "administrator for the 'MCP User' group."
        )

    @staticmethod
    def _mcp_unauthorized(description):
        """Build a 401 carrying the RFC 9728 protected-resource pointer.

        The ``resource_metadata`` parameter tells a browser MCP client where to
        find this endpoint's protected-resource metadata (and from it the
        authorization server), so it can start the OAuth flow. It is included
        only when that document is actually served (MCP and OAuth both enabled,
        see ``oauth_server.oauth_surface_enabled``); otherwise it answers 404
        and there is no AS to discover, so a plain ``Bearer`` challenge is
        emitted instead.
        """
        if oauth_server.oauth_surface_enabled():
            challenge = WWWAuthenticate(
                "Bearer",
                {"resource_metadata": oauth_server.protected_resource_metadata_url()},
            )
        else:
            challenge = WWWAuthenticate("Bearer")
        return Unauthorized(description, www_authenticate=challenge)

    @classmethod
    def _handle_error(cls, exception):
        """Render internal errors on MCP routes as JSON-RPC ``-32603``.

        Transport-level HTTP errors (401/405/...) keep the default handling so
        their status code and headers (e.g. ``WWW-Authenticate``) survive; any
        other error on an ``mcp``-routed request becomes a JSON-RPC internal
        error, since the MCP client speaks JSON-RPC rather than HTML.
        """
        if cls._is_mcp_request() and not isinstance(exception, HTTPException):
            # The framework logs the full traceback (odoo/http.py __call__) before
            # the error response is sent; we return only the generic message so no
            # internal detail (traceback / SQL / paths) reaches the client.
            return request.make_json_response(
                jsonrpc.make_error(
                    getattr(request.dispatcher, "request_id", None),
                    jsonrpc.INTERNAL_ERROR,
                    GENERIC_ERROR_MESSAGE,
                )
            )
        return super()._handle_error(exception)

    @classmethod
    def _is_mcp_request(cls):
        """Whether the current request is routed through the MCP dispatcher."""
        dispatcher = getattr(request, "dispatcher", None)
        return getattr(dispatcher, "routing_type", None) == MCP_ROUTING_TYPE

"""OAuth 2.1 Authorization Server for the native MCP endpoint.

Adapts Authlib's framework-agnostic ``AuthorizationServer`` to Odoo's HTTP
layer -- ``request.httprequest`` is already a Werkzeug request, so the bridge is
thin -- and wires the grants a browser MCP client needs: a PKCE-protected
(S256-only) authorization-code grant, a rotating refresh-token grant, and
RFC 7591 dynamic client registration.

Issued tokens are opaque and persisted SHA-256-hashed in the ``mcp.oauth.*``
models; this module only drives the protocol and delegates every
persistence/lookup to those models (referenced by name at runtime), so no
secret material is kept or logged here. The route handlers and consent screen
that call into this server live alongside it.
"""

import json
import logging
import time
from collections import defaultdict
from functools import cached_property, wraps
from urllib.parse import quote, urlsplit

from authlib.common.security import generate_token
from authlib.common.urls import add_params_to_uri
from authlib.oauth2 import AuthorizationServer, OAuth2Error
from authlib.oauth2.rfc6749 import (
    InvalidGrantError,
    JsonPayload,
    JsonRequest,
    OAuth2Payload,
    OAuth2Request,
    grants,
)
from authlib.oauth2.rfc6749.authenticate_client import authenticate_none
from authlib.oauth2.rfc6749.util import extract_basic_authorization
from authlib.oauth2.rfc6750 import BearerTokenGenerator
from authlib.oauth2.rfc7591 import (
    ClientRegistrationEndpoint,
    InvalidClientMetadataError,
)
from authlib.oauth2.rfc7636 import CodeChallenge
from psycopg2 import OperationalError
from werkzeug.exceptions import BadRequest, UnsupportedMediaType

from odoo import _, http
from odoo.http import Response, request

from . import auth, utils
from .rate_limiting import SlidingWindowLimiter

# Authlib refuses to operate over plain HTTP. We deliberately do NOT set the
# process-wide ``AUTHLIB_INSECURE_TRANSPORT`` escape hatch: disabling the check
# globally fails open, so a production plain-http misroute would silently
# downgrade instead of being rejected. Authlib validates the scheme of the
# request URL it is handed (``request.httprequest.url``); this stays fail-safe on
# its own -- a TLS-fronted deployment (Odoo ``proxy_mode`` + ``X-Forwarded-Proto``)
# presents ``https`` and validates natively, dev/test run on a loopback host
# (``127.0.0.1``/``localhost``/``[::1]``) which Authlib already treats as secure,
# and a non-loopback plain-http request correctly fails closed.

_logger = logging.getLogger(__name__)

# Access tokens are deliberately short-lived; longevity comes from rotating
# refresh tokens, which keeps a leaked access token's blast radius small.
ACCESS_TOKEN_EXPIRES_IN = 3600

# Capabilities advertised to (and pinned for) dynamically registered clients.
# Every client uses PKCE. A public client ("none") has no secret; a confidential
# client also authenticates with a client secret. "none" stays first for clients
# that take the first advertised method.
SUPPORTED_GRANT_TYPES = ["authorization_code", "refresh_token"]
SUPPORTED_RESPONSE_TYPES = ["code"]
SUPPORTED_TOKEN_ENDPOINT_AUTH_METHODS = [
    "none",
    "client_secret_basic",
    "client_secret_post",
]

# Stored token-endpoint allow-lists (mcp.oauth.client.token_endpoint_auth_method).
# A confidential client may send its secret either way. A DCR client that omitted
# the method gets a secret (RFC 7591 default: client_secret_basic) but may still
# authenticate without it, so a public client that ignores the secret keeps
# working.
CONFIDENTIAL_CLIENT_AUTH_METHODS = "client_secret_basic client_secret_post"
LENIENT_CLIENT_AUTH_METHODS = "client_secret_basic client_secret_post none"

# Order in which the token endpoint tries the client authentication methods. A
# client that sends a secret is authenticated by it; "none" (public PKCE client)
# is the fallback. Each client's stored allow-list decides which method it may
# actually use (mcp.oauth.client.check_endpoint_auth_method).
CLIENT_AUTH_METHODS = ["client_secret_basic", "client_secret_post", "none"]

# OAuth scopes advertised at consent and in the discovery documents. "mcp" is
# the legacy full-access scope (read+write); "mcp:read"/"mcp:write" let the
# resource owner grant read-only or read+write access at the consent screen.
# Per-model (mcp.enabled.model) and per-user (Odoo ACL) gating still applies on
# top of whichever scope is granted.
SUPPORTED_SCOPES = ["mcp", "mcp:read", "mcp:write"]

# Scopes that grant WRITE in our hierarchical grammar: "mcp" (legacy full) and
# "mcp:write" both imply write (and read); "mcp:read" is read-only. The
# request-time gate in controllers/mcp.py (_write_allowed) reuses this via
# scope_grants_write(), so the grant-time bound and the request-time gate agree
# on what "write" means.
_WRITE_SCOPES = frozenset({"mcp", "mcp:write"})

# Scopes accepted in a request but dropped before validation. ``offline_access``
# (OpenID Connect) asks for a refresh token, which this server always issues.
_IGNORED_SCOPES = frozenset({"offline_access"})


def _scope_set(scope):
    """Split a space-separated ``scope`` string into a set of scope tokens."""
    return set((scope or "").split())


def scope_grants_write(scope):
    """Whether a space-separated ``scope`` string grants write access.

    Read is granted by any of our scopes, so only write needs testing: it is
    granted iff the scope set contains ``mcp`` or ``mcp:write``.
    """
    return bool(_WRITE_SCOPES & _scope_set(scope))


def is_read_only_request(scope_str):
    """Whether the requested ``scope`` is exactly the read-only scope.

    A request for exactly ``{mcp:read}`` is pinned read-only: no write checkbox
    is offered at consent, and the granted scope stays ``mcp:read``.
    """
    return _scope_set(scope_str) == {"mcp:read"}


def expand_registered_scopes(scope):
    """Expand a client's registered ``scope`` into every scope it implies.

    Hierarchy-aware so a coarse registration covers the finer scopes it
    subsumes: ``mcp`` implies {mcp, mcp:read, mcp:write}; ``mcp:write`` implies
    {mcp:write, mcp:read}; ``mcp:read`` implies {mcp:read}. Used by
    ``mcp.oauth.client.get_allowed_scope`` to bound a grant to no more than the
    client registered without treating ``mcp`` / ``mcp:read`` as disjoint.
    """
    granted = _scope_set(scope)
    if "mcp" in granted:
        granted |= {"mcp:read", "mcp:write"}
    if "mcp:write" in granted:
        granted.add("mcp:read")
    return granted


def client_allows_write(client):
    """Whether ``client``'s REGISTERED scope permits a write grant.

    Back-compat: a client that registered no scope is treated as full access,
    so existing/legacy clients are never newly restricted. Only a client that
    registered a non-empty scope lacking ``mcp``/``mcp:write`` (i.e. it
    registered read-only) is barred from ever being granted write.
    """
    registered = client.scope or ""
    return not registered or scope_grants_write(registered)


# ---------------------------------------------------------------------------
# Request wrappers: present Odoo's Werkzeug request to Authlib
# ---------------------------------------------------------------------------
class _OdooOAuth2Payload(OAuth2Payload):
    """Form/query payload view over the Werkzeug request (token/authorize).

    Params are read by HTTP method so a POST endpoint never honors the URL query
    string: the token exchange and the consent-approval POST read the FORM BODY
    only, while GET /authorize reads the query string. Accepting grant/credential
    params (``code``, ``client_id``, ``code_verifier``, ``redirect_uri``,
    ``scope``, ``resource`` ...) from the query on a POST would leak secret-class
    values into access logs, proxy logs, browser history and Referer headers
    (RFC 6749 3.2 -- the token endpoint uses POST body parameters).
    """

    def __init__(self, httprequest):
        self._httprequest = httprequest

    @property
    def data(self):
        req = self._httprequest
        # POST (token, consent approval): form body only. GET (authorize): query.
        data = req.form if req.method == "POST" else req.args
        scope = data.get("scope")
        if scope and _IGNORED_SCOPES & _scope_set(scope):
            # Drop ``offline_access`` instead of refusing it as an unknown scope:
            # a refresh token is always issued, so the scope adds nothing, and
            # many clients (Microsoft's among them) send it by habit.
            data = data.copy()
            data["scope"] = " ".join(
                token for token in scope.split() if token not in _IGNORED_SCOPES
            )
        return data

    @cached_property
    def datalist(self):
        values = defaultdict(list)
        for key in self.data:
            values[key].extend(self.data.getlist(key))
        return values


class OdooOAuth2Request(OAuth2Request):
    """Authlib ``OAuth2Request`` backed by Odoo's ``request.httprequest``."""

    def __init__(self, httprequest):
        super().__init__(
            method=httprequest.method,
            uri=httprequest.url,
            headers=httprequest.headers,
        )
        self._httprequest = httprequest
        self.payload = _OdooOAuth2Payload(httprequest)

    @property
    def form(self):
        return self._httprequest.form


class _OdooJsonPayload(JsonPayload):
    """JSON-body payload view over the Werkzeug request (DCR)."""

    def __init__(self, httprequest):
        self._httprequest = httprequest

    @property
    def data(self):
        return self._httprequest.get_json()


class OdooJsonRequest(JsonRequest):
    """Authlib ``JsonRequest`` backed by Odoo's ``request.httprequest``."""

    def __init__(self, httprequest):
        super().__init__(httprequest.method, httprequest.url, httprequest.headers)
        self.payload = _OdooJsonPayload(httprequest)


# ---------------------------------------------------------------------------
# Grants -- protocol logic only; the mcp.oauth.* models do the storage + hashing
# ---------------------------------------------------------------------------
class _S256CodeChallenge(CodeChallenge):
    """PKCE extension restricted to S256.

    Rejecting ``plain`` and defaulting an omitted method to S256 makes the flow
    downgrade-proof at *both* ends: the authorize step refuses a non-S256 method,
    and redemption re-checks the stored method too. Authlib only validates the
    method against the supported set at authorize time -- at the token step it
    just looks up a verify function for whatever method the code carries -- so
    without this re-check a code that somehow held ``plain`` could still be
    verified with the weaker transform.
    """

    SUPPORTED_CODE_CHALLENGE_METHOD = ["S256"]
    DEFAULT_CODE_CHALLENGE_METHOD = "S256"

    def validate_code_verifier(self, grant, result):
        authorization_code = grant.request.authorization_code
        if authorization_code is not None:
            method = self.get_authorization_code_challenge_method(authorization_code)
            if method and method not in self.SUPPORTED_CODE_CHALLENGE_METHOD:
                raise InvalidGrantError(
                    description="Unsupported code_challenge_method."
                )
        return super().validate_code_verifier(grant, result)


class _AuthorizationCodeGrant(grants.AuthorizationCodeGrant):
    """Authorization-code grant (PKCE) for public and confidential clients."""

    TOKEN_ENDPOINT_AUTH_METHODS = CLIENT_AUTH_METHODS

    def save_authorization_code(self, code, oauth2_request):
        codes = request.env[
            "mcp.oauth.authorization.code"
        ].sudo()  # sudo: AS persists protocol state before any user ACL applies
        return codes._save_code(code, oauth2_request)

    def query_authorization_code(self, code, client):
        codes = request.env[
            "mcp.oauth.authorization.code"
        ].sudo()  # sudo: resolve a hashed code regardless of the acting user
        authorization_code = codes._get_valid_code(code, client)
        if authorization_code:
            return authorization_code
        # No live code for this client. If a spent code matches, this is a
        # replay: revoke the tokens it produced (RFC 6749 4.1.2 reuse
        # detection) before letting Authlib reject the request as invalid.
        codes._detect_reuse(code)
        return None

    def delete_authorization_code(self, authorization_code):
        # Single-use: mark consumed (not deleted) so a replay is detectable.
        authorization_code.sudo()._consume()  # sudo: AS state, not user data

    def authenticate_user(self, authorization_code):
        # Bind the token to the code's owner, but return None (Authlib then
        # rejects the grant) if that account has since been archived/deactivated
        # or has lost the MCP access group between consent and exchange.
        user = authorization_code.sudo().user_id  # sudo: read code owner + active
        if not (user and user.active):
            return None
        return user if auth.user_has_mcp_access(user) else None


class _RefreshTokenGrant(grants.RefreshTokenGrant):
    """Rotating refresh-token grant for public and confidential clients."""

    TOKEN_ENDPOINT_AUTH_METHODS = CLIENT_AUTH_METHODS
    # Rotate on every use: a fresh refresh token is issued and the old one
    # revoked, so a captured refresh token is single-use.
    INCLUDE_NEW_REFRESH_TOKEN = True

    def authenticate_refresh_token(self, refresh_token):
        tokens = request.env["mcp.oauth.token"].sudo()  # sudo: resolve hashed token
        valid = tokens._get_valid_refresh_token(refresh_token)
        if valid:
            return valid
        # No live token for this hash. If it matches one we already rotated
        # (revoked), the token is being replayed: revoke the whole family before
        # rejecting, so its live descendant cannot be used either (reuse
        # detection, RFC 6749 4.1.2 / RFC 9700).
        tokens._detect_refresh_reuse(refresh_token)
        return None

    def authenticate_user(self, credential):
        # Bind the token to the refresh token's owner, but return None (Authlib
        # then rejects the grant) if that account has since been archived or has
        # lost the MCP access group -- the refresh then dies cleanly with
        # invalid_request instead of minting a token that would only 403 at /mcp.
        user = credential.sudo().user_id  # sudo: read token owner + active flag
        if not (user and user.active):
            return None
        return user if auth.user_has_mcp_access(user) else None

    def revoke_old_credential(self, refresh_token):
        # Rotation: retire the just-used refresh token once the new one issues.
        refresh_token.sudo()._revoke()  # sudo: AS state, not user data


# Bounds on attacker-controllable dynamic-registration input. The DCR endpoint
# is unauthenticated (rate-limited only per IP/worker), so cap the count and
# length of the fields a client persists -- generous for real MCP clients, but
# enough to stop uncapped rows accumulating until the 30-day GC reaps them.
_MAX_REDIRECT_URIS = 5
_MAX_REDIRECT_URI_LENGTH = 2048
_MAX_CLIENT_STRING_LENGTH = 255


def _log_registration_request(data):
    """Log what a DCR request asks for: client name, auth method, redirect hosts.

    Written before validation, so a rejected request is logged too. It shows
    which auth method a client really sends. The request carries no secret;
    every string is capped and repr-quoted, because the endpoint is open.
    """
    if not isinstance(data, dict):
        data = {}
    method = data.get("token_endpoint_auth_method") or "omitted"
    if not isinstance(method, str):
        method = "<%s>" % type(method).__name__
    name = data.get("client_name")
    name = name if isinstance(name, str) else ""
    uris = data.get("redirect_uris")
    hosts = []
    if isinstance(uris, list):
        for uri in uris[:_MAX_REDIRECT_URIS]:
            try:
                host = urlsplit(uri).hostname if isinstance(uri, str) else None
            except ValueError:
                host = None
            hosts.append(host and host[:_MAX_CLIENT_STRING_LENGTH])
    _logger.info(
        "OAuth DCR request: client_name=%r auth_method=%r redirect_hosts=%r",
        name[:_MAX_CLIENT_STRING_LENGTH],
        method[:_MAX_CLIENT_STRING_LENGTH],
        hosts,
    )


class _ClientRegistrationEndpoint(ClientRegistrationEndpoint):
    """RFC 7591 dynamic client registration (auth-code + refresh, PKCE).

    The requested ``token_endpoint_auth_method`` decides the client class:
    ``none`` registers a public client, a secret method a confidential client,
    and an omitted method a confidential client that may also act as public.
    """

    def authenticate_token(self, oauth2_request):
        # Open registration: returning a non-None value lets DCR proceed. The
        # route layer rate-limits registration by IP and the model pins the
        # client to safe defaults, so no initial access token is required.
        return True

    def get_server_metadata(self):
        # Authlib validates the requested client metadata against this, so the
        # advertised sets double as the allow-list for what a client may ask for.
        return {
            "grant_types_supported": SUPPORTED_GRANT_TYPES,
            "response_types_supported": SUPPORTED_RESPONSE_TYPES,
            "token_endpoint_auth_methods_supported": (
                SUPPORTED_TOKEN_ENDPOINT_AUTH_METHODS
            ),
        }

    def generate_client_info(self, oauth_request):
        # A public client gets only a client_id; the base method would always
        # mint a secret. A confidential client gets a secret that never expires
        # (client_secret_expires_at 0); the model stores only its hash.
        info = {
            "client_id": self.generate_client_id(oauth_request),
            "client_id_issued_at": int(time.time()),
        }
        if oauth_request.mcp_client_auth_methods != "none":
            info["client_secret"] = generate_token(48)
            info["client_secret_expires_at"] = 0
        return info

    def extract_client_metadata(self, oauth_request):
        data = oauth_request.payload.data
        _log_registration_request(data)
        # A null or empty method counts as omitted (as before), so drop the key
        # and let Authlib apply the RFC 7591 default, "client_secret_basic".
        # Authlib copies payload.data, so this change is seen by its validation
        # (not a persisted side effect).
        requested = None
        if isinstance(data, dict):
            if not data.get("token_endpoint_auth_method"):
                data.pop("token_endpoint_auth_method", None)
            requested = data.get("token_endpoint_auth_method")
        metadata = super().extract_client_metadata(oauth_request)
        # The validated request decides the stored allow-list (see
        # generate_client_info / save_client, which run after this).
        if requested is None:
            auth_methods = LENIENT_CLIENT_AUTH_METHODS
        elif requested == "none":  # nosec B105 - RFC 7591 method name, no secret
            auth_methods = "none"
        else:
            auth_methods = CONFIDENTIAL_CLIENT_AUTH_METHODS
        oauth_request.mcp_client_auth_methods = auth_methods
        # Pin the auth-code + refresh profile so the returned metadata matches
        # what is stored, regardless of what the client requested.
        metadata["grant_types"] = SUPPORTED_GRANT_TYPES
        metadata["response_types"] = SUPPORTED_RESPONSE_TYPES
        # RFC 7591 section 2: the server may replace the requested scope. Keep
        # only the supported tokens, so a client that also asks for e.g.
        # offline_access still registers and the response shows what is stored.
        # An empty stored scope means full access (client_allows_write), so a
        # request with no supported token is stored read-only, unless it asked
        # only for the ignored ones.
        if metadata.get("scope") is not None and not isinstance(metadata["scope"], str):
            raise InvalidClientMetadataError("scope must be a space-separated string.")
        if metadata.get("scope"):
            requested = metadata["scope"].split()
            supported = [token for token in requested if token in SUPPORTED_SCOPES]
            if supported:
                metadata["scope"] = " ".join(supported)
            elif all(token in _IGNORED_SCOPES for token in requested):
                metadata["scope"] = ""
            else:
                metadata["scope"] = "mcp:read"
        return metadata

    def save_client(self, client_info, client_metadata, oauth2_request):
        redirect_uris = client_metadata.get("redirect_uris") or []
        if not redirect_uris:
            raise InvalidClientMetadataError("At least one redirect_uri is required.")
        if len(redirect_uris) > _MAX_REDIRECT_URIS:
            raise InvalidClientMetadataError(
                "Too many redirect_uris (max %d)." % _MAX_REDIRECT_URIS
            )
        for uri in redirect_uris:
            if len(uri) > _MAX_REDIRECT_URI_LENGTH:
                raise InvalidClientMetadataError(
                    "A redirect_uri is too long (max %d characters)."
                    % _MAX_REDIRECT_URI_LENGTH
                )
            if not _is_allowed_redirect_uri(uri):
                raise InvalidClientMetadataError(
                    "Each redirect_uri must use https, or http for loopback only "
                    "127.0.0.1 or localhost."
                )
        if len(client_metadata.get("client_name") or "") > _MAX_CLIENT_STRING_LENGTH:
            raise InvalidClientMetadataError(
                "client_name is too long (max %d characters)."
                % _MAX_CLIENT_STRING_LENGTH
            )
        if len(client_metadata.get("scope") or "") > _MAX_CLIENT_STRING_LENGTH:
            raise InvalidClientMetadataError(
                "scope is too long (max %d characters)." % _MAX_CLIENT_STRING_LENGTH
            )
        clients = request.env["mcp.oauth.client"].sudo()  # sudo: AS provisions client
        return clients._register_client(
            client_info,
            client_metadata,
            auth_methods=oauth2_request.mcp_client_auth_methods,
        )


# ---------------------------------------------------------------------------
# The authorization server
# ---------------------------------------------------------------------------
class OdooAuthorizationServer(AuthorizationServer):
    """Authlib ``AuthorizationServer`` bridged to Odoo request/response.

    Stateless: every method reads the thread-local :data:`odoo.http.request`, so
    one shared instance is safely reused across requests and workers.
    """

    def send_signal(self, name, *args, **kwargs):
        # No signal bus is wired in (the Flask integration uses Blinker); the
        # grants only emit advisory signals, so a no-op is correct here.
        pass

    def create_oauth2_request(self, framework_request):
        return OdooOAuth2Request(request.httprequest)

    def create_json_request(self, framework_request):
        return OdooJsonRequest(request.httprequest)

    def handle_response(self, status, body, headers):
        if isinstance(body, dict):
            body = json.dumps(body)
        return Response(body, status=status, headers=headers)

    def query_client(self, client_id):
        clients = request.env["mcp.oauth.client"].sudo()  # sudo: pre-auth lookup
        domain = [("client_id", "=", client_id), ("active", "=", True)]
        return clients.search(domain, limit=1) or None

    def save_token(self, token, oauth2_request):
        # The model hashes the opaque access/refresh values and derives the
        # expiries/audience from the request before storing them.
        tokens = request.env["mcp.oauth.token"].sudo()  # sudo: AS persists token
        tokens._save_token(token, oauth2_request)


def _basic_credentials(oauth2_request):
    """Return ``(user, password)`` from a Basic header, or ``(None, None)``.

    Authlib's parser raises on a header that is not UTF-8; such a header counts
    as no credentials instead of failing the request with a 500.
    """
    try:
        return extract_basic_authorization(oauth2_request.headers)
    except ValueError:
        return None, None


def _authenticate_client_secret_basic(query_client, oauth2_request):
    """``client_secret_basic`` that skips a Basic user name of no client.

    Authlib's version raises 401 for an unknown user name, so the later methods
    never run. A proxy in front of Odoo can send its own Basic credentials, which
    must not block a public client. A known client with a wrong secret still
    fails over to the other methods, exactly as in Authlib.
    """
    client_id, client_secret = _basic_credentials(oauth2_request)
    if not (client_id and client_secret):
        return None
    client = query_client(client_id)
    if client and client.check_client_secret(client_secret):
        return client
    return None


def _authenticate_none(query_client, oauth2_request):
    """``none`` that a client with a secret cannot reach with a wrong secret.

    A client registered without an auth method may authenticate with or without
    its secret. A request that carries a secret reaches this fallback only when
    the secret is wrong: Authlib's version already refuses a form secret, and
    this also refuses a Basic secret for the same client.
    """
    client = authenticate_none(query_client, oauth2_request)
    if client and client.client_secret_hash:
        basic_id, basic_secret = _basic_credentials(oauth2_request)
        if basic_id == client.client_id and basic_secret:
            return None
    return client


def _opaque_token(**kwargs):
    """Generate a high-entropy opaque token (the model hashes it before store)."""
    return generate_token(48)


def _build_authorization_server():
    server = OdooAuthorizationServer()
    # Defence in depth: pin the advertised scope set so Authlib's
    # validate_requested_scope rejects an unknown scope at /authorize (an empty or
    # in-set scope still passes). Mirrors scopes_supported in the discovery docs.
    server.scopes_supported = SUPPORTED_SCOPES
    server.register_token_generator(
        "default",
        BearerTokenGenerator(
            access_token_generator=_opaque_token,
            refresh_token_generator=_opaque_token,
            expires_generator=ACCESS_TOKEN_EXPIRES_IN,
        ),
    )
    server.register_client_auth_method(
        "client_secret_basic", _authenticate_client_secret_basic
    )
    server.register_client_auth_method("none", _authenticate_none)
    server.register_grant(_AuthorizationCodeGrant, [_S256CodeChallenge(required=True)])
    server.register_grant(_RefreshTokenGrant)
    server.register_endpoint(_ClientRegistrationEndpoint)
    return server


_authorization_server = None


def get_authorization_server():
    """Return the process-wide OAuth 2.1 authorization server (lazily built)."""
    global _authorization_server
    if _authorization_server is None:
        _authorization_server = _build_authorization_server()
    return _authorization_server


# ---------------------------------------------------------------------------
# Deployment-derived URLs
# ---------------------------------------------------------------------------
# The issuer/resource identifiers are NOT hardcoded: they are derived from the
# live request's host so the same module serves whatever public URL it is
# deployed behind (honours Odoo ``proxy_mode`` for the X-Forwarded-* headers).
def _base_url():
    """Scheme+host root of the current request, without a trailing slash."""
    return request.httprequest.url_root.rstrip("/")


def resource_url():
    """Canonical resource identifier of the native MCP endpoint (RFC 8707).

    This exact string is advertised as the protected resource; an access
    token's ``audience`` must match one of :func:`accepted_resource_urls` to
    be accepted at ``/mcp``.
    """
    return _base_url() + "/mcp"


def accepted_resource_urls():
    """Resource identifiers accepted as a token ``audience``.

    ``/mcp`` is canonical; ``/mcp/rpc`` is the legacy alias of the same
    endpoint. A client configured against the alias sends it as the RFC 8707
    ``resource`` parameter, so tokens minted with either audience must
    authenticate.
    """
    base = _base_url()
    return (base + "/mcp", base + "/mcp/rpc")


def protected_resource_metadata_url():
    """Absolute URL of the RFC 9728 protected-resource metadata document."""
    return _base_url() + "/.well-known/oauth-protected-resource"


def _resource_accepted(resource):
    """Whether an RFC 8707 ``resource`` indicator targets this MCP endpoint.

    Trailing-slash-insensitive match against :func:`accepted_resource_urls`. An
    empty/absent ``resource`` is accepted -- the parameter is optional and the
    audience then defaults to the canonical resource. A non-empty resource this
    AS does not serve is rejected (RFC 8707 invalid_target) so a code/token is
    never minted for an audience that would only 401 at ``/mcp``.
    """
    if not resource:
        return True
    accepted = {url.rstrip("/") for url in accepted_resource_urls()}
    return resource.rstrip("/") in accepted


def oauth_surface_enabled():
    """Whether the OAuth surface is served: MCP enabled AND OAuth enabled.

    The single condition behind :func:`_require_oauth_enabled` and the
    ``resource_metadata`` pointer of the ``/mcp`` 401 challenge
    (``ir.http._mcp_unauthorized``), so the challenge never advertises a
    discovery document that answers 404.
    """
    return utils.is_mcp_enabled() and utils.is_oauth_enabled(request.env)


def _require_oauth_enabled(method):
    """Short-circuit an OAuth route with a bare 404 when the surface is closed.

    The whole OAuth surface -- the two discovery documents and the
    ``authorize``/``token``/``register`` endpoints -- is gated on BOTH the global
    MCP switch AND the opt-in ``mcp_server.enable_oauth`` switch (see
    :func:`oauth_surface_enabled`). When either is off the authorization server
    is not exposed at all: a bare 404 that reveals nothing about the AS (and
    never serves it). API-key auth is unaffected.
    """

    @wraps(method)
    def wrapper(*args, **kwargs):
        if not oauth_surface_enabled():
            return Response(status=404)
        return method(*args, **kwargs)

    return wrapper


def _oauth_error_response(error, status, description=None):
    """Build an RFC 6749 ``{"error"[, "error_description"]}`` JSON response.

    Shared by the token and registration endpoints for their rate-limit (429),
    invalid-metadata (400) and generic server_error (500) paths; the optional
    ``error_description`` is omitted when None.
    """
    body = {"error": error}
    if description is not None:
        body["error_description"] = description
    return request.make_json_response(body, status=status)


# ---------------------------------------------------------------------------
# Discovery metadata (RFC 9728 / RFC 8414)
# ---------------------------------------------------------------------------
class OAuthMetadataController(http.Controller):
    """Well-known OAuth discovery documents for the native MCP endpoint."""

    # auth='none': RFC 9728/8414 discovery documents MUST be reachable without
    # credentials -- they are how an unauthenticated client learns where to get
    # a token. sitemap=False: machine-facing metadata, not a public web page.
    # The path-based variant is the RFC 9728 well-known URI derived from the
    # canonical resource identifier ``<base>/mcp`` (path-insertion form), which
    # clients falling back from the ``WWW-Authenticate`` hint construct
    # themselves. The root document is kept for clients that follow the hint.
    @http.route(
        [
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
        ],
        type="http",
        auth="none",
        methods=["GET"],
        sitemap=False,
        # Browser-based MCP clients read the discovery documents and call
        # register and token cross-origin; none of these routes uses cookies.
        cors="*",
    )
    @_require_oauth_enabled
    def protected_resource_metadata(self, **kwargs):
        """RFC 9728 protected-resource metadata for ``/mcp``."""
        metadata = {
            "resource": resource_url(),
            "authorization_servers": [_base_url()],
            "bearer_methods_supported": ["header"],
            "scopes_supported": SUPPORTED_SCOPES,
        }
        return request.make_json_response(metadata)

    # auth='none'/sitemap=False: same rationale as the protected-resource doc.
    @http.route(
        "/.well-known/oauth-authorization-server",
        type="http",
        auth="none",
        methods=["GET"],
        sitemap=False,
        cors="*",
    )
    @_require_oauth_enabled
    def authorization_server_metadata(self, **kwargs):
        """RFC 8414 authorization-server metadata.

        Advertises the ``/mcp/oauth/*`` endpoint paths and pins the security
        profile: S256-only PKCE for every client, and the token-endpoint auth
        methods of public and confidential clients.
        """
        base = _base_url()
        metadata = {
            "issuer": base,
            "authorization_endpoint": base + "/mcp/oauth/authorize",
            "token_endpoint": base + "/mcp/oauth/token",
            "registration_endpoint": base + "/mcp/oauth/register",
            "response_types_supported": SUPPORTED_RESPONSE_TYPES,
            "grant_types_supported": SUPPORTED_GRANT_TYPES,
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": (
                SUPPORTED_TOKEN_ENDPOINT_AUTH_METHODS
            ),
            "scopes_supported": SUPPORTED_SCOPES,
        }
        return request.make_json_response(metadata)


# ---------------------------------------------------------------------------
# Authorization-flow helpers (consent / token / DCR)
# ---------------------------------------------------------------------------
# Loopback HTTP is the only relaxation of the https-only redirect rule (OAuth
# 2.1 / RFC 8252): native and CLI MCP clients listen on an ephemeral loopback
# port, so any port on these hosts is allowed -- but only over loopback.
_LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")

# Authorization parameters re-submitted as hidden fields on the consent form so
# the approve/deny POST carries the exact same request Authlib already validated.
_AUTHORIZE_PARAMS = (
    "response_type",
    "client_id",
    "redirect_uri",
    "scope",
    "state",
    "code_challenge",
    "code_challenge_method",
    "resource",
)


def _is_allowed_redirect_uri(uri):
    """Whether ``uri`` is an acceptable redirect target for an OAuth client.

    Exact-match registration is enforced elsewhere; this only constrains the
    *scheme/host*: https anywhere, or http on loopback (any port) for native
    clients. Everything else (http on a public host, custom schemes) is refused.
    """
    try:
        parts = urlsplit(uri)
        # ``.hostname``/``.port`` parse the authority lazily: a malformed IPv6
        # literal makes ``urlsplit`` itself raise, and a non-numeric or
        # out-of-range port makes ``.port`` raise ("Port out of range",
        # "Port could not be cast", "Invalid IPv6 URL").
        username = parts.username
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        # A malformed authority has no legitimate redirect use: fail closed here
        # so the bad value is rejected cleanly (400) at DCR/authorize instead of
        # crashing (500) here or later while rendering consent.
        return False
    # A userinfo component (``user[:pass]@host``) has no legitimate OAuth use and
    # is a known consent-spoofing vector: ``https://trusted.example.com@evil.com``
    # points at ``evil.com`` while reading as the trusted host. Reject outright.
    if username is not None:
        return False
    # ``.port`` already rejected >65535 and non-numeric; also refuse an explicit
    # port of 0 so only the usable 1-65535 range is accepted.
    if port is not None and not 1 <= port <= 65535:
        return False
    # RFC 6749 3.1.2: the redirection endpoint URI MUST NOT include a fragment
    # component. A fragment has no legitimate OAuth use; reject it outright at
    # both DCR registration and authorize-time validation.
    if parts.fragment:
        return False
    if parts.scheme == "https" and hostname:
        return True
    if parts.scheme == "http" and hostname in _LOOPBACK_HOSTS:
        return True
    return False


# Per-route request-body ceiling for the UNAUTHENTICATED OAuth endpoints (token
# exchange + dynamic client registration; also applied to the authorize consent
# POST). These payloads are tiny -- a token request is a handful of form fields,
# a DCR body a small JSON object already bounded by the field caps above -- so a
# few hundred KB is generous while stopping an unauthenticated caller from making
# the worker buffer/parse an outsized body before the per-IP rate limits run.
# Far below Odoo's 128 MiB default and DISTINCT from the /mcp 10 MiB cap (which
# must fit base64 tool payloads). Core's Dispatcher.pre_dispatch hands the cap
# to Werkzeug, which refuses an over-cap form body by Content-Length (413). The
# JSON registration body is read with utils.read_bounded_body.
OAUTH_MAX_CONTENT_LENGTH = 256 * 1024  # 256 KiB


# Dynamic client registration is unauthenticated, so it carries its own fixed
# per-IP abuse cap. This is deliberately NOT the general MCP request limiter:
# that one is disabled when mcp_server.request_limit=0 (an admin choosing
# "unlimited API"), which must never switch off the registration cap. In-memory
# => per worker (effective N x cap across N workers); move to a shared store if a
# hard global cap is ever required.
_DCR_MAX_REGISTRATIONS = 20
_DCR_WINDOW_SECONDS = 3600
_dcr_limiter = SlidingWindowLimiter(_DCR_WINDOW_SECONDS)


def _dcr_rate_limited(ip):
    """Whether registrations from ``ip`` (in this DB) exceed the DCR window cap.

    Keyed by ``(db, ip)`` so a worker serving several databases never shares one
    IP's registration bucket across tenants (mirrors the per-user API limiter).
    """
    return _dcr_limiter.is_limited((request.db, ip), _DCR_MAX_REGISTRATIONS)


# The token endpoint is unauthenticated and DB-writing (authorization-code
# exchange + refresh rotation), so it carries its own per-IP abuse cap as a DoS
# backstop -- set well above any legitimate rate (a live session rotates roughly
# once per access-token lifetime). Separate bucket from DCR so the two caps tune
# independently. In-memory => per worker, same tradeoff as the registration cap.
_TOKEN_MAX_REQUESTS = 60
_TOKEN_WINDOW_SECONDS = 60
_token_limiter = SlidingWindowLimiter(_TOKEN_WINDOW_SECONDS)


def _token_rate_limited(ip):
    """Whether token-endpoint requests from ``ip`` (in this DB) exceed the cap.

    Keyed by ``(db, ip)`` so the per-IP bucket is not shared across databases in
    a multi-DB worker (mirrors the per-user API limiter).
    """
    return _token_limiter.is_limited((request.db, ip), _TOKEN_MAX_REQUESTS)


def _add_issuer(response):
    """Append the RFC 9207 ``iss`` parameter to an authorization redirect.

    Identifying the issuer in the authorization response lets the client detect
    mix-up attacks. Applied to both the success (code) and error redirects.
    """
    location = response.headers.get("Location")
    if location:
        response.headers["Location"] = add_params_to_uri(
            location, [("iss", _base_url())]
        )
    return response


def _security_headers(response):
    """Harden the consent / authorize-error screens.

    Forbids framing (clickjacking -> silent consent) and marks the page
    ``no-store`` so the session-bound CSRF token it carries is never retained by
    a shared or browser cache.
    """
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    response.headers["Cache-Control"] = "no-store"
    return response


def _render_authorize_error(description):
    """Render a standalone 400 page rejecting an invalid authorization request.

    A bad request is never redirected back to an unvalidated client URI; it is
    shown to the resource owner instead so no open-redirect surface is exposed.
    """
    response = request.render(
        "mcp_server.oauth_authorize_error", {"error_description": description}
    )
    response.status_code = 400
    return _security_headers(response)


def _render_consent(grant):
    """Render the per-request consent screen for a validated authorization grant."""
    client = grant.client
    redirect_uri = grant.redirect_uri or ""
    # authorize() already ran _is_allowed_redirect_uri on this exact value, which
    # parses it and rejects (before consent) any authority urlsplit/.port cannot
    # handle -- so this parse cannot raise here.
    parts = urlsplit(redirect_uri)
    scheme, hostname, port = parts.scheme, parts.hostname, parts.port
    # Display the real host only (never the raw netloc): a userinfo component is
    # already rejected before consent, but rendering ``hostname[:port]`` keeps the
    # shown origin honest as defence in depth against consent-origin spoofing.
    if scheme and hostname:
        host = f"{hostname}:{port}" if port else hostname
        redirect_origin = f"{scheme}://{host}"
    else:
        redirect_origin = redirect_uri
    values = request.httprequest.values
    qcontext = {
        "user_name": request.env.user.name,
        # Prefer the client-supplied name; the opaque id is the honest fallback.
        # The name is attacker-controllable (registration is open), so the
        # redirect origin below stays the real trust anchor.
        "client_label": client.client_name or client.client_id,
        "client_id": client.client_id,
        "redirect_uri": redirect_uri,
        "redirect_origin": redirect_origin,
        # Read access is always granted; the write checkbox is offered only when
        # the effective request would include write (anything other than exactly
        # ``mcp:read``) AND the client registered for write. A read-only-registered
        # client is never offered write -- it could not be granted it anyway.
        "show_write_checkbox": (
            not is_read_only_request(grant.request.scope)
            and client_allows_write(client)
        ),
        # "Not you?" — log out and come back to this same authorize request so the
        # user can approve as a different Odoo account.
        "switch_user_url": "/web/session/logout?redirect="
        + quote(request.httprequest.full_path, safe=""),
        # Re-submit the validated request verbatim; the approve/deny POST is then
        # the same request, re-validated server-side before a code is issued.
        "oauth_params": {
            name: values.get(name) for name in _AUTHORIZE_PARAMS if values.get(name)
        },
        "csrf_token": request.csrf_token(),
    }
    response = request.render("mcp_server.oauth_consent", qcontext)
    return _security_headers(response)


def _pkce_s256_present(grant):
    """Whether the request carries a mandatory, downgrade-proof S256 challenge.

    Authlib only rejects a *bad* ``code_challenge_method`` at the authorize step,
    not an absent challenge, so PKCE presence is enforced here to keep every
    client downgrade-proof (S256 only; an omitted method defaults to S256). Read
    from the grant's payload -- the source Authlib stores on the code (the form
    on POST, the query on GET) -- so a query-only challenge on the consent POST
    cannot pass here while the code is stored without one.
    """
    values = grant.request.payload.data
    challenge = values.get("code_challenge")
    method = values.get("code_challenge_method", "S256")
    return bool(challenge) and method == "S256"


class OAuthFlowController(http.Controller):
    """Authorize/consent, token and dynamic-registration endpoints."""

    # auth='user': the resource owner authenticates with their normal Odoo
    # session; an unauthenticated GET is redirected to /web/login and back. CSRF
    # stays enabled (default) -- this is a browser form POST. sitemap=False: not
    # a public web page.
    @http.route(
        "/mcp/oauth/authorize",
        type="http",
        auth="user",
        methods=["GET", "POST"],
        csrf=True,
        sitemap=False,
        max_content_length=OAUTH_MAX_CONTENT_LENGTH,
    )
    @_require_oauth_enabled
    def authorize(self, **_params):
        """Validate the request, show the consent screen, then issue a code."""
        # The request parameters are read from ``request`` below. They are not
        # bound to a local named ``kwargs``: Odoo's ``_()`` reads one as a dict
        # holding a ``context``, so ``?context=x`` would crash every message.
        # Per-user MCP access gate: the resource owner must hold the MCP User
        # group before consent is shown or a code minted -- the user gets a
        # clear consent-time error instead of a token that would only 403 at
        # /mcp (where the authoritative use-time gate lives).
        if not auth.user_has_mcp_access(request.env.user):
            return _render_authorize_error(
                _(
                    "Your user account is not authorized to use the MCP "
                    "server. Ask your Odoo administrator to add you to the "
                    "'MCP User' group."
                )
            )
        server = get_authorization_server()
        try:
            # Validates client, exact redirect_uri, response_type=code, scope and
            # (when present) the PKCE method against the acting Odoo user.
            grant = server.get_consent_grant(end_user=request.env.user)
        except OAuth2Error as error:
            return _render_authorize_error(error.description or error.error)

        # Defence in depth: a client registered outside DCR (e.g. hand-entered in
        # the backend UI, bypassing the DCR redirect-URI checks) must still not
        # carry a redirect_uri with a userinfo component or a non-loopback http
        # scheme -- both enable consent-origin spoofing. Re-validate here before a
        # code is ever issued for it.
        if not _is_allowed_redirect_uri(grant.redirect_uri or ""):
            return _render_authorize_error(
                _("The redirect URI registered for this client is not allowed.")
            )

        if not _pkce_s256_present(grant):
            return _render_authorize_error(
                _(
                    "Proof Key for Code Exchange (PKCE) with the S256 method is "
                    "required to authorize this request."
                )
            )

        # RFC 8707: reject an unknown resource indicator (invalid_target) before a
        # code is issued, rather than binding it to the code and minting a token
        # whose audience would only fail closed (401) at /mcp.
        if not _resource_accepted(request.httprequest.values.get("resource")):
            return _render_authorize_error(
                _("The requested resource is not served by this server.")
            )

        if request.httprequest.method == "GET":
            # Consent is requested per client on every authorization (never
            # persisted), which closes the one-click account-takeover surface.
            return _render_consent(grant)

        approved = request.params.get("decision") == "allow"
        grant_user = request.env.user if approved else None
        if approved:
            # Compute the scope actually granted: a client that asked for exactly
            # ``mcp:read`` is pinned read-only; otherwise the resource owner's
            # write checkbox decides. Stashed on ``request`` so ``_save_code``
            # (which Authlib calls with no scope argument) persists it onto the
            # authorization code, whence token exchange copies it to the token.
            granted = (
                "mcp:write"
                if (
                    not is_read_only_request(grant.request.scope)
                    and request.params.get("grant_write")
                )
                else "mcp:read"
            )
            # Bound the granted scope by the client's registration (authoritative):
            # a read-only-registered client can never be upscoped to write, even
            # if the user submitted the write checkbox. Defence in depth -- the
            # checkbox is also hidden, and get_allowed_scope already narrows
            # grant.request.scope -- so the persisted code/token never exceeds the
            # client's registered scope.
            if granted == "mcp:write" and not client_allows_write(grant.client):
                granted = "mcp:read"
            request._mcp_granted_scope = granted
        response = server.create_authorization_response(
            grant=grant, grant_user=grant_user
        )
        return _add_issuer(response)

    # auth='none', csrf=False: machine-to-machine token exchange. There is no
    # browser session or cookie to protect; the client authenticates with PKCE
    # plus its client_id (and its secret, if it has one), so a CSRF token is
    # neither available nor meaningful.
    # readonly=False: an auth='none' route defaults to a read-only cursor, but
    # this endpoint persists tokens (and consumes the code) on every call.
    @http.route(
        "/mcp/oauth/token",
        type="http",
        auth="none",
        cors="*",
        methods=["POST"],
        csrf=False,
        readonly=False,
        sitemap=False,
        max_content_length=OAUTH_MAX_CONTENT_LENGTH,
    )
    @_require_oauth_enabled
    def token(self, **kwargs):
        """Exchange an authorization code or refresh token for opaque tokens.

        Authlib verifies PKCE (S256), single-use code, exact redirect_uri and
        client match; the mcp.oauth.token model binds the audience (RFC 8707) and
        rotates the refresh token.
        """
        # Recorded before the retryable create_token_response below: a
        # serialization retry (service.model.retrying) re-runs this handler and
        # re-counts the hit. Best-effort, self-penalizing, safe-direction -- see
        # mcp._enforce_rate_limit's RETRY DOUBLE-COUNT note.
        if _token_rate_limited(request.httprequest.remote_addr):
            return _oauth_error_response(
                "temporarily_unavailable", 429, "Too many token requests."
            )
        try:
            return get_authorization_server().create_token_response()
        except OperationalError:
            # Serialization failure / deadlock: re-raise so Odoo's
            # service.model.retrying retries the whole request. The FOR-UPDATE
            # single-use code / refresh-rotation logic relies on that retry;
            # swallowing it below would defeat it and skip reuse-detection
            # revocation on a genuinely-concurrent replay.
            raise
        except Exception:  # noqa: BLE001 - sanitized JSON 500; traceback logged
            # Authlib already renders protocol (OAuth2Error) failures as JSON;
            # an *unexpected* exception (DB error, bug) would otherwise reach
            # Odoo's HTML 500. Machine clients need JSON, so return a generic
            # RFC 6749-style server_error with no internal detail leaked.
            _logger.exception("OAuth token endpoint failed")
            return _oauth_error_response("server_error", 500)

    # auth='none', csrf=False: RFC 7591 dynamic client registration is a
    # programmatic JSON POST with no browser session.
    # readonly=False: an auth='none' route defaults to a read-only cursor, but
    # registration creates an mcp.oauth.client record.
    @http.route(
        "/mcp/oauth/register",
        type="http",
        auth="none",
        cors="*",
        methods=["POST"],
        csrf=False,
        readonly=False,
        sitemap=False,
        max_content_length=OAUTH_MAX_CONTENT_LENGTH,
    )
    @_require_oauth_enabled
    def register(self, **kwargs):
        """Register a PKCE client (pinned profile, IP rate-limited)."""
        # Recorded before the retryable create_endpoint_response below: a
        # serialization retry (service.model.retrying) re-runs this handler and
        # re-counts the hit. Best-effort, self-penalizing, safe-direction -- see
        # mcp._enforce_rate_limit's RETRY DOUBLE-COUNT note.
        if _dcr_rate_limited(request.httprequest.remote_addr):
            return _oauth_error_response(
                "temporarily_unavailable", 429, "Too many registration requests."
            )
        utils.read_bounded_body(request.httprequest, OAUTH_MAX_CONTENT_LENGTH)
        try:
            return get_authorization_server().create_endpoint_response(
                "client_registration"
            )
        except (BadRequest, UnsupportedMediaType):
            # A malformed / non-JSON body makes Werkzeug's get_json() raise a
            # 400/415 while Authlib reads request.payload.data. That is a client
            # error, so return a 400 DCR error instead of letting it fall into
            # the generic server_error (500) path below.
            return _oauth_error_response(
                "invalid_client_metadata", 400, "Invalid request body."
            )
        except ValueError:
            # A malformed redirect_uri authority (bad IPv6 literal, non-numeric
            # or out-of-range port) makes Authlib's own URL validation raise a
            # raw ValueError -- not a clean OAuth2Error -- which would otherwise
            # surface as a 500 on this public endpoint. Translate it to the same
            # 400 a disallowed redirect_uri already gets (no traceback, no raw
            # client value echoed back). Logged (unlike a clean rejection) so a
            # spike of Authlib-tripping registrations is still visible.
            _logger.warning("OAuth DCR rejected a redirect_uri Authlib could not parse")
            return _oauth_error_response(
                "invalid_client_metadata", 400, "Invalid redirect_uri."
            )
        except OperationalError:
            # Serialization failure / deadlock: re-raise so service.model.retrying
            # can retry rather than returning a spurious 500 the client can't retry.
            raise
        except Exception:  # noqa: BLE001 - sanitized JSON 500; traceback logged
            # Any other unexpected failure (e.g. a non-iterable redirect_uris
            # scalar tripping Authlib's validation, or a DB error) would reach
            # Odoo's HTML 500; return a generic JSON server_error instead so the
            # machine client gets a parseable body, with no internal detail.
            _logger.exception("OAuth register endpoint failed")
            return _oauth_error_response("server_error", 500)

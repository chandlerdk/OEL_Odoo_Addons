"""Authentication utilities for MCP Server."""

import functools
import logging

from odoo import SUPERUSER_ID
from odoo.http import request

from . import audit
from .rate_limiting import SlidingWindowLimiter

_logger = logging.getLogger(__name__)

# Per-IP cap on the auth-failure audit write. ``_log_auth_failure`` opens a
# fresh cursor + explicit COMMIT (fsync) per rejected credential, and the legacy
# X-API-Key REST routes (``require_auth``) and the ``/mcp/xmlrpc/object`` proxy
# (``api._identify_user``) reach it with the default ``log_failure=True`` while
# rate limiting is OFF by default -- so an unauthenticated bad-key flood would
# amplify into one connection checkout + fsync per request. Past the cap the
# committed write is skipped (the 401 / XML-RPC fault is unaffected), so normal
# load still logs and only a flood is throttled. The ``/mcp`` bearer door
# already carries its own ``ir_http._bearer_failure_limiter`` gate; this one
# additionally covers the legacy callers. Mirrors that limiter and
# ``mcp._audit_write_limiter``. In-memory => per worker.
_AUTH_FAILURE_MAX = 20
_AUTH_FAILURE_WINDOW_SECONDS = 60
_auth_failure_limiter = SlidingWindowLimiter(_AUTH_FAILURE_WINDOW_SECONDS)


def _log_auth_failure(error_message, api_key_used=True, user_id=None):
    """Write an authentication-failure audit row via :func:`audit.write_audit_row`.

    ``api_key_used`` records whether the rejected credential was an API key.
    Defaults to ``True`` for the X-API-Key / api-key callers; the MCP bearer door
    passes ``False`` because a failed bearer is neither a confirmed API key nor,
    once the OAuth door has also failed, distinguishable as one.

    ``user_id`` attributes the failure to a resolved-but-refused user (the
    access-group gate rejects a VALID credential); credential failures have no
    user to attribute and leave it ``None``.

    The row is written on a fresh, explicitly-committed cursor in production so it
    survives the request-transaction reset a rejected ``/mcp`` bearer causes (and
    on the request cursor under tests) -- see :func:`audit.write_audit_row`. The
    write runs as ``SUPERUSER_ID`` because no user is authenticated on a failure.

    Throttled per IP (see ``_auth_failure_limiter``): past the window budget for
    that IP the write is skipped, so a bad-credential flood on the legacy REST /
    XML-RPC callers cannot amplify into one fsync per request. Under normal
    (non-flood) load the budget is never reached, so legitimate audit rows are
    never dropped.
    """
    if _auth_failure_limiter.is_limited(
        (request.db, request.httprequest.remote_addr), _AUTH_FAILURE_MAX
    ):
        return
    audit.write_audit_row(
        SUPERUSER_ID,
        lambda mcp_log: mcp_log.log_authentication(
            success=False,
            user_id=user_id,
            api_key_used=api_key_used,
            ip_address=request.httprequest.remote_addr,
            error_message=error_message,
        ),
        "Failed to write MCP auth-failure audit row",
    )


# Audit-log reason recorded when a valid credential resolves a user outside
# the MCP access group; shared by every door so the rows stay greppable.
MCP_GROUP_DENIED_MESSAGE = (
    "User is not a member of the MCP User group (mcp_server.group_mcp_user)"
)


def log_mcp_group_denied(user, *, api_key_used):
    """Audit a valid credential refused for lacking the MCP access group.

    Shared by every door (native bearer, REST/session, XML-RPC proxy) so the
    resolved user is always recorded; rides ``_log_auth_failure``'s per-IP
    throttle. ``api_key_used`` records the credential kind for the row.
    """
    _log_auth_failure(
        MCP_GROUP_DENIED_MESSAGE, api_key_used=api_key_used, user_id=user.id
    )


def user_has_mcp_access(user):
    """Whether ``user`` may use any MCP surface at all.

    The per-user opt-in: membership in the "MCP User" group
    (``group_mcp_admin`` implies it, and ``base.group_system`` implies
    ``group_mcp_admin``, so system administrators always pass). Checked at
    credential USE time on every door -- the native ``/mcp`` bearer (API key
    or OAuth token), the X-API-Key/session REST routes and the XML-RPC proxy
    (both its API-key and its uid/password credentials, the latter gated only
    once core has verified the password) -- and at OAuth authorize/token time,
    so removing the group cuts off outstanding tokens, keys and password
    logins immediately, like the archived-user and deactivated-client use-time
    checks.

    Portal/share users can never hold the group (it implies
    ``base.group_user``), so they are rejected at every door by construction.
    ``user`` may be any singleton ``res.users`` record.
    """
    user_su = user.sudo()  # sudo: membership read only, no user bound yet
    group = user_su.env.ref("mcp_server.group_mcp_user", raise_if_not_found=False)
    # Membership via ``groups_id``: implied memberships are materialized into
    # it on write (``UsersImplied``/``GroupsImplied`` in core), so a plain m2m
    # read sees a grant/revocation on the next request with no per-user cache
    # entry to go stale -- and no debug-mode special-casing the way
    # ``has_group`` treats ``base.group_no_one``. Fail closed if the group
    # record is missing.
    return bool(group) and group.id in user_su.groups_id.ids


def get_user_from_api_key(api_key, *, allowed_scopes=("rpc",), log_failure=True):
    """
    Get user from API key.

    :param api_key: The API key to validate
    :param allowed_scopes: Ordered API-key scopes to probe against core
        ``res.users.apikeys._check_credentials`` (which matches
        ``scope IS NULL OR scope = %s``); the first scope that resolves the key
        wins. Defaults to ``("rpc",)`` -- the general-RPC blast radius the legacy
        callers expect (``validate_api_key`` gating the X-API-Key REST routes, and
        the ``/mcp/xmlrpc/object`` proxy's user identification in ``api.py``): a
        NULL/global legacy key and an explicit ``rpc`` key authenticate, but a
        dedicated ``mcp``-scope key does NOT. Only the ``/mcp`` bearer door
        (``ir_http._auth_method_mcp``) passes ``("mcp", "rpc")`` so an ``mcp`` key
        authenticates there -- and there only -- honouring its smaller blast radius:
        a leaked ``mcp`` key cannot be used for general RPC. (A NULL/global key
        matches every scope probe, so it keeps authenticating everywhere.)
    :param log_failure: Whether to write a failure audit row when the key does
        not resolve. Callers that try another credential afterwards (e.g. the
        MCP bearer door also accepts OAuth access tokens) pass ``False`` so a
        non-key bearer does not log a spurious "Invalid API key" failure before
        the other door succeeds; they log a single failure only if all doors fail.
    :return: res.users record or None
    """
    if not api_key:
        return None

    try:
        apikeys = request.env[
            "res.users.apikeys"
        ].sudo()  # sudo: validate API key without user context
        # Probe each allowed scope in order; the first hit wins. Core
        # ``_check_credentials`` matches ``scope IS NULL OR scope = %s``: a
        # NULL/global legacy key matches EVERY probe and keeps working; a key
        # stored with scope ``mcp`` matches only an ``mcp`` probe (so under the
        # default ``("rpc",)`` it is rejected here -- the intended smaller blast
        # radius); an explicitly ``rpc``-scoped key matches only an ``rpc`` probe.
        # At most ``len(allowed_scopes)`` queries, so the audit rows below fire once.
        user_id = None
        for scope in allowed_scopes:
            user_id = apikeys._check_credentials(scope=scope, key=api_key)
            if user_id:
                break
        if not user_id:
            if log_failure:
                _log_auth_failure("Invalid API key")
            return None
        users = request.env["res.users"].sudo()  # sudo: browse user by id, no ctx
        user = users.browse(user_id).exists()
        if user and user.active:
            # No per-request auth_success audit row: the native /mcp operation
            # rows (mcp.log model_access) already carry user + IP + auth method,
            # so logging a success here on every request is redundant noise. A
            # rejected key still logs an auth_failure (below / _log_auth_failure).
            return user
        else:
            if log_failure:
                _log_auth_failure("User not found or inactive")
            return None
    except Exception as e:
        _logger.exception("Error validating API key")
        if log_failure:
            _log_auth_failure(str(e))
        return None


def validate_api_key(req):
    """Validate API key from request headers."""
    api_key = req.httprequest.headers.get("X-API-Key")
    if not api_key:
        return None

    return get_user_from_api_key(api_key)


def get_user_from_session():
    """Get user from the current Odoo session.

    With auth="public" routes, Odoo resolves the session automatically
    when a valid session_id cookie is present.
    """
    try:
        user = request.env.user
        if user and user.id and user.id != request.env.ref("base.public_user").id:
            return user
    except Exception as e:
        _logger.debug("Session auth check failed: %s", e)
    return None


def require_auth(func):
    """
    Decorator for endpoints requiring authentication.

    Checks authentication in order:
    1. X-API-Key header
    2. Odoo session cookie (if user is logged in)
    3. Rejects with 401

    A resolved user who is not a member of the MCP access group
    (:func:`user_has_mcp_access`) is refused with 403 -- authenticated but
    not authorized, distinct from the 401 credential failures.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        from . import response_utils

        user = validate_api_key(request)
        # Record which credential actually resolved the user, not whether an
        # X-API-Key header was merely present: a stale key alongside a valid
        # session cookie authenticates via the session, and the denial row
        # below must say so.
        api_key_used = user is not None

        if not user:
            user = get_user_from_session()

        if user and not user_has_mcp_access(user):
            # The credential is VALID; the user is simply not opted in to MCP.
            log_mcp_group_denied(user, api_key_used=api_key_used)
            return response_utils.error_response(
                "Access denied: your user is not authorized for MCP. Ask your "
                "Odoo administrator for the 'MCP User' group.",
                "E403",
                status=403,
            )

        if not user:
            # A supplied-but-invalid API key already logged its own "Invalid API
            # key" failure inside get_user_from_api_key; only log the generic
            # no-credential failure when no key was presented at all, so a bad key
            # is not double-counted against the throttle. Both routes go through
            # the throttled, cursor-disciplined _log_auth_failure.
            if not request.httprequest.headers.get("X-API-Key"):
                _log_auth_failure("No valid API key or session", api_key_used=False)
            return response_utils.error_response(
                "Authentication required. Provide a valid API key "
                "(X-API-Key header) or session cookie.",
                "E401",
                status=401,
            )

        kwargs["user"] = user
        return func(*args, **kwargs)

    return wrapper


# Backwards compatibility alias
require_api_key = require_auth

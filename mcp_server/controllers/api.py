import logging
import xmlrpc.client as xmlrpclib  # nosec
from datetime import datetime
from typing import Any, Optional, Tuple

import defusedxml.xmlrpc
from passlib.exc import PasswordValueError

from odoo import api, http
from odoo.exceptions import (
    AccessDenied,
    AccessError,
    MissingError,
    RedirectWarning,
    UserError,
)
from odoo.http import request
from odoo.service import (
    common as common_service_root,
    db as db_service_root,
    model as model_service_root,
)
from odoo.tools import config

# Importing the core RPC controller installs its marshaller (dates, bytes,
# Markup) on xmlrpc.client, which the object proxy's responses rely on.
from odoo.addons.base.controllers.rpc import (
    RPC_FAULT_CODE_ACCESS_DENIED,
    RPC_FAULT_CODE_ACCESS_ERROR,
    RPC_FAULT_CODE_APPLICATION_ERROR,
    RPC_FAULT_CODE_WARNING,
)

from . import auth, error_sanitizer, utils
from .rate_limiting import (
    check_rate_limit,
    is_rate_limiting_enabled,
    record_api_request,
    retry_after_seconds,
)

_logger = logging.getLogger(__name__)
defusedxml.xmlrpc.monkey_patch()

# XML-RPC fault codes aligned with HTTP status codes
XMLRPC_FAULT_CODES = {
    "bad_request": 400,
    "unauthorized": 401,
    "forbidden": 403,
    "not_found": 404,
    "rate_limit": 429,
    "internal_error": 500,
}


# Fault string for a verified credential whose user lacks the MCP access group;
# shared by the ``common`` login methods and the ``object`` proxy.
MCP_GROUP_DENIED_FAULT = (
    "MCP access denied: user is not a member of the MCP User group."
)


def _check_served_database(db_name: Any) -> None:
    """Refuse a call that names another database than the request is served from.

    Core verifies and runs a call in the database it names, while the MCP gates
    (group membership, enabled models, rate limit, audit) read the request's
    database; the two must be the same.
    """
    if db_name != request.db:
        raise xmlrpclib.Fault(
            XMLRPC_FAULT_CODES["bad_request"],
            "The call names a database this endpoint does not serve. Select the "
            "database through the host name.",
        )


# ``common`` service methods that return the uid of a verified credential.
_COMMON_LOGIN_METHODS = frozenset({"login", "authenticate"})


def _generate_xmlrpc_fault(code: int, message: str) -> str:
    """Build an XML-RPC fault string with standardized HTTP-aligned codes."""
    fault = xmlrpclib.Fault(code, message)
    return xmlrpclib.dumps(fault, methodresponse=1, allow_none=1)


def _fault_code_for(exc: Exception) -> int:
    """XML-RPC fault code for an error raised by a proxied call.

    Odoo user errors (``UserError`` and its subclasses) are expected outcomes of
    a call and keep the codes core's ``/xmlrpc/2`` sends, so XML-RPC clients can
    tell a business error from a transport failure: 3 for a refused login, 4 for
    an access error, 2 for any other user error. A ``ValueError`` or
    ``TypeError`` (an unknown field or a wrong value, for example) gets core's
    code 1. Anything else is an internal fault.
    """
    if isinstance(exc, AccessDenied):
        return RPC_FAULT_CODE_ACCESS_DENIED
    if isinstance(exc, AccessError):
        return RPC_FAULT_CODE_ACCESS_ERROR
    if isinstance(exc, (UserError, RedirectWarning)):
        return RPC_FAULT_CODE_WARNING
    if isinstance(exc, (ValueError, TypeError)):
        return RPC_FAULT_CODE_APPLICATION_ERROR
    return XMLRPC_FAULT_CODES["internal_error"]


def _audit_code_for(exc: Exception) -> int:
    """HTTP-style status recorded in the audit log for an error of a proxied call."""
    if isinstance(exc, AccessDenied):
        return XMLRPC_FAULT_CODES["unauthorized"]
    if isinstance(exc, AccessError):
        return XMLRPC_FAULT_CODES["forbidden"]
    if isinstance(exc, MissingError):
        return XMLRPC_FAULT_CODES["not_found"]
    if isinstance(exc, (UserError, RedirectWarning, ValueError, TypeError)):
        return XMLRPC_FAULT_CODES["bad_request"]
    return XMLRPC_FAULT_CODES["internal_error"]


def _fault_for_exception(controller_name: str, exc: Exception) -> Tuple[int, str]:
    """Fault code and client-safe message for an error raised by a proxied call.

    A user error returns its sanitized message and logs one line; an internal
    fault is logged with its traceback by the sanitizer and the client gets the
    generic message, never the raw exception text. A ``ValueError`` or
    ``TypeError`` returns its sanitized text, as on the native endpoint.
    """
    code = _fault_code_for(exc)
    if code == RPC_FAULT_CODE_APPLICATION_ERROR:
        # A ValueError or TypeError is a bad argument or a server bug, so the
        # log keeps its traceback, as on the native endpoint.
        _logger.warning(
            "%s: %s: %s",
            controller_name,
            type(exc).__name__,
            utils._one_line(str(exc)),
            exc_info=exc,
        )
        return code, error_sanitizer.sanitize_message(str(exc))
    if code != XMLRPC_FAULT_CODES["internal_error"]:
        _logger.info(
            "%s: %s: %s", controller_name, type(exc).__name__, utils._one_line(str(exc))
        )
    return code, error_sanitizer.sanitize_exception(
        exc, f"Unhandled error in {controller_name}"
    )


def _get_client_ip() -> Optional[str]:
    """Get client IP address from request."""
    if request and hasattr(request, "httprequest"):
        return request.httprequest.remote_addr
    return None


def _dispatch_common_service(method: str, params) -> Any:
    """Serve the ``common`` RPC service, gating its login methods on MCP access.

    ``login`` / ``authenticate`` return the uid of a credential core has just
    verified, or ``False`` for a wrong one. The MCP access-group gate runs only
    on that verified uid, like the object proxy's password path: a wrong
    credential gets core's ``False`` whatever the user's membership, so there is
    no membership oracle. ``version`` stays open.
    """
    if method in _COMMON_LOGIN_METHODS and params:
        _check_served_database(params[0])
    result = common_service_root.dispatch(method, params)
    if method in _COMMON_LOGIN_METHODS and result:
        user = request.env["res.users"].sudo().browse(result)
        if not auth.user_has_mcp_access(user):
            auth.log_mcp_group_denied(user, api_key_used=False)
            raise xmlrpclib.Fault(
                XMLRPC_FAULT_CODES["forbidden"], MCP_GROUP_DENIED_FAULT
            )
    return result


def _dispatch_service_xmlrpc(controller_name: str, service_dispatch) -> Any:
    """Guard + parse + dispatch + marshal + fault scaffold for the stateless
    ``common`` / ``db`` XML-RPC proxies.

    The ``object`` proxy differs (custom access-controlled dispatch and Odoo's
    date-aware marshaller) and keeps its own handler below.
    """
    if not utils.is_mcp_enabled():
        fault_response = _generate_xmlrpc_fault(
            XMLRPC_FAULT_CODES["forbidden"],
            "MCP Server is disabled globally.",
        )
        return request.make_response(fault_response, [("Content-Type", "text/xml")])

    data = request.httprequest.data
    try:
        params, method = xmlrpclib.loads(data)
        result = service_dispatch(method, params)
        response_data = xmlrpclib.dumps((result,), methodresponse=1, allow_none=1)
        return request.make_response(response_data, [("Content-Type", "text/xml")])
    except xmlrpclib.Fault as e:
        _logger.warning(
            f"{controller_name} XML-RPC Fault: "
            f"Code {e.faultCode}, String: {e.faultString}"
        )
        return request.make_response(
            xmlrpclib.dumps(e, methodresponse=1, allow_none=1),
            [("Content-Type", "text/xml")],
        )
    except Exception as e:
        fault_response = _generate_xmlrpc_fault(
            *_fault_for_exception(controller_name, e)
        )
        return request.make_response(fault_response, [("Content-Type", "text/xml")])


class MCPCommonController(http.Controller):
    # auth="none"/csrf=False: stateless XML-RPC proxy. Credentials travel in the
    # call params (db, uid, api-key), not an Odoo session cookie, so there is no
    # CSRF surface (mirrors stock /xmlrpc/2/common); MCP is gated inside dispatch.
    @http.route(
        "/mcp/xmlrpc/common", type="http", auth="none", methods=["POST"], csrf=False
    )
    def index(self, **kwargs):
        return _dispatch_service_xmlrpc("MCPCommonController", _dispatch_common_service)


class MCPDatabaseController(http.Controller):
    # auth="none"/csrf=False: stateless XML-RPC proxy, same rationale as
    # /mcp/xmlrpc/common -- no session cookie, so no CSRF surface (mirrors stock
    # /xmlrpc/2/db); the global MCP kill-switch is checked inside dispatch.
    @http.route(
        "/mcp/xmlrpc/db", type="http", auth="none", methods=["POST"], csrf=False
    )
    def index(self, **kwargs):
        return _dispatch_service_xmlrpc(
            "MCPDatabaseController", db_service_root.dispatch
        )


class MCPObjectController(http.Controller):
    def _validate_request(self, xmlrpc_method: str, params: list) -> None:
        """Validate the XML-RPC method and params; raise Fault if invalid."""
        if xmlrpc_method != "execute_kw":
            # The name comes from a caller without credentials: on one line and
            # cut, so it cannot forge log lines (CWE-117).
            method_name = utils._one_line(xmlrpc_method)[:80]
            _logger.warning(
                f"MCPObjectController received non-execute_kw method: {method_name}"
            )
            # The audit row has the same per-IP budget as an auth failure.
            if (
                request
                and hasattr(request, "env")
                and not auth._auth_failure_limiter.is_limited(
                    (request.db, request.httprequest.remote_addr),
                    auth._AUTH_FAILURE_MAX,
                )
            ):
                request.env["mcp.log"].sudo().log_error(
                    error_message=f"MCPObjectController: "
                    f"Unsupported method {method_name}. "
                    f"Only execute_kw is allowed.",
                    error_code="E400",
                    endpoint="/mcp/xmlrpc/object",
                    operation=method_name,
                    ip_address=_get_client_ip(),
                )
            raise xmlrpclib.Fault(
                XMLRPC_FAULT_CODES["bad_request"],
                f"MCPObjectController: Unsupported method "
                f"{method_name}. Only execute_kw is allowed.",
            )

        if len(params) < 5:
            raise xmlrpclib.Fault(
                XMLRPC_FAULT_CODES["bad_request"],
                "MCPObjectController: Insufficient parameters for execute_kw.",
            )

    def _identify_user(self, auth_token: Any) -> Optional[Any]:
        """The user of an API key, or ``None`` when the token is no API key."""
        if isinstance(auth_token, str) and len(auth_token) > 20:
            return auth.get_user_from_api_key(auth_token) or None
        return None

    def _apply_rate_limiting(
        self,
        user_obj: Optional[Any],
        user_id: int,
        model_name: str,
        model_method: str,
    ) -> None:
        """Apply rate limiting if enabled; raise Fault if the limit is exceeded.

        ``user_id`` must be a proven identity: a uid that nobody proved would
        let a caller without credentials use up the budget of another user,
        which ``/mcp`` shares.
        """
        if not is_rate_limiting_enabled():
            return

        # Namespace the rate-limit bucket by database (cross-tenant collision).
        dbname = request.env.cr.dbname

        if not check_rate_limit(user_id, dbname):
            _logger.warning(
                f"MCP XML-RPC: Rate limit exceeded for user ID "
                f"{user_id} on {model_name}.{model_method}."
            )
            env_for_log = request.env(user=user_obj.id) if user_obj else request.env
            env_for_log["mcp.log"].sudo().log_rate_limit_exceeded(
                user_id=user_id,
                endpoint="/mcp/xmlrpc/object",
                ip_address=_get_client_ip(),
            )
            raise xmlrpclib.Fault(
                XMLRPC_FAULT_CODES["rate_limit"],
                "Too many requests. Rate limit exceeded.",
            )
        record_api_request(user_id, dbname)

    def _get_env_for_user(self, user_obj: Optional[Any], uid: Any) -> Any:
        """Return the Odoo environment for the resolved user context."""
        if user_obj:
            return request.env(user=user_obj.id)

        if uid:
            try:
                return request.env(user=uid)
            except Exception as e:
                # Log the failure but continue with default environment
                _logger.debug(f"Failed to create environment for uid {uid}: {e}")

        return request.env

    def _extract_record_ids(self, params: list) -> Optional[list]:
        """Extract record IDs from params[5] if present, else None."""
        if len(params) > 5 and isinstance(params[5], list):
            # For methods like read, write that have record IDs in params[5]
            if params[5] and isinstance(params[5][0], int):
                return params[5]
        return None

    @staticmethod
    def _verify_uid_passwd(uid: int, auth_token: Any) -> None:
        """Verify a (uid, password) credential the way core does, or raise.

        Delegates to the ``res.users.check`` classmethod -- the exact call
        ``model_service_root.dispatch`` makes through ``security.check`` -- so
        the gate can never accept a credential core would refuse. ``check``
        opens its own cursor internally (``cls.pool.cursor()``), so it is safe
        on this ``auth="none"`` route even though the route defaults to
        ``readonly`` (``odoo/http.py``), and it is ``@ormcache('uid',
        'passwd')``: core's own call inside dispatch hits the cache, so the
        second verification costs no password hashing.

        Under ``test_enable`` the check body runs inlined on the REQUEST
        cursor instead: ``check``'s internal ``cls.pool.cursor()`` is refused
        outright when opened from HttpCase's readonly test cursor. Mirrors
        ``controllers/audit.py``'s test/prod cursor discipline.

        Failures propagate untouched -- the caller must not turn a rejected
        credential into anything the caller can distinguish.
        """
        if config["test_enable"]:
            # Core's ``check`` body on the request cursor: same auth counter,
            # same active check, same credential verification -- minus the
            # cursor it cannot open here and the ormcache entry (tests never
            # reuse a warm one anyway).
            users = api.Environment(request.env.cr, uid, {})["res.users"]
            with users._assert_can_auth(user=uid):
                if not users.env.user.active:
                    raise AccessDenied()
                users._check_credentials(auth_token, {"interactive": False})
            return
        request.env["res.users"].check(request.env.cr.dbname, uid, auth_token)

    def _gate_password_credential(self, uid: Any, auth_token: Any) -> int:
        """Apply the MCP access-group gate to a (uid, password) credential.

        Returns the proven uid as an int.

        The uid a client puts in ``execute_kw`` is unverified, so the gate
        cannot read it directly -- a 403-vs-fault split would turn this
        ``auth="none"`` route into a membership oracle over the uid space. So
        verify the credential FIRST, exactly as core will, and gate only the
        identity that survives: the 403 is then reachable only by a caller who
        already proved the password, which tells them nothing they did not
        already know.

        ``uid`` is normalised with ``int()`` because that is precisely what
        ``model_service_root.dispatch`` does before authenticating it -- gating
        only a Python ``int`` would let ``<string>42</string>`` walk past the
        gate and still authenticate downstream. A uid that ``int()`` rejects,
        and a password that is empty or not a string, are refused here as core
        refuses them: the MCP gate after this one reads records in the uid's
        environment, so it must never run for an unproven credential.

        ``res.users.check`` is ``@ormcache('uid', 'passwd')``, so core's own
        call inside dispatch hits the cache -- the second verification costs no
        password hashing.

        A failed verification is re-raised as a bare ``AccessDenied``, so it
        faults identically for every uid: no membership signal, and no existence
        signal either -- core raises ``MissingError`` for a nonexistent uid
        (``_assert_can_auth``'s ``except AccessDenied`` only counts login
        failures; the ``env.user`` read inside it is not caught), which would
        otherwise fault with a different code and message.
        """
        try:
            uid = int(uid)
        except (TypeError, ValueError, OverflowError):
            uid = None
        try:
            if uid is None or not auth_token or not isinstance(auth_token, str):
                raise AccessDenied()
            self._verify_uid_passwd(uid, auth_token)
        # Only a refused credential: any other error (a database outage, say)
        # propagates and is logged with its traceback as an internal error.
        # Passlib refuses a password it cannot hash, one over 4096 characters
        # for example, only for an existing user.
        except (AccessDenied, MissingError, PasswordValueError):
            # The rejection must leave an mcp.log trace:
            # every other door records its denials, and without this row a
            # failed uid/password on this route commits with no audit entry at
            # all. No user_id -- the uid is client-supplied and unverified, so
            # attributing the row would let a caller seed the audit log with
            # arbitrary identities. Throttled per IP like every auth failure.
            auth._log_auth_failure(
                "Invalid uid/password credential", api_key_used=False
            )
            raise AccessDenied() from None

        user = request.env["res.users"].sudo().browse(uid)
        if not auth.user_has_mcp_access(user):
            auth.log_mcp_group_denied(user, api_key_used=False)
            raise xmlrpclib.Fault(
                XMLRPC_FAULT_CODES["forbidden"], MCP_GROUP_DENIED_FAULT
            )
        return uid

    def _mcp_object_dispatch(self, xmlrpc_method: str, params: list):
        """Dispatch an XML-RPC object call through the MCP access-control gate."""
        self._validate_request(xmlrpc_method, params)

        # Standard params for execute_kw: (db_name, uid, password, model_name,
        # model_method, args_array, kwargs_dict)
        uid = params[1]
        auth_token = params[2]
        # Collapse CR/LF in the client-supplied method name before it reaches
        # any log sink or the mcp.log audit table: on this auth="none" route a
        # newline-bearing name would otherwise forge log/audit lines (CWE-117).
        # A legitimate method name has no CR/LF, so this is a no-op for real
        # calls; a forged one is denied at check_mcp_access before dispatch runs.
        model_method = utils._one_line(params[4])

        try:
            model_name = utils.sanitize_model_name(params[3])
        except ValueError as e:
            raise xmlrpclib.Fault(
                XMLRPC_FAULT_CODES["bad_request"], f"Invalid model name: {e}"
            ) from e

        _check_served_database(params[0])
        user_obj = self._identify_user(auth_token)

        # Per-user MCP access gate. Two credential kinds reach this proxy and
        # both are gated -- but only ever on a VERIFIED identity, never on the
        # raw client-supplied uid: acting on that unverified value would answer
        # "is uid N an MCP member?" for an unauthenticated caller sweeping the
        # uid space. See ``_gate_password_credential`` for how the password
        # path earns a verified identity before the gate runs.
        if user_obj is not None:
            # An rpc-/NULL-scope API key, already resolved by _identify_user.
            if not auth.user_has_mcp_access(user_obj):
                auth.log_mcp_group_denied(user_obj, api_key_used=True)
                raise xmlrpclib.Fault(
                    XMLRPC_FAULT_CODES["forbidden"], MCP_GROUP_DENIED_FAULT
                )
            user_id = user_obj.id
        else:
            user_id = self._gate_password_credential(uid, auth_token)

        self._apply_rate_limiting(user_obj, user_id, model_name, model_method)

        env_for_check = self._get_env_for_user(user_obj, user_id)

        start_time = datetime.now()
        ip_address = _get_client_ip()

        denied_message = None
        fault_code = XMLRPC_FAULT_CODES["forbidden"]
        if not utils.check_mcp_access(env_for_check, model_name, model_method):
            denied_message = (
                f"Access denied by MCP for model "
                f"'{model_name}' method '{model_method}'."
            )
        else:
            # The values of a write can reach other models: x2many commands,
            # context defaults and the owner of an attachment. Gate them as the
            # native tools do.
            try:
                denied_message = utils.legacy_write_denial(
                    env_for_check,
                    model_name,
                    model_method,
                    params[5] if len(params) > 5 else [],
                    params[6] if len(params) > 6 else {},
                )
            except utils.UnreadableWriteValues as e:
                denied_message = utils._one_line(str(e))
                fault_code = XMLRPC_FAULT_CODES["bad_request"]
            except Exception as e:
                self._log_call_error(
                    env_for_check, e, model_name, model_method, user_id, ip_address
                )
                raise
        if denied_message:
            env_for_check["mcp.log"].sudo().log_permission_denied(
                model_name=model_name,
                operation=model_method,
                user_id=user_id,
                endpoint="/mcp/xmlrpc/object",
                ip_address=ip_address,
                error_message=denied_message,
            )
            raise xmlrpclib.Fault(fault_code, denied_message)

        _logger.info(
            f"MCP XML-RPC: Access GRANTED for {model_name}.{model_method} "
            f"(User ID: {user_id if user_id else 'N/A'})"
        )

        try:
            result = model_service_root.dispatch(xmlrpc_method, params)

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            env_for_check["mcp.log"].sudo().log_model_access(
                model_name=model_name,
                operation=model_method,
                user_id=user_id,
                record_ids=self._extract_record_ids(params),
                endpoint="/mcp/xmlrpc/object",
                http_method="POST",
                duration_ms=duration_ms,
                ip_address=ip_address,
            )

            return result
        except Exception as e:
            self._log_call_error(
                env_for_check, e, model_name, model_method, user_id, ip_address
            )
            raise

    @staticmethod
    def _log_call_error(env, exc, model_name, model_method, user_id, ip_address):
        """Write the audit row of a proxied call that raised ``exc``."""
        env["mcp.log"].sudo().log_error(
            error_message=str(exc),
            error_code=f"E{_audit_code_for(exc)}",
            endpoint="/mcp/xmlrpc/object",
            model_name=model_name,
            operation=model_method,
            user_id=user_id,
            ip_address=ip_address,
        )

    # auth="none"/csrf=False: stateless XML-RPC proxy -- credentials ride in the
    # call params, not a session cookie, so no CSRF surface (mirrors stock
    # /xmlrpc/2/object). Every call is access-controlled by check_mcp_access
    # (per-model/operation MCP gate) inside _mcp_object_dispatch.
    @http.route(
        "/mcp/xmlrpc/object", type="http", auth="none", methods=["POST"], csrf=False
    )
    def index(self, **kwargs):
        if not utils.is_mcp_enabled():
            fault_response = _generate_xmlrpc_fault(
                XMLRPC_FAULT_CODES["forbidden"],
                "MCP Server is disabled globally.",
            )
            return request.make_response(fault_response, [("Content-Type", "text/xml")])

        data = request.httprequest.data
        try:
            params, method = xmlrpclib.loads(data)
            result = self._mcp_object_dispatch(method, params)
            # Use Odoo's custom XML-RPC marshaller that handles date objects
            response_data = xmlrpclib.dumps(
                (result,), methodresponse=1, allow_none=False
            )
            return request.make_response(response_data, [("Content-Type", "text/xml")])
        except xmlrpclib.Fault as e:
            _logger.warning(
                f"MCPObjectController XML-RPC Fault: "
                f"Code {e.faultCode}, String: {e.faultString}"
            )
            headers = [("Content-Type", "text/xml")]
            if e.faultCode == XMLRPC_FAULT_CODES["rate_limit"]:
                # XML-RPC faults travel on HTTP 200; the back-off hint rides
                # along as a header, same value as the /mcp and REST 429s.
                headers.append(("Retry-After", str(retry_after_seconds())))
            return request.make_response(
                xmlrpclib.dumps(e, methodresponse=1, allow_none=1), headers
            )
        except Exception as e:
            fault_response = _generate_xmlrpc_fault(
                *_fault_for_exception("MCPObjectController", e)
            )
            return request.make_response(fault_response, [("Content-Type", "text/xml")])

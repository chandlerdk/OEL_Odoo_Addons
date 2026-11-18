"""Custom HTTP dispatcher for the native MCP protocol.

Registers a ``type='mcp'`` dispatcher with Odoo's HTTP layer: the base
:class:`odoo.http.Dispatcher` records every subclass in ``http._dispatchers``
keyed by its ``routing_type`` (``Dispatcher.__init_subclass__``), so declaring
``routing_type = "mcp"`` is enough to make ``@mcp_route`` endpoints routable.

The dispatcher centralises the MCP transport concerns in one place: CORS /
preflight handling, parsing the raw JSON-RPC 2.0 request body, and rendering
otherwise-unhandled errors as JSON-RPC error objects (never leaking tracebacks).
"""

import logging

import werkzeug.exceptions
from werkzeug.exceptions import HTTPException

from odoo import http
from odoo.http import CORS_MAX_AGE, Response

from . import jsonrpc, utils
from .error_sanitizer import GENERIC_ERROR_MESSAGE

_logger = logging.getLogger(__name__)


class MCPDispatcher(http.Dispatcher):
    """Serve ``@mcp_route`` endpoints (plain JSON-RPC 2.0 over HTTP POST)."""

    routing_type = "mcp"
    mimetypes = ("application/json",)

    def __init__(self, request):
        super().__init__(request)
        self.jsonrequest = {}
        # The route's body cap, set in pre_dispatch, applied in dispatch.
        self.max_content_length = None
        # Read by ``ir.http._handle_error`` to echo the request id on errors.
        self.request_id = None

    @classmethod
    def is_compatible_with(cls, request):
        # Selected explicitly via routing type 'mcp', so always compatible.
        return True

    def pre_dispatch(self, rule, args):
        """Apply CORS headers and short-circuit ``OPTIONS`` preflight with 204.

        Browser MCP clients send a CORS preflight before the real POST; the
        headers are also set on the actual request so its JSON-RPC response is
        readable cross-origin. Mirrors :meth:`odoo.http.Dispatcher.pre_dispatch`
        but kept here so the transport concern lives in one place, including
        core's per-route ``max_content_length`` cap (``@mcp_route`` sets a small
        default) so an oversized body is refused before it is buffered/parsed.
        """
        routing = rule.endpoint.routing
        self.request.session.can_save &= routing.get("save_session", True)

        # Cap the request body (before get_json_data reads it in dispatch): an
        # over-cap body makes werkzeug raise RequestEntityTooLarge (413), which
        # handle_error passes through as an HTTPException. Set here because this
        # override does not call super().pre_dispatch.
        max_content_length = routing.get("max_content_length")
        if max_content_length is not None:
            self.request.httprequest.max_content_length = max_content_length
        self.max_content_length = max_content_length

        # Origin validation (2025-11-25 spec MUST; DNS-rebinding protection).
        # Enforced only when the admin configured an allowlist: with it unset
        # any Origin is accepted (permissive default) -- every request on this
        # endpoint already needs an explicit bearer token, so there is no
        # ambient authority for a rebound page to ride on. Requests without an
        # Origin header (all native MCP clients) always pass; only browsers
        # send it. Checked before the preflight short-circuit so a disallowed
        # origin's OPTIONS is refused too.
        origin = self.request.httprequest.headers.get("Origin")
        allowed_origins = utils.get_allowed_origins()
        if allowed_origins and origin:
            if origin.rstrip("/").lower() not in allowed_origins:
                _logger.info("MCP request refused: Origin %r not allowed", origin)
                werkzeug.exceptions.abort(
                    self.request.make_json_response(
                        jsonrpc.make_error(
                            None, jsonrpc.SERVER_ERROR, "Origin not allowed."
                        ),
                        status=403,
                    )
                )

        # Protocol-version validation (Streamable HTTP spec, 2025-06-18+).
        # Post-initialize requests carry the negotiated version in this header;
        # the spec says a server SHOULD answer 400 to an unsupported value. Only
        # a *present* value is checked -- a missing header is tolerated (older
        # clients, and the initialize request itself, omit it), so this never
        # breaks the handshake and only rejects genuinely incompatible clients.
        # OPTIONS preflights carry the header name in Access-Control-Request-
        # Headers, not as this header, so they pass through untouched. Imported
        # lazily: this transport module loads before ``mcp`` in
        # ``controllers/__init__``, and the constant is only needed per-request.
        protocol_version = self.request.httprequest.headers.get("MCP-Protocol-Version")
        if protocol_version:
            from .mcp import SUPPORTED_PROTOCOL_VERSIONS

            if protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
                _logger.info(
                    "MCP request refused: unsupported MCP-Protocol-Version %r",
                    protocol_version,
                )
                werkzeug.exceptions.abort(
                    self.request.make_json_response(
                        jsonrpc.make_error(
                            None,
                            jsonrpc.SERVER_ERROR,
                            "Unsupported MCP-Protocol-Version.",
                        ),
                        status=400,
                    )
                )

        headers = self.request.future_response.headers
        if allowed_origins:
            # The allowlist doubles as the CORS policy: echo the (validated)
            # Origin instead of the wildcard, and mark the response as
            # Origin-dependent so a shared cache never replays it cross-origin.
            if origin:
                headers.set("Access-Control-Allow-Origin", origin)
            headers.set("Vary", "Origin")
        else:
            headers.set("Access-Control-Allow-Origin", routing.get("cors") or "*")
        headers.set(
            "Access-Control-Allow-Methods",
            ", ".join(routing.get("methods") or ["POST"]),
        )

        if self.request.httprequest.method == "OPTIONS":
            headers.set("Access-Control-Max-Age", CORS_MAX_AGE)
            headers.set(
                "Access-Control-Allow-Headers",
                # MCP-Protocol-Version is REQUIRED on every post-initialize
                # request by the Streamable HTTP spec (2025-06-18 and later --
                # both advertised revisions); omitting it here makes a browser
                # client's preflight fail and blocks the real POST.
                # Mcp-Session-Id is allowed too as harmless future-proofing.
                "Origin, X-Requested-With, Content-Type, Accept, Authorization, "
                "Range, MCP-Protocol-Version, Mcp-Session-Id",
            )
            werkzeug.exceptions.abort(Response(status=204))

    def dispatch(self, endpoint, args):
        """Parse the raw JSON-RPC envelope and route it to ``endpoint``.

        MCP speaks plain JSON-RPC 2.0, so (unlike core's ``JsonRPCDispatcher``,
        which unwraps Odoo's ``{"params": ...}``) the whole request object is the
        message. We merge it into ``request.params`` and let the ``/mcp``
        handler read the parsed envelope, mirroring ``Json2Dispatcher``.
        """
        if self.max_content_length is not None:
            utils.read_bounded_body(self.request.httprequest, self.max_content_length)
        try:
            self.jsonrequest = self.request.get_json_data()
        except (ValueError, AttributeError, RecursionError):
            # Malformed body: JSON-RPC parse error; the request id is unknown.
            # RecursionError guards a deeply-nested payload (json's recursive
            # descent overflows before ValueError), so it is treated as a parse
            # error too rather than escaping to a generic -32603.
            # ``request.params`` must still be set so downstream hooks (e.g.
            # ``ir.http._post_dispatch`` -> utm's ``_set_utm``) don't crash.
            self.request.params = dict(args)
            return self._json_rpc_error(None, jsonrpc.PARSE_ERROR, "Parse error")

        self.request_id = (
            self.jsonrequest.get("id") if isinstance(self.jsonrequest, dict) else None
        )
        try:
            self.request.params = {**self.jsonrequest, **args}
        except TypeError:
            # Valid JSON but not an object (e.g. a batch array): leave the
            # handler to reject it as an invalid request.
            self.request.params = dict(args)

        if self.request.db:
            result = self.request.registry["ir.http"]._dispatch(endpoint)
        else:
            result = endpoint(**self.request.params)
        if isinstance(result, Response):
            return result
        return self.request.make_json_response(result)

    def handle_error(self, exc):
        """Render an exception as a JSON-RPC error without leaking internals.

        HTTP transport errors (401/405/...) keep their status and headers so the
        bearer ``WWW-Authenticate`` challenge survives; anything else becomes a
        generic JSON-RPC internal error (-32603).
        """
        if isinstance(exc, HTTPException):
            return exc
        _logger.error("Unhandled error on MCP route", exc_info=exc)
        return self._json_rpc_error(
            self.request_id, jsonrpc.INTERNAL_ERROR, GENERIC_ERROR_MESSAGE
        )

    def _json_rpc_error(self, request_id, code, message):
        """Build a JSON-RPC 2.0 error response (no ``data``/traceback)."""
        return self.request.make_json_response(
            jsonrpc.make_error(request_id, code, message)
        )

"""Native MCP protocol endpoint (``POST /mcp``).

Speaks plain JSON-RPC 2.0 over HTTP (Streamable HTTP, request/response only).
The custom ``type='mcp'`` dispatcher parses the raw envelope into
``request.params`` and authenticates via ``auth='mcp'`` before this handler
runs; here we only validate the JSON-RPC shape and dispatch on ``method``.

Implements the full MCP surface: the handshake (``initialize``/``ping``/
``notifications/*``), ``tools/list`` + ``tools/call`` (the read/write tools
registered on :class:`mcp.mixin`), and the ``odoo://`` resource methods
(``resources/list`` / ``resources/templates/list`` / ``resources/read``).
Per-request rate limiting, sanitized error handling and independent-cursor
audit logging (``mcp.log``) wrap the dispatch.
"""

import json
import logging
from datetime import datetime

from psycopg2 import IntegrityError, OperationalError

from odoo import _, http
from odoo.exceptions import AccessError
from odoo.http import Response, request

from ..tools.uri_schema import URIParseError, parse_attachment_uri, parse_field_uri
from . import audit, error_sanitizer, jsonrpc, oauth_server, rate_limiting, utils
from .mcp_route import mcp_route

_logger = logging.getLogger(__name__)

# Dedupe the over-limit (429) audit write. A client past its per-minute limit
# gets a 429 on EVERY subsequent request in the window; auditing each one on a
# fresh, explicitly committed cursor (see _audit_on_independent_cursor) would
# let the flood self-amplify into commit/fsync load -- the one part of the
# rate-limit path the request limiter itself does not bound. Record at most one
# rate_limit audit row per (db, user) per window; the 429 response is unaffected.
# In-memory => per worker, like the request limiter it guards.
_rate_limit_audit_limiter = rate_limiting.SlidingWindowLimiter(
    rate_limiting.RATE_LIMIT_WINDOW_MINUTES * 60
)

# Per-IP cap on the error-audit independent-cursor write (see
# _audit_on_independent_cursor). Every rejected / denied / failed tool call
# forces its own fresh cursor + explicit COMMIT (fsync); with the per-user
# request limiter OFF by default, one valid credential can turn a flood of cheap
# malformed calls into one commit per request (write-amplification / bounded
# DoS). Past the cap, the committed write is skipped -- the client response is
# unaffected -- so audit logging is preserved under normal load and only
# throttled under flood. Mirrors ir_http._bearer_failure_limiter. In-memory =>
# per worker.
_AUDIT_WRITE_MAX = 20
_AUDIT_WRITE_WINDOW_SECONDS = 60
_audit_write_limiter = rate_limiting.SlidingWindowLimiter(_AUDIT_WRITE_WINDOW_SECONDS)

# Batch write tools: their success audit row derives the touched ids from the
# tool result (see _batch_touched_ids) and carries the requested batch size.
_BATCH_WRITE_TOOLS = frozenset({"create_records", "update_records"})

# Attachment tools: their audit ``model`` column defaults to ``ir.attachment``
# when the call names no model (a ``odoo://record/...`` URI names its own).
_ATTACHMENT_TOOLS = frozenset(
    {"read_attachment", "list_record_attachments", "upload_attachment"}
)


# Builtin-tool arguments the tool coerces and checks itself, with its own
# precise message: ids and page bounds also take digit strings (a legacy
# tolerance, see mcp.mixin._coerce_record_id and _effective_limit), the batch
# lists also take a JSON / Python-literal string (see _unstringify_list), and
# ``values`` must be a non-empty object ("No values provided ..."). The argument
# validator leaves them to the tool; custom tools get no exemption.
_TOOL_CHECKED_ARGS = frozenset(
    {
        "record_id",
        "record_ids",
        "attachment_id",
        "limit",
        "offset",
        "records",
        "updates",
        "values",
    }
)

# JSON Schema types the argument validator checks, as its messages word them.
_JSON_TYPE_LABELS = {
    "string": "a string",
    "integer": "an integer",
    "number": "a number",
    "boolean": "a boolean",
    "array": "a list",
    "object": "an object",
    "null": "null",
}
# Array item types the validator checks (simple types only), in plural.
_JSON_ITEM_LABELS = {
    "string": "strings",
    "integer": "integers",
    "number": "numbers",
    "boolean": "booleans",
    "null": "nulls",
}


def _tool_error(text):
    """Build an MCP tool-error result (``isError: true``, sanitized text)."""
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _json_type_of(value):
    """JSON Schema type name of a decoded JSON ``value``."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _matches_json_type(value, type_name):
    """Whether ``value`` is of JSON Schema type ``type_name``.

    As in JSON Schema, an integer is a number and a float with no fractional
    part is an integer; a boolean is neither.
    """
    actual = _json_type_of(value)
    if type_name == "number":
        return actual in ("integer", "number")
    if type_name == "integer" and actual == "number":
        return value.is_integer()
    return actual == type_name


def _schema_types(schema, labels):
    """The ``type`` of a (sub)schema as a list of names found in ``labels``.

    ``None`` when the schema declares no type, or one this validator does not
    check (unknown name, nested type for an item, malformed value): such a
    constraint is not enforced rather than refused.
    """
    declared = schema.get("type")
    names = [declared] if isinstance(declared, str) else declared
    if not isinstance(names, list) or not names:
        return None
    if not all(isinstance(name, str) and name in labels for name in names):
        return None
    return names


def _join_alternatives(labels):
    """``["a", "b", "c"]`` -> ``"a, b or c"``."""
    if len(labels) == 1:
        return labels[0]
    return "%s or %s" % (", ".join(labels[:-1]), labels[-1])


def _in_enum(value, options):
    """Whether ``value`` equals one of ``options`` (``True`` is not ``1``).

    Strings match ignoring case and surrounding spaces, as the tools normalize
    their enum arguments (e.g. read_attachment's ``format``) the same way.
    """
    if isinstance(value, str):
        value = value.strip().lower()
        return any(
            isinstance(option, str) and option.lower() == value for option in options
        )
    return any(
        option == value and isinstance(option, bool) == isinstance(value, bool)
        for option in options
    )


def _canonical_enum(value, options):
    """The ``options`` entry that a string ``value`` matched, else ``value``.

    :func:`_in_enum` ignores case and surrounding spaces. The tool, and the
    server action of a custom tool, then receive the option as the schema
    declares it. An exact match wins over a match that ignores case.
    """
    if isinstance(value, str) and isinstance(options, list):
        if value in options:
            return value
        wanted = value.strip().lower()
        for option in options:
            if isinstance(option, str) and option.lower() == wanted:
                return option
    return value


def _argument_violation(key, schema, value):
    """Message for the first way ``value`` breaks property ``schema``, or None.

    Checks the property's ``type`` (unions included), ``enum``, ``minItems``
    and, for simple item types, ``items.type``. Any other keyword, and any
    part of the schema it cannot read, is not enforced.
    """
    return (
        _type_violation(key, schema, value)
        or _enum_violation(key, schema, value)
        or _list_violation(key, schema, value)
    )


def _type_violation(key, schema, value):
    """``type`` part of :func:`_argument_violation`."""
    types = _schema_types(schema, _JSON_TYPE_LABELS)
    if types is None:
        if schema.get("type") is not None:
            _logger.warning(
                "MCP argument %r declares an unsupported type %r; not validated",
                key,
                schema["type"],
            )
        return None
    if any(_matches_json_type(value, name) for name in types):
        return None
    return _(
        "Invalid argument '%(name)s': must be %(expected)s, got %(actual)s.",
        name=key,
        expected=_join_alternatives([_JSON_TYPE_LABELS[t] for t in types]),
        actual=_JSON_TYPE_LABELS[_json_type_of(value)],
    )


def _enum_violation(key, schema, value):
    """``enum`` part of :func:`_argument_violation`."""
    options = schema.get("enum")
    if not isinstance(options, list) or not options or _in_enum(value, options):
        return None
    return _(
        "Invalid argument '%(name)s': must be one of %(options)s.",
        name=key,
        options=", ".join(
            option if isinstance(option, str) else json.dumps(option)
            for option in options
        ),
    )


def _list_violation(key, schema, value):
    """``minItems`` / ``items.type`` part of :func:`_argument_violation`."""
    if not isinstance(value, list):
        return None
    min_items = schema.get("minItems")
    if isinstance(min_items, int) and not isinstance(min_items, bool):
        if len(value) < min_items:
            if min_items == 1:
                return _(
                    "Invalid argument '%(name)s': must be a non-empty list.", name=key
                )
            return _(
                "Invalid argument '%(name)s': must contain at least %(count)s items.",
                name=key,
                count=min_items,
            )
    items = schema.get("items")
    item_types = (
        _schema_types(items, _JSON_ITEM_LABELS) if isinstance(items, dict) else None
    )
    if item_types is None:
        return None
    for index, item in enumerate(value):
        if not any(_matches_json_type(item, name) for name in item_types):
            return _(
                "Invalid argument '%(name)s': must be a list of %(items)s; item "
                "%(index)s is %(actual)s.",
                name=key,
                items=_join_alternatives([_JSON_ITEM_LABELS[t] for t in item_types]),
                index=index,
                actual=_JSON_TYPE_LABELS[_json_type_of(item)],
            )
    return None


def _write_allowed():
    """Whether the current MCP session may invoke write (non-read-only) tools.

    The read/write scope gate applies to OAuth tokens ONLY. Auth stashes
    ``request._mcp_oauth_scope`` on the OAuth front door and NOTHING on the
    API-key path, so we distinguish by presence:

    * attribute absent (``None``) -> API key -> full access (no gate);
    * attribute present but empty -> legacy OAuth token (issued before scopes)
      -> full access;
    * otherwise the space-separated scope must include ``mcp`` or ``mcp:write``;
      a bare ``mcp:read`` grants read-only.
    """
    scope = getattr(request, "_mcp_oauth_scope", None)
    # None (API key) or "" (legacy token) -> ungated; otherwise reuse
    # oauth_server's predicate so the grant-time bound and this request-time
    # gate cannot silently desync on what "write" means.
    return not scope or oauth_server.scope_grants_write(scope)


def _requires_write(annotations):
    """A tool needs write access unless it declares readOnlyHint True (fail-closed)."""
    return annotations.get("readOnlyHint") is not True


class _ToolResultError(Exception):
    """A tool returned something that can't be normalized into a result.

    Raised inside the savepoint so a malformed return value rolls back any DB
    writes the tool made, and classified separately from the tool's own
    ``ValueError``/``TypeError`` (which is an invalid-params client error).
    """


# Protocol versions this server can speak. ``initialize`` echoes the client's
# requested version when supported, else falls back to the preferred one.
#
# 2025-11-25 is preferred; 2025-06-18 is kept for clients that have not
# adopted it yet. The earlier revisions (2025-03-26, 2024-11-05) mandate
# JSON-RPC request batching, which this server does not implement (a batch
# array is answered with a single -32600) -- advertising them would claim a
# capability we don't meet, so a client asking for one is negotiated down on
# initialize. Of the 2025-11-25 changes, the ones that apply to this server
# are implemented: SEP-1303 validation-error semantics (_reject_invalid_input),
# Origin validation (MCPDispatcher.pre_dispatch) and serverInfo.description;
# the rest (tasks, elicitation, sampling, SSE polling) concern optional or
# client-side features this stateless request/response server does not use.
PREFERRED_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18")

SERVER_NAME = "much-mcp-server"
# serverInfo.description (2025-11-25): human-readable context clients may show.
SERVER_DESCRIPTION = (
    "Odoo MCP server: read and write Odoo records with per-model access "
    "control, audit logging and rate limiting."
)


class MCPController(http.Controller):
    """JSON-RPC 2.0 handler backing the native MCP ``/mcp`` endpoint."""

    # Spec-defined client->server notifications exempt from the rate limiter
    # (see _counts_against_rate_limit).
    _RATE_LIMIT_EXEMPT_NOTIFICATIONS = frozenset(
        {
            "notifications/initialized",
            "notifications/cancelled",
            "notifications/progress",
            "notifications/roots/list_changed",
        }
    )

    # /mcp is the canonical path; /mcp/rpc is a legacy alias of the same
    # endpoint (its audience is likewise still accepted, see
    # oauth_server.accepted_resource_urls).
    @mcp_route(["/mcp", "/mcp/rpc"], methods=["POST"])
    def handle_rpc(self, **envelope):
        """Validate the JSON-RPC envelope and dispatch on ``method``.

        Returns a JSON-RPC response dict (the dispatcher serialises it) or, for
        notifications, an empty HTTP 202 ``Response``.
        """
        # The parameter is not named ``kwargs``: Odoo's ``_()`` reads a local of
        # that name as a dict holding a ``context``, and this one is the
        # client's envelope (``"context": "x"`` would crash every message).
        request_id = envelope.get("id")
        if not jsonrpc.is_valid_id(request_id):
            # Never echo an unusable id back (parse_request refuses it).
            request_id = None

        # Coarse global gate -- per-model ACLs are enforced once tools exist.
        # HTTP 503 like the REST door; the JSON-RPC body carries the reason.
        if not utils.is_mcp_enabled():
            body = jsonrpc.make_error(
                request_id,
                jsonrpc.SERVER_ERROR,
                _("MCP server is disabled globally."),
            )
            return request.make_json_response(body, status=503)

        try:
            method, params, request_id = jsonrpc.parse_request(envelope)
        except jsonrpc.JSONRPCError as err:
            return jsonrpc.make_error(request_id, err.code, err.message, err.data)

        # Per-request rate limiting (after auth, before dispatch).
        rate_limited = self._enforce_rate_limit(method, request_id)
        if rate_limited is not None:
            return rate_limited

        # A message without an ``id`` is a notification (JSON-RPC 2.0): it gets
        # no response. MCP requests MUST carry an id, so a request method sent
        # without one is acknowledged but never run -- a side effect whose
        # outcome nobody can receive is unsafe. Counted by the limiter above
        # like any other non-exempt message.
        if "id" not in envelope and not method.startswith("notifications/"):
            _logger.info("MCP request %r without an id ignored", method)
            return self._notification_ack(params)

        handler = self._METHOD_HANDLERS.get(method)
        try:
            if handler is not None:
                result = handler(self, params)
            elif method.startswith("notifications/"):
                # Tolerate any other client->server notification: no response body.
                result = self._notification_ack(params)
            else:
                return jsonrpc.make_error(
                    request_id,
                    jsonrpc.METHOD_NOT_FOUND,
                    _("Method not found: %(method)s", method=method),
                )
        except jsonrpc.JSONRPCError as err:
            return jsonrpc.make_error(request_id, err.code, err.message, err.data)

        # Notifications return a bare 202 Response; everything else a JSON-RPC
        # result the dispatcher will serialise.
        if isinstance(result, Response):
            return result
        return jsonrpc.make_response(request_id, result)

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------
    def _enforce_rate_limit(self, method, request_id):
        """Enforce the per-request rate limit on ``/mcp``.

        Called after auth and before dispatch. Mirrors the legacy ``@rate_limit``
        decorator's logic -- check-then-record, keyed on the API-key owner -- but
        invokes the :mod:`rate_limiting` primitives directly, because that
        decorator needs ``kwargs['user']`` + ``type='http'`` plumbing that is
        absent under our ``type='mcp'`` dispatcher.

        PER-WORKER LIMITATION: the limiter keeps its counters in a
        per-process in-memory dict, so the cap is enforced PER WORKER -- with N
        Odoo HTTP workers the effective ceiling is ``N x request_limit``. Move the
        counters to a shared store (Redis / a DB table) if a true global cap is
        ever required.

        RETRY DOUBLE-COUNT: the hit is recorded here, before dispatch, and a
        tool that raises OperationalError is re-raised for
        ``service.model.retrying`` (re-runs this whole handler, up to 5x) -- so a
        serialization-conflicted write records one hit per attempt. Left as-is:
        the miscount is self-penalizing (a caller's own retried write burns its
        own quota), bounded, and in the safe (stricter) direction, so it cannot
        be used to exceed the cap. The audit path guards this via
        ``concurrency_retry``; the limiter accepts it as best-effort.

        :return: an HTTP 429 JSON-RPC :class:`~odoo.http.Response` (carrying a
            ``Retry-After`` header) when the owner is over the limit, else
            ``None`` to let the request proceed.
        """
        if not self._counts_against_rate_limit(method):
            return None
        if not rate_limiting.is_rate_limiting_enabled():
            return None

        uid = request.env.uid
        # Namespace the rate-limit bucket by database (cross-tenant collision).
        dbname = request.env.cr.dbname
        if not rate_limiting.check_rate_limit(uid, dbname):
            # Dedupe the over-limit audit (see _rate_limit_audit_limiter) so a
            # flood cannot amplify into one committed-cursor write per rejected
            # request; the 429 below is always returned regardless.
            if not _rate_limit_audit_limiter.is_limited((dbname, uid), 1):
                self._audit_rate_limit_exceeded(uid)
            retry_after = rate_limiting.retry_after_seconds()
            body = jsonrpc.make_error(
                request_id,
                jsonrpc.SERVER_ERROR,
                _("Rate limit exceeded; too many requests."),
            )
            return request.make_json_response(
                body, headers=[("Retry-After", str(retry_after))], status=429
            )

        rate_limiting.record_api_request(uid, dbname)
        return None

    def _counts_against_rate_limit(self, method):
        """Whether ``method`` consumes a rate-limit slot.

        Every request/response method that does real work counts. ``ping`` (a
        liveness heartbeat, analogous to the un-limited ``/mcp/health`` probe)
        and the spec-defined client->server notifications (fire-and-forget acks
        returning HTTP 202) are exempt -- counting routine keepalive traffic
        would needlessly exhaust a client's budget. An unrecognised
        ``notifications/*`` name is NOT exempt, so it cannot be used to flood the
        endpoint under the limiter's radar.
        """
        return method != "ping" and method not in self._RATE_LIMIT_EXEMPT_NOTIFICATIONS

    def _audit_on_independent_cursor(self, uid, write_log, failure_message, *args):
        """Persist an ``mcp.log`` row that survives a request-transaction rollback.

        Thin wrapper over :func:`audit.write_audit_row` (which owns the shared
        test/prod cursor discipline) adding the per-IP throttle: past the window
        budget for that IP (see ``_audit_write_limiter``) the write is skipped, so
        a flood of rejected / denied / failed calls cannot amplify into one fsync
        per request. The caller's response path is unaffected; under normal
        (non-flood) load the budget is never reached, so legitimate audit rows are
        never dropped. ``write_log`` performs the single log call that differs
        between callers; any audit-write failure is logged and swallowed.
        """
        if _audit_write_limiter.is_limited(
            (request.db, request.httprequest.remote_addr), _AUDIT_WRITE_MAX
        ):
            return
        audit.write_audit_row(uid, write_log, failure_message, *args)

    def _audit_rate_limit_exceeded(self, uid):
        """Audit an over-limit (HTTP 429) event on an INDEPENDENT cursor.

        Same discipline as :meth:`_audit_tool_call` (see
        :meth:`_audit_on_independent_cursor`): the ``rate_limit`` audit row
        persists even though the request returns a 429. Uses
        ``mcp.log.log_rate_limit_exceeded`` -- the same call the legacy REST
        decorator makes -- so over-limit events are auditable
        (``event_type='rate_limit'``).
        """
        ip_address = request.httprequest.remote_addr
        self._audit_on_independent_cursor(
            uid,
            lambda mcp_log: mcp_log.log_rate_limit_exceeded(
                user_id=uid,
                endpoint="/mcp",
                ip_address=ip_address,
            ),
            "MCP rate-limit audit logging failed",
        )

    def _initialize(self, params):
        """Handle ``initialize``: negotiate the protocol version + advertise."""
        requested = params.get("protocolVersion")
        if requested in SUPPORTED_PROTOCOL_VERSIONS:
            protocol_version = requested
        else:
            protocol_version = PREFERRED_PROTOCOL_VERSION
        result = {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {}, "resources": {}},
            "serverInfo": {
                "name": SERVER_NAME,
                "version": utils.get_mcp_server_version(),
                "description": SERVER_DESCRIPTION,
            },
        }
        # Personalized user/timezone/company context that spec-compliant clients
        # inject into the model's context automatically. Best-effort: a glitch
        # building it must never break the handshake, so omit the key on failure.
        try:
            result["instructions"] = utils.build_user_context(request.env)
        except Exception:
            _logger.exception("Failed to build MCP initialize instructions")
        return result

    def _ping(self, params):
        """Handle ``ping``: an empty result acknowledges liveness."""
        return {}

    @staticmethod
    def _hidden_from_list(annotations, write_allowed):
        """Whether a tool is omitted from ``tools/list`` for the current session.

        Scope gate: a read-only OAuth session (scope mcp:read) does not even see
        write tools, so the model never tries to call them. Keyed on the
        readOnlyHint annotation and fail-closed (absent / not exactly True ==
        write-requiring), mirroring the ``_tools_call`` gate. Shared by the
        builtin and custom-tool loops below.
        """
        return not write_allowed and _requires_write(annotations)

    def _tools_list(self, params):
        """Handle ``tools/list``: advertise the registered MCP tools.

        Built from the mixin tool index; ``input_schema`` is exposed under the
        MCP-spec camelCase key ``inputSchema``.
        """
        mixin = request.env["mcp.mixin"]
        index = mixin._get_mcp_tools()
        write_allowed = _write_allowed()
        tools = []
        for name, meta in index.items():
            if self._hidden_from_list(meta["annotations"], write_allowed):
                continue
            tool = {
                "name": name,
                "description": meta["description"],
                # Fill live limit-tuning values into the advertised schema.
                "inputSchema": mixin._mcp_live_input_schema(meta["input_schema"]),
            }
            if meta.get("title"):
                tool["title"] = meta["title"]
            if meta.get("annotations"):
                tool["annotations"] = meta["annotations"]
            tools.append(tool)
        # Append admin-defined custom tools the caller may run. Live-searched
        # every request (no cache) so edits take effect immediately. The same
        # read-only omission as builtins applies: a read-only session does not see
        # non-read-only custom tools (predicate keyed on readOnlyHint, fail-closed).
        # _visible_tools() already applied the non-su _user_can_run() gate; iterate
        # the filtered result under sudo so the per-row DISPLAY reads below
        # (is_readonly/input_schema/name/description -- never the action code)
        # succeed for a caller lacking mcp.custom.tool read (e.g. a portal user
        # authorized to run a tool via the action's groups_id).
        visible = request.env["mcp.custom.tool"]._visible_tools()
        for custom in visible.sudo():  # sudo: non-sensitive display reads only
            annotations = {
                "readOnlyHint": custom.is_readonly,
                "destructiveHint": not custom.is_readonly,
            }
            if self._hidden_from_list(annotations, write_allowed):
                continue
            input_schema = custom._parsed_input_schema()
            if input_schema is None:
                # A corrupted row (the @api.constrains normally prevents saving
                # invalid JSON, but a distributable module must tolerate one)
                # must not break tools/list for every user who can see this
                # tool: log it and skip the single bad row.
                _logger.warning(
                    "Skipping custom tool '%s' in tools/list: malformed "
                    "input_schema.",
                    custom.name,
                )
                continue
            tools.append(
                {
                    "name": custom.name,
                    "title": custom.name,
                    "description": custom.description,
                    "inputSchema": input_schema,
                    "annotations": annotations,
                }
            )
        return {"tools": tools}

    @staticmethod
    def _missing_required_args(input_schema, arguments):
        """Required argument names declared by ``input_schema`` but absent from
        ``arguments``.

        Coerces ``required`` defensively before iterating: the @api.constrains
        keeps it a list of strings, but a distributable module must tolerate a
        corrupted row (e.g. one written via raw SQL, bypassing the constraint). A
        non-list ``required`` -> ``[]`` and non-string keys are skipped, so neither
        crashes the required-arg check -- degrading like
        ``_parsed_input_schema``/``_lookup_custom_tool`` do.
        """
        required = input_schema.get("required", [])
        if not isinstance(required, list):
            required = []
        return [
            key for key in required if isinstance(key, str) and key not in arguments
        ]

    @staticmethod
    def _unknown_args(input_schema, arguments):
        """Argument names not declared by an ``additionalProperties: false`` schema.

        Every builtin tool declares ``additionalProperties: false``; honouring it
        here turns a stray key into a clean validation error instead of the
        Python ``TypeError`` from ``method(**arguments)``, whose message names
        the implementing class and signature. A schema that allows extra
        properties (custom tools by default) keeps accepting them. Defensive
        like :meth:`_missing_required_args`: a malformed ``properties`` yields
        no rejection.
        """
        if input_schema.get("additionalProperties") is not False:
            return []
        properties = input_schema.get("properties")
        if not isinstance(properties, dict):
            return []
        return [key for key in arguments if key not in properties]

    @classmethod
    def _arguments_error(cls, input_schema, arguments, builtin):
        """Message for the first argument check ``arguments`` fails, or None.

        In order: missing required arguments, unknown arguments, then the
        first schema violation (:meth:`_invalid_argument`).
        """
        missing = cls._missing_required_args(input_schema, arguments)
        if missing:
            return _(
                "Missing required argument(s): %(fields)s", fields=", ".join(missing)
            )
        unknown = cls._unknown_args(input_schema, arguments)
        if unknown:
            return _(
                "Unknown argument(s): %(fields)s",
                fields=", ".join(sorted(map(str, unknown))),
            )
        return cls._invalid_argument(input_schema, arguments, builtin)

    @staticmethod
    def _invalid_argument(input_schema, arguments, builtin):
        """Message for the first argument that breaks ``input_schema``, or None.

        Checked per declared top-level property (see ``_argument_violation``),
        in declaration order, for builtin and custom tools alike -- except the
        builtin arguments a tool checks itself (``_TOOL_CHECKED_ARGS``). An
        explicit ``null`` for an optional argument counts as omitted: every
        builtin treats it so, and clients commonly send it. Defensive like
        :meth:`_missing_required_args`: a malformed ``properties`` or
        ``required`` yields no rejection.
        """
        properties = input_schema.get("properties")
        if not isinstance(properties, dict):
            return None
        required = input_schema.get("required")
        if not isinstance(required, list):
            required = []
        for key, schema in properties.items():
            if key not in arguments or not isinstance(schema, dict):
                continue
            if builtin and key in _TOOL_CHECKED_ARGS:
                continue
            value = arguments[key]
            if value is None and key not in required:
                continue
            message = _argument_violation(key, schema, value)
            if message:
                return message
            arguments[key] = _canonical_enum(value, schema.get("enum"))
        return None

    def _tools_call(self, params):
        """Handle ``tools/call``: invoke a registered tool and return its result.

        Tool execution errors AND input-validation failures (non-object
        ``arguments``, missing required or unknown arguments, arguments breaking
        the tool's input schema) are returned as
        an MCP tool result with ``isError: true`` -- the latter per SEP-1303 (protocol
        revision 2025-11-25), so clients feed the message back to the model for
        self-correction. Only calls that cannot be attributed to a tool --
        unknown tool, unusable ``name`` -- are JSON-RPC ``-32602`` errors.
        """
        name = params.get("name")
        # ``name`` is client-supplied JSON; guard it before it reaches
        # index.get(name) (an unhashable list/dict would raise TypeError OUTSIDE
        # any try -> unaudited -32603). Not SEP-1303 material: without a usable
        # name there is no tool to attribute an isError result to, so
        # _audit_rejected_call logs an E400 row and raises a clean -32602.
        if not isinstance(name, str) or not name:
            self._audit_rejected_call(
                name, _("Invalid params: 'name' must be a non-empty string.")
            )

        index = request.env["mcp.mixin"]._get_mcp_tools()
        meta = index.get(name)
        custom_tool = None
        if meta is None:
            # Not a builtin -> resolve an active custom tool (builtins always win
            # at dispatch). Raises -32602 unknown-tool for a truly unknown name; an
            # existing-but-unauthorized tool is refused later by the _user_can_run()
            # gate as an access-denied isError.
            meta, custom_tool = self._lookup_custom_tool(name)

        # Validated AFTER the tool lookup so an unknown tool keeps its -32602
        # protocol error; for a known tool, a non-object ``arguments`` is an
        # input-validation failure -> isError tool result (SEP-1303).
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return self._reject_invalid_input(
                name, _("Invalid arguments: 'arguments' must be an object.")
            )

        # Audit context captured for the ``finally`` write. ``model`` is
        # only present on model-scoped tools (e.g. ``list_models`` has none).
        model_name = arguments.get("model")
        if not isinstance(model_name, str):
            model_name = self._attachment_audit_model(name, arguments)
        operation = meta.get("operation") or name
        # ``call_model_method`` invokes an arbitrary business method; record the
        # real method name as the operation (parity with the legacy XML-RPC
        # proxy, which logs the invoked method) rather than the literal tool name.
        if name == "call_model_method":
            method_arg = arguments.get("method")
            if isinstance(method_arg, str) and method_arg.strip():
                operation = method_arg.strip()
        record_ids = self._extract_record_ids(arguments)
        # Audit metadata (never raw field values): the argument KEYS the caller
        # sent, plus -- filled after a successful run -- a one-line result-size
        # summary. Both land on the model_access row so the log shows what was
        # asked and how much came back without storing business data.
        request_summary = self._audit_request_summary(name, arguments)
        response_summary = None
        start_time = datetime.now()
        error_detail = None
        # Set when the required-arg check already logged its own E400 row, so
        # the finally does not write a second (success) audit row.
        rejected = False
        # Set when a concurrency conflict (serialization failure / deadlock) is
        # re-raised for Odoo's service.model.retrying: the finally must then skip
        # the success audit -- the aborted request cursor cannot be written, and
        # the retried attempt writes the real row.
        concurrency_retry = False

        method = (
            None
            if custom_tool is not None
            else getattr(request.env["mcp.mixin"], meta["method_name"])
        )
        try:
            # Authorization gate: for a custom tool, evaluate the caller's
            # _user_can_run() FIRST -- before the scope gate, the required-arg
            # check or any other schema-dependent work -- so an authenticated-but-
            # unauthorized caller who guesses a HIDDEN tool's name cannot learn its
            # required-argument field names. Placed inside this try so the
            # denial is audited by the finally (like the read-only denial).
            # _run_tool re-checks _user_can_run() inside the savepoint as defense
            # in depth; this only moves the same denial earlier.
            if custom_tool is not None and not custom_tool._user_can_run():
                # Authorization denial, not an internal error: audit as a
                # distinct permission_denied event and set `rejected` so the
                # finally does not also write an E500 row (one row per call).
                rejected = True
                return self._deny(
                    name,
                    operation,
                    model_name,
                    _(
                        "You are not allowed to run the '%(name)s' tool.",
                        name=name,
                    ),
                )

            # Scope gate: a read-only OAuth session (scope mcp:read) may only
            # invoke read-only tools. Keyed on the tool's readOnlyHint annotation
            # -- NOT its operation (which is None for several tools) -- and
            # fail-closed: a tool whose readOnlyHint is absent or not exactly True
            # is treated as write-requiring. Placed BEFORE the required-arg check
            # (and after the authorization gate) so a read-only caller entitled to
            # a write tool cannot learn its required-argument field names by
            # omitting them -- the read-only refusal must precede the validation
            # isError that names the missing fields. Reads only meta's annotations
            # + _write_allowed(), so it does not depend on the arg check. Inside
            # this try so the finally still writes the audit row.
            if _requires_write(meta["annotations"]) and not _write_allowed():
                # Authorization denial (read-only scope), not an internal error:
                # audit as permission_denied and set `rejected` so the finally
                # does not also write an E500 row (one row per call).
                rejected = True
                return self._deny(
                    name,
                    operation,
                    model_name,
                    _(
                        "This connection was authorized read-only (scope "
                        "mcp:read); the '%(name)s' tool requires write access.",
                        name=name,
                    ),
                )

            # Argument checks -- required, unknown, then schema violations
            # (SEP-1303: refused as an isError tool result the model can
            # self-correct from, not a -32602). Kept inside the audited try,
            # AFTER both the authorization gate and the scope gate so that
            # neither an unauthorized caller nor a read-only-scoped caller can
            # learn the argument field names by omitting them.
            # _reject_invalid_input logs an E400 row, and `rejected` tells the
            # finally not to write a second row.
            invalid = self._arguments_error(
                meta["input_schema"], arguments, builtin=custom_tool is None
            )
            if invalid:
                rejected = True
                return self._reject_invalid_input(name, invalid)
            try:
                # Run the tool AND normalize its result inside one savepoint, so
                # a failure in either -- the tool raising, or the tool returning
                # something malformed -- rolls back any partial DB writes (and
                # invalidates the dirty ORM cache) before we build the sanitized
                # isError result. Otherwise the service layer would flush+commit
                # work the client is told failed. A malformed return is re-raised
                # as _ToolResultError so it too rolls back.
                with request.env.cr.savepoint():
                    if custom_tool is not None:
                        # Run the wrapped action as the calling user (its own
                        # _user_can_run gate re-checked inside) and serialize its
                        # result to text with the module's json default (str).
                        tool_output = custom_tool._run_tool(arguments)
                        text = json.dumps(
                            (
                                tool_output
                                if tool_output is not None
                                else _("The action completed successfully.")
                            ),
                            ensure_ascii=False,
                            default=str,
                        )
                        result = {
                            "content": [{"type": "text", "text": text}],
                            "isError": False,
                        }
                    else:
                        tool_output = method(**arguments)
                        try:
                            result = dict(tool_output)
                            result.setdefault("content", [])
                            result["isError"] = False
                        except Exception as err:  # noqa: BLE001 - re-raised below
                            raise _ToolResultError from err
                # Result built OK inside the savepoint: capture a size summary for
                # the audit row (per-tool total/groups, else touched-record count).
                # Batch writes take their touched ids from the result -- the
                # argument list can differ after dedupe, and created ids do not
                # exist before the call -- so both derive from the same source.
                record_ids = self._batch_touched_ids(name, result, record_ids)
                response_summary = self._audit_response_summary(
                    name, result, record_ids
                )
            except (ValueError, TypeError) as err:
                # A tool raising ValueError/TypeError -- either a wrong-typed
                # argument (record_id="abc" -> int("abc")) or an ORM coercion
                # error (a bad Selection value / Many2one id in create/update) --
                # is a tool-execution failure, surfaced as an isError result per
                # the MCP spec rather than a JSON-RPC protocol error (which some
                # clients treat as fatal instead of feeding back to the model).
                # The savepoint already rolled back any partial writes; the full
                # context is logged server-side and the client message sanitized.
                # sanitize_message (not sanitize_exception) keeps the useful
                # detail -- ValueError is not a SAFE_EXCEPTION, so the latter
                # would flatten it to a generic "Internal server error".
                _logger.warning(
                    "MCP tool '%s' raised %s",
                    name,
                    type(err).__name__,
                    exc_info=True,
                )
                error_detail = error_sanitizer.sanitize_message(str(err))
                return _tool_error(error_detail)
            except _ToolResultError as err:
                # The tool succeeded but returned something malformed; the
                # savepoint already rolled its writes back. Audited as an error,
                # kept distinct from the ValueError/TypeError branch so it is not
                # mistaken for an invalid-params (client) error.
                error_detail = error_sanitizer.sanitize_exception(
                    err.__cause__,
                    "MCP tool '%s' result post-processing failed" % name,
                )
                return _tool_error(error_detail)
            except OperationalError:
                # Serialization failure / deadlock: let it propagate so Odoo's
                # service.model.retrying (the MCP dispatcher runs inside that
                # loop) retries the whole request. Swallowing it below as a
                # generic isError would defeat the retry and return a spurious
                # internal error for a transient conflict. Mirrors the OAuth
                # token/register endpoints.
                concurrency_retry = True
                raise
            except IntegrityError as err:
                # A constraint failure at flush (NOT NULL / FK / unique) is a
                # data error the model can fix, not an internal fault: surface
                # Odoo's own field-naming message (see sanitize_integrity_error)
                # instead of the generic text. The savepoint already rolled the
                # writes back, so the cursor is usable for the translation.
                error_detail = error_sanitizer.sanitize_integrity_error(
                    request.env, err, "MCP tool '%s' hit a DB constraint" % name
                )
                return _tool_error(error_detail)
            except AccessError as err:
                # Authorization denial -- the MCP per-model / method gate
                # (mcp.mixin) or Odoo's own ACL / record rules -- not an internal
                # error. Audit it as a distinct permission_denied event with auth
                # attribution (mirroring the scope/authorization gates) instead
                # of letting it fall to the generic branch below, which would log
                # a misleading E500 with no attribution. ``rejected`` tells the
                # finally not to also write an E500 row -> exactly one
                # permission_denied row.
                rejected = True
                self._audit_permission_denied(name, operation, model_name)
                return _tool_error(error_sanitizer.sanitize_exception(err))
            except Exception as err:  # noqa: BLE001 - sanitized; traceback logged
                # Safe Odoo exceptions surface their (scrubbed) message; any
                # other error is logged in full server-side and returned as a
                # generic message so no internal detail reaches the client.
                error_detail = error_sanitizer.sanitize_exception(
                    err, "MCP tool '%s' failed" % name
                )
                return _tool_error(error_detail)
            return result
        finally:
            # Audit every tool call: success on the request cursor, errors on an
            # independent committed cursor (see _audit_tool_call). A rejected
            # required-arg call already logged its own E400 row and raised, so
            # skip here to avoid a spurious second (success) row; a concurrency
            # re-raise (retrying) skips too -- the request cursor is aborted and
            # the retried attempt writes the real row.
            if not rejected and not concurrency_retry:
                duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
                self._audit_tool_call(
                    tool_name=name,
                    model_name=model_name,
                    operation=operation,
                    error_detail=error_detail,
                    duration_ms=duration_ms,
                    record_ids=record_ids,
                    request_summary=request_summary,
                    response_summary=response_summary,
                )

    def _lookup_custom_tool(self, name):
        """Resolve a non-builtin tool ``name`` to a ``(meta, custom_tool)`` pair.

        Called from :meth:`_tools_call` when ``name`` is not a builtin tool
        (builtins always win at dispatch). Performs the sudo discovery search, the
        unknown-tool rejection, the non-su re-bind, the schema parse/degrade and
        the synthetic ``meta`` build -- the custom-tool half of ``tools/call``
        dispatch, extracted so the gate ordering (authorization -> scope ->
        arg-check) reads cleanly in the caller.

        The DISPLAY fields read here (input_schema/is_readonly/name) are
        non-sensitive -- never the action code -- so they are read off a SUDO
        record, letting a caller who lacks mcp.custom.tool read (e.g. a portal user
        authorized via the action's groups_id) reach the gate. The returned
        ``custom_tool`` is re-bound to the caller's NON-su env, so the downstream
        _user_can_run() gate and _run_tool evaluate the caller's own ACLs, never
        sudo's.
        """
        custom_su = (
            request.env["mcp.custom.tool"]
            .sudo()  # sudo: discovery + non-sensitive display reads only
            .search([("name", "=", name), ("active", "=", True)], limit=1)
        )
        if not custom_su:
            # Intentional 403-vs-404 split (see test_user_not_in_group_hidden_and_
            # denied): a truly unknown name keeps the -32602 unknown-tool error,
            # while a real-but-unauthorized tool is refused post-auth by the
            # _user_can_run() gate in _tools_call as an access-denied isError. This
            # is an existence oracle for NAMES ONLY, and only to an already-
            # authenticated caller -- an accepted tradeoff.
            self._audit_rejected_call(name, _("Unknown tool: %(name)s", name=name))
        # Re-bind to the caller's non-su env for the authorization gate and
        # execution: _user_can_run() / _run_tool must evaluate the caller's ACLs,
        # never sudo's.
        custom_tool = custom_su.with_env(request.env)
        # Guard the schema parse: the @api.constrains normally keeps this valid
        # JSON, but a distributable module must tolerate a corrupted row rather
        # than raise an unaudited JSONDecodeError here (outside the audited try).
        # Degrade to an empty schema -- the required-arg check then sees nothing
        # required -- and let the audited flow run.
        custom_schema = custom_su._parsed_input_schema()
        if custom_schema is None:
            _logger.warning(
                "Custom tool '%s' has a malformed input_schema; treating "
                "it as empty for this call.",
                custom_su.name,
            )
            custom_schema = {}
        # Synthetic meta so the shared required-arg check, scope gate and audit
        # path treat the custom tool exactly like a builtin. No "operation" key: it
        # falls through to `name` in _tools_call so the audit row records the real
        # tool name (distinguishable per custom tool). Built from the sudo record --
        # display fields only, never the action code.
        meta = {
            "input_schema": custom_schema,
            "annotations": {"readOnlyHint": custom_su.is_readonly},
        }
        return meta, custom_tool

    @staticmethod
    def _parse_uri_argument(arguments):
        """Parsed ``uri`` argument of an attachment tool call, or ``None``.

        Returns ``("attachment", id)`` / ``("record", RecordFieldURI)``; a
        missing or malformed URI yields ``None`` (the tool itself rejects it).
        """
        uri = arguments.get("uri")
        if not isinstance(uri, str):
            return None
        try:
            return "record", parse_field_uri(uri)
        except URIParseError:
            try:
                return "attachment", parse_attachment_uri(uri)
            except URIParseError:
                return None

    @classmethod
    def _attachment_audit_model(cls, name, arguments):
        """Audit ``model`` for an attachment tool call that names no model.

        ``read_attachment`` targets are given as a URI or an attachment id:
        a ``odoo://record/...`` URI is attributed to its own model, everything
        else to ``ir.attachment``. Other tools keep ``None``.
        """
        if name not in _ATTACHMENT_TOOLS:
            return None
        parsed = cls._parse_uri_argument(arguments)
        if parsed and parsed[0] == "record":
            return parsed[1].model
        return "ir.attachment"

    @classmethod
    def _extract_record_ids(cls, arguments):
        """Record IDs a tool call targets, for the audit row.

        Mirrors the legacy proxy's ``_extract_record_ids``: pull the ids from the
        tool arguments (a ``record_ids`` / ``ids`` list, or a single
        ``record_id``). Attachment tools name their target as ``attachment_id``
        or a ``uri`` instead. Tools that target no specific records (search /
        aggregate / create) yield ``None``.
        """
        for key in ("record_ids", "ids"):
            value = arguments.get(key)
            if isinstance(value, (list, tuple)) and value:
                return list(value)
        for key in ("record_id", "attachment_id"):
            value = arguments.get(key)
            if value is not None:
                return [value]
        parsed = cls._parse_uri_argument(arguments)
        if parsed is None:
            return None
        kind, ref = parsed
        return [ref.record_id if kind == "record" else ref]

    def _audit_tool_call(
        self,
        tool_name,
        model_name,
        operation,
        error_detail,
        duration_ms,
        record_ids=None,
        request_summary=None,
        response_summary=None,
    ):
        """Write one ``mcp.log`` audit entry for a tool call / resource read.

        Success rows go on the **request cursor**: the framework already commits
        it at end of request (``odoo.service.model.retrying``), and a tool error
        here is savepoint-isolated and caught, so the request transaction is not
        discarded -- opening a second pooled connection and issuing an explicit
        ``COMMIT`` (fsync) per successful call would be pure overhead. The
        request cursor is committed by the framework at end of request; the
        write is guarded below so an audit hiccup never breaks the response.

        Error rows go on a fresh, explicitly-committed **independent cursor**
        (see :meth:`_audit_on_independent_cursor`) so the row still persists in
        the defensive case where the request transaction is later discarded.
        """
        uid = request.env.uid
        ip_address = request.httprequest.remote_addr

        if error_detail:
            self._audit_on_independent_cursor(
                uid,
                lambda mcp_log: mcp_log.log_error(
                    error_message=error_detail,
                    error_code="E500",
                    endpoint="/mcp",
                    model_name=model_name,
                    operation=operation,
                    user_id=uid,
                    ip_address=ip_address,
                ),
                "MCP audit logging failed for tool '%s'",
                tool_name,
            )
        else:
            # Enrich the success row with the tool name, metadata-only payload
            # summaries and the front-door attribution (auth method + OAuth
            # client/scope), on the request cursor. Guarded: an audit-write
            # hiccup must never turn a successful tool call into an error -- the
            # finally would otherwise propagate it as the response.
            try:
                mcp_log = request.env["mcp.log"].sudo()  # sudo: audit row, any user
                mcp_log.log_model_access(
                    model_name=model_name,
                    operation=operation,
                    user_id=uid,
                    record_ids=record_ids,
                    endpoint="/mcp",
                    http_method="POST",
                    duration_ms=duration_ms,
                    ip_address=ip_address,
                    tool_name=tool_name,
                    request_data=request_summary,
                    response_data=response_summary,
                    **self._audit_auth_context(),
                )
            except Exception:  # noqa: BLE001 - auditing must never break response
                _logger.exception("MCP audit logging failed for tool '%s'", tool_name)

    @staticmethod
    def _audit_auth_context():
        """Front-door attribution for an operation audit row.

        ir.http stashes these on ``request`` during the bearer handshake:
        ``_mcp_auth_method`` ('api_key' / 'oauth'), and for OAuth the client id
        and granted scope. ``user_agent`` identifies the MCP client;
        ``session_id`` is the client-sent ``Mcp-Session-Id`` when present -- this
        server is stateless and issues none, so it is usually empty.
        """
        scope = getattr(request, "_mcp_oauth_scope", None)
        headers = request.httprequest.headers
        return {
            "auth_method": getattr(request, "_mcp_auth_method", None),
            "oauth_client_id": getattr(request, "_mcp_oauth_client_id", None),
            "oauth_scope": scope or None,  # "" (legacy token) -> None
            "user_agent": headers.get("User-Agent"),
            "session_id": headers.get("Mcp-Session-Id"),
        }

    @staticmethod
    def _audit_request_summary(name, arguments):
        """Argument KEYS of a tool call, for the audit row (never values).

        A batch write tool additionally carries its requested batch size --
        metadata only (a count). A non-list argument yields no size; the tool
        itself rejects it.
        """
        summary = ", ".join(sorted(arguments)) if arguments else None
        if name not in _BATCH_WRITE_TOOLS:
            return summary
        entries = None
        for key in ("records", "updates", "record_ids"):
            if isinstance(arguments.get(key), list):
                entries = arguments[key]
                break
        if not isinstance(entries, list):
            return summary
        size = "batch_size=%s" % len(entries)
        return f"{summary}, {size}" if summary else size

    @staticmethod
    def _batch_touched_ids(name, result, record_ids):
        """Touched record ids for the audit row of a successful tool call.

        Non-batch tools keep the argument-derived ``record_ids``. A batch
        write tool takes them from the tool result instead. Runs after the
        savepoint released, in the must-not-raise audit position:
        isinstance-guarded throughout, so a malformed ``structuredContent``
        degrades to ``None`` (no ids on the row) rather than an error response
        for writes that are already committed.
        """
        if name not in _BATCH_WRITE_TOOLS:
            return record_ids
        structured = None
        if isinstance(result, dict):
            structured = result.get("structuredContent")
        rows = structured.get("records") if isinstance(structured, dict) else None
        if not isinstance(rows, list):
            return None
        ids = [
            row["id"]
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("id"), int)
        ]
        return ids or None

    @staticmethod
    def _audit_response_summary(name, result, record_ids):
        """One-line result-size summary for a model_access audit row.

        "Size" and its structuredContent key differ per tool: ``search_records``
        reports the total matching count (``total``) and ``aggregate_records``
        the number of ``groups``. Other read tools (``get_fields`` stashes a
        ``total`` that counts *fields*, ``list_models``) are not record-oriented,
        so they carry no summary here. Calls targeting explicit records
        (``get_record`` / update / delete) fall back to the id count; the batch
        write tools report their ``count``. ``None`` when none applies.
        Metadata only -- never row values.
        """
        structured = None
        if isinstance(result, dict):
            structured = result.get("structuredContent")
        if isinstance(structured, dict):
            if name == "search_records" and "total" in structured:
                return "%s records" % structured["total"]
            if name == "aggregate_records" and "groups" in structured:
                return "%s groups" % len(structured["groups"])
            if name in _BATCH_WRITE_TOOLS and "count" in structured:
                return "%s records" % structured["count"]
        if record_ids:
            return "%s record(s)" % len(record_ids)
        return None

    def _audit_e400(self, tool_name, message):
        """Write the ``E400`` audit row for a refused call (independent cursor).

        Shared by :meth:`_audit_rejected_call` (protocol-level rejections) and
        :meth:`_reject_invalid_input` (SEP-1303 validation failures) so both
        leave the same ``mcp.log`` trail.
        """
        uid = request.env.uid
        # ``tool_name`` may be a non-string (a client can send any JSON for
        # params.name); the audit row's ``operation`` is a Char, so coerce it
        # rather than let the write fail (which _audit_on_independent_cursor would
        # swallow, leaving no row).
        operation = tool_name if isinstance(tool_name, str) else str(tool_name)
        self._audit_on_independent_cursor(
            uid,
            lambda mcp_log: mcp_log.log_error(
                error_message=message,
                error_code="E400",
                endpoint="/mcp",
                operation=operation,
                user_id=uid,
                ip_address=request.httprequest.remote_addr,
            ),
            "MCP audit logging failed for rejected call '%s'",
            operation,
        )

    def _audit_rejected_call(self, tool_name, message):
        """Audit a pre-dispatch rejection, then raise it as invalid-params.

        Unknown-tool / bad-``name`` / missing-``uri`` calls are refused
        before the execution+audit block, so without this an authenticated
        client probing tool names or firing malformed calls would leave no
        ``mcp.log`` trail (the legacy XML-RPC proxy logs its equivalent). Audited
        as an ``E400`` error on an independent cursor; never returns.
        """
        self._audit_e400(tool_name, message)
        raise jsonrpc.JSONRPCError(jsonrpc.INVALID_PARAMS, message)

    def _reject_invalid_input(self, name, message):
        """Audit an input-validation failure and return the isError result.

        SEP-1303 (protocol revision 2025-11-25): tool-input validation
        failures -- a non-object ``arguments`` payload, missing, unknown or
        schema-breaking arguments -- are tool execution errors
        (``isError: true``), not JSON-RPC protocol errors, so clients feed the
        message back to the model for self-correction instead of treating the
        call as fatal.
        Audited as the same ``E400`` row as a protocol-level rejection.
        """
        self._audit_e400(name, message)
        return _tool_error(message)

    def _deny(self, name, operation, model_name, message):
        """Audit an authorization denial and return the isError tool result.

        Shared by the access-denied and read-only-scope gates, which both audit
        a ``permission_denied`` row then return an isError result with
        their own message. The caller keeps its own ``rejected = True`` (read by
        the shared ``finally``) so exactly one row is written per denied call.
        """
        self._audit_permission_denied(name, operation, model_name)
        return _tool_error(message)

    def _audit_permission_denied(self, name, operation, model_name):
        """Audit an authorization denial as a distinct ``permission_denied`` row.

        Three callers: the custom-tool authorization gate and the read-only-scope
        gate (which return an isError result without raising), and the tool-exec
        ``except AccessError`` branch (the per-model / method gate or Odoo's own
        ACL / record rules). Each sets the ``rejected`` flag so the shared
        ``finally`` skips its own audit -- exactly one row per denied call.
        Without this the denial would be logged as a generic ``E500`` error,
        indistinguishable from a real internal failure. Carries the front-door
        attribution (``_audit_auth_context``) and logs
        ``event_type='permission_denied'`` on an independent committed cursor.
        Mirrors the legacy proxy (controllers/api.py).
        """
        uid = request.env.uid
        ip_address = request.httprequest.remote_addr
        ctx = self._audit_auth_context()
        self._audit_on_independent_cursor(
            uid,
            lambda mcp_log: mcp_log.log_permission_denied(
                model_name=model_name,
                operation=operation,
                user_id=uid,
                endpoint="/mcp",
                tool_name=name,
                ip_address=ip_address,
                **ctx,
            ),
            "MCP permission-denied audit logging failed for tool '%s'",
            name,
        )

    # ------------------------------------------------------------------
    # Resources. Read-only by nature (they only materialise odoo:// URIs), so
    # the read/write scope gate does NOT apply here -- no _write_allowed()
    # check on resources/templates/list, resources/list or resources/read.
    # ------------------------------------------------------------------
    def _resources_templates_list(self, params):
        """Handle ``resources/templates/list``: advertise the native templates.

        We expose two ``odoo://`` URI templates rather than a static resource
        list -- a record binary/image field and an ``ir.attachment`` -- mirroring
        the ``list_resource_templates`` tool. MCP camelCase keys
        (``resourceTemplates`` / ``uriTemplate``).
        """
        return {
            "resourceTemplates": [
                {
                    "uriTemplate": "odoo://record/{model}/{id}/{field}",
                    "name": "Odoo record binary field",
                    "description": (
                        "Fetch a binary/image field from an Odoo record "
                        "(e.g. an image or stored document) instead of "
                        "inlining base64. The same URI can be passed to the "
                        "read_attachment tool."
                    ),
                },
                {
                    "uriTemplate": "odoo://attachment/{id}",
                    "name": "Odoo attachment",
                    "description": (
                        "Fetch an ir.attachment by ID. The same URI can be "
                        "passed to the read_attachment tool."
                    ),
                },
            ]
        }

    def _resources_list(self, params):
        """Handle ``resources/list``: empty -- we advertise templates, not a list."""
        return {"resources": []}

    def _resources_read(self, params):
        """Handle ``resources/read``: materialise an ``odoo://`` resource URI.

        Returns ``{"contents": [<entry>]}`` per the MCP spec, where the entry is
        ``{uri, mimeType, text}`` for textual content or ``{uri, mimeType, blob}``
        for binary. Unknown/forbidden/missing resources become sanitised
        JSON-RPC errors (no traceback leaked to the client).
        """
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri:
            self._audit_rejected_call(
                "resources/read", _("Invalid params: 'uri' is required.")
            )

        # Audit context captured for the ``finally`` write: resource reads
        # return raw record/attachment blobs, so they are auditable like a tool
        # call (model + operation 'read', user, success/error).
        start_time = datetime.now()
        error_detail = None
        # See _tools_call: skip the finally audit when re-raising a concurrency
        # conflict for service.model.retrying.
        concurrency_retry = False
        # An authorization denial is audited as permission_denied below (not by
        # the finally); this flag makes the finally skip its own audit.
        denied = False
        model_name = self._resource_audit_model(uri)
        try:
            try:
                entry = request.env["mcp.mixin"]._read_resource(uri)
            except AccessError as err:
                # Authorization denial (model not MCP-enabled / ACL / record
                # rule) -> audit as permission_denied, not a generic E500 that
                # looks like an internal fault (mirrors _tools_call). The client
                # still receives the same sanitised invalid-params error.
                denied = True
                self._audit_permission_denied("resources/read", "read", model_name)
                raise jsonrpc.JSONRPCError(
                    jsonrpc.INVALID_PARAMS, error_sanitizer.sanitize_exception(err)
                ) from err
            except error_sanitizer.SAFE_EXCEPTIONS as err:
                # User-safe (bad/missing/forbidden URI) -> invalid params, clean text.
                error_detail = error_sanitizer.sanitize_exception(err)
                raise jsonrpc.JSONRPCError(
                    jsonrpc.INVALID_PARAMS, error_detail
                ) from err
            except OperationalError:
                # Serialization failure / deadlock: propagate for
                # service.model.retrying (see _tools_call) instead of converting
                # it to a non-retryable JSON-RPC internal error.
                concurrency_retry = True
                raise
            except Exception as err:  # noqa: BLE001 - sanitized; traceback logged
                # Unexpected -> generic internal error; full traceback kept in log.
                error_detail = error_sanitizer.sanitize_exception(
                    err, "MCP resources/read failed for %s" % uri
                )
                raise jsonrpc.JSONRPCError(
                    jsonrpc.INTERNAL_ERROR, error_detail
                ) from None
            return {"contents": [entry]}
        finally:
            # Audit like a tool call: success on the request cursor, errors on an
            # independent committed cursor (see _audit_tool_call). A concurrency
            # re-raise (retrying) skips the audit -- the request cursor is aborted
            # and the retried attempt writes the real row. A denial already wrote
            # its own permission_denied row above, so skip the finally audit too.
            if not concurrency_retry and not denied:
                duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
                self._audit_tool_call(
                    tool_name="resources/read",
                    model_name=model_name,
                    operation="read",
                    error_detail=error_detail,
                    duration_ms=duration_ms,
                )

    @staticmethod
    def _resource_audit_model(uri):
        """Best-effort model name for a ``resources/read`` audit row.

        Lightweight prefix parse (not full URI validation -- the read path does
        that): ``odoo://record/{model}/...`` -> the record's model;
        ``odoo://attachment/{id}`` -> ``ir.attachment``; otherwise ``None``.
        """
        record_prefix = "odoo://record/"
        if uri.startswith(record_prefix):
            rest = uri[len(record_prefix) :].split("/", 1)
            return rest[0] or None
        if uri.startswith("odoo://attachment/"):
            return "ir.attachment"
        return None

    def _notification_ack(self, params):
        """Acknowledge a client notification with an empty HTTP 202 (no body)."""
        return Response("", status=202)

    _METHOD_HANDLERS = {
        "initialize": _initialize,
        "ping": _ping,
        "tools/list": _tools_list,
        "tools/call": _tools_call,
        "resources/templates/list": _resources_templates_list,
        "resources/list": _resources_list,
        "resources/read": _resources_read,
    }

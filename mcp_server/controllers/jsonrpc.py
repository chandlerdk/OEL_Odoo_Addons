"""Stdlib-only JSON-RPC 2.0 helpers for the native MCP endpoint.

The module hand-rolls the MCP protocol rather than depending on the ``mcp``
SDK, so these helpers build the response envelopes directly. They are pure
(no Odoo imports) and therefore unit-testable in isolation.
"""

JSONRPC_VERSION = "2.0"

# JSON-RPC 2.0 standard error codes (https://www.jsonrpc.org/specification).
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Implementation-defined server error (JSON-RPC reserves -32000..-32099 for
# server-defined errors). Used for the rate-limit (HTTP 429) JSON-RPC body.
SERVER_ERROR = -32000


class JSONRPCError(Exception):
    """A JSON-RPC error to render back to the client.

    Carries the protocol ``code``/``message`` (and optional ``data``) so the
    controller can turn it into an error response via :func:`make_error`.
    """

    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def make_response(request_id, result):
    """Build a JSON-RPC 2.0 success object."""
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def make_error(request_id, code, message, data=None):
    """Build a JSON-RPC 2.0 error object."""
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def is_valid_id(request_id):
    """Whether ``request_id`` is a usable MCP request id.

    MCP narrows JSON-RPC 2.0: a request id is a string or an integer and MUST
    NOT be null. ``bool`` is excluded explicitly (it is an ``int`` subclass).
    """
    if isinstance(request_id, bool):
        return False
    return isinstance(request_id, (str, int))


def parse_request(envelope):
    """Validate a JSON-RPC 2.0 request envelope.

    A message without an ``id`` member is a notification and passes; the
    caller decides how to answer it. An ``id`` that is present but not a
    string or integer (``null`` included) is refused, per MCP.

    :param envelope: the parsed request object.
    :return: ``(method, params, request_id)``.
    :raises JSONRPCError: code ``-32600`` when the envelope is not a valid
        JSON-RPC 2.0 request (wrong/absent ``jsonrpc`` version or ``method``,
        or an unusable ``id``).
    """
    if not isinstance(envelope, dict):
        raise JSONRPCError(INVALID_REQUEST, "Invalid Request")

    request_id = envelope.get("id")
    if envelope.get("jsonrpc") != JSONRPC_VERSION:
        raise JSONRPCError(INVALID_REQUEST, "Invalid Request")
    if "id" in envelope and not is_valid_id(request_id):
        raise JSONRPCError(INVALID_REQUEST, "Invalid Request")

    method = envelope.get("method")
    if not method or not isinstance(method, str):
        raise JSONRPCError(INVALID_REQUEST, "Invalid Request")

    params = envelope.get("params")
    if not isinstance(params, dict):
        params = {}
    return method, params, request_id

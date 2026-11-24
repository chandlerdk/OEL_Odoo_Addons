"""Response utilities for MCP Server."""

from datetime import datetime, timezone

from odoo.http import request

# Standard error codes
ERROR_CODES = {
    400: "E400",  # Bad Request
    401: "E401",  # Unauthorized
    403: "E403",  # Forbidden
    404: "E404",  # Not Found
    429: "E429",  # Too Many Requests
    500: "E500",  # Internal Server Error
    503: "E503",  # Service Unavailable
}


def get_timestamp():
    """Return the current timestamp (UTC) in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def success_response(data, meta=None):
    """Format a successful API response and always stamp ``meta.timestamp``.

    Shape: ``{"success": true, "data": {...}, "meta": {...}}``.
    """
    # Ensure data is a dict
    if data is None:
        data = {}
    elif not isinstance(data, dict):
        try:
            data = dict(data)
        except (TypeError, ValueError):
            # Wrap in a dictionary if conversion fails
            data = {"result": data}

    # Prepare metadata
    response_meta = {"timestamp": get_timestamp()}
    if meta and isinstance(meta, dict):
        response_meta.update(meta)

    payload = {"success": True, "data": data, "meta": response_meta}

    headers = {
        "Content-Type": "application/json",
    }

    return request.make_json_response(payload, headers=headers)


def error_response(message, code=None, status=400, meta=None):
    """Format an error API response and always stamp ``meta.timestamp``.

    Shape: ``{"success": false, "error": {"message", "code"}, "meta": {...}}``.
    ``code`` defaults to one derived from ``status`` when not given.
    """
    message = str(message) if message else "Unknown error"

    # Derive code from status if not provided
    if not code:
        code = ERROR_CODES.get(status, f"E{status}")

    # Prepare metadata
    response_meta = {"timestamp": get_timestamp()}
    if meta and isinstance(meta, dict):
        response_meta.update(meta)

    payload = {
        "success": False,
        "error": {"message": message, "code": code},
        "meta": response_meta,
    }

    headers = {
        "Content-Type": "application/json",
    }

    return request.make_json_response(payload, status=status, headers=headers)

"""Route decorator preset for native MCP protocol endpoints.

Thin wrapper over :func:`odoo.http.route` that pins the routing options every
``/mcp/*`` JSON-RPC endpoint needs, so individual handlers only declare their
path and HTTP methods.
"""

from odoo import http

# Per-route request-body ceiling for the JSON-RPC control plane. Far below the
# framework default (128 MiB) so a single authenticated client cannot make the
# worker buffer and parse an outsized body, yet generous enough for legitimate
# tool calls (including base64 payloads). Enforced in MCPDispatcher.pre_dispatch.
MCP_MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10 MiB


def mcp_route(*args, **kwargs):
    """Bind a route served by the custom ``type='mcp'`` dispatcher.

    Pins ``type='mcp'`` / ``auth='mcp'`` and the transport defaults; callers
    still pass the path and ``methods``. Each option is set as a default, so an
    endpoint may override it when genuinely needed.
    """
    kwargs.setdefault("type", "mcp")
    kwargs.setdefault("auth", "mcp")
    # csrf=False: machine-to-machine JSON-RPC endpoint authenticated by a bearer
    # API key (auth='mcp'); there is no browser session/cookie to forge.
    kwargs.setdefault("csrf", False)
    kwargs.setdefault("save_session", False)
    kwargs.setdefault("cors", "*")
    kwargs.setdefault("max_content_length", MCP_MAX_CONTENT_LENGTH)
    return http.route(*args, **kwargs)

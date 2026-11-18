"""Deactivate custom tools whose name collides with a new builtin tool.

Version 2.2.0 adds the ``read_attachment``, ``list_record_attachments`` and
``upload_attachment`` builtin tools. A pre-existing ``mcp.custom.tool`` row
with one of those names passed the name constraint at save time (it only
rejects the builtins known then) but would now be shadowed: builtins win at
dispatch, and ``tools/list`` would advertise the name twice. Such rows are
archived here with a log line so an administrator can rename them.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    if "mcp.custom.tool" not in env:
        return
    builtins = list(env["mcp.mixin"]._get_mcp_tools())
    colliding = env["mcp.custom.tool"].search(
        [("name", "in", builtins), ("active", "=", True)]
    )
    if not colliding:
        return
    colliding.write({"active": False})
    _logger.warning(
        "Archived %s custom MCP tool(s) whose name collides with a builtin "
        "tool: %s. Rename and re-activate them to keep using them.",
        len(colliding),
        ", ".join(colliding.mapped("name")),
    )

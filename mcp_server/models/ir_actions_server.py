"""Execution glue for MCP custom tools.

``mcp.custom.tool`` wraps an ``ir.actions.server``; the runner passes the tool's
arguments and collects its result through a shared mutable dict placed on the
context (see :meth:`mcp.custom.tool._run_tool`). This override exposes that dict
to the action's Python code as ``mcp`` in the safe_eval context -- ``mcp['args']``
in, ``mcp['result'] = ...`` out (safe_eval permits subscript assignment). It is
inert whenever the ``mcp_tool_call`` context key is absent, so ordinary server
actions are unaffected.
"""

from odoo import models


class IrActionsServer(models.Model):
    _inherit = "ir.actions.server"

    def _get_eval_context(self, action=None):
        """Expose the MCP tool-call dict to code actions as ``mcp``.

        When invoked via ``mcp.custom.tool._run_tool`` the context carries
        ``mcp_tool_call`` -> ``{"args": {...}, "result": None}``; inject it as
        ``mcp`` so the action's Python code can read ``mcp['args']`` and assign
        ``mcp['result'] = ...``. Absent the key this is a no-op -- no behavior
        change for ordinary server-action evaluation.
        """
        ctx = super()._get_eval_context(action=action)
        tool_call = self.env.context.get("mcp_tool_call")
        if isinstance(tool_call, dict):
            ctx["mcp"] = tool_call
        return ctx

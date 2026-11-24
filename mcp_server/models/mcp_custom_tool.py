"""Admin-defined MCP tools backed by ``ir.actions.server``.

Each record exposes a curated verb (e.g. ``confirm_sale_order``) to MCP clients
instead of enabling generic CRUD on a model. The wrapped server action runs as
the calling user; access is the action's own boundary, enforced by the module
(non-su) at list and call time. The wrapped model does NOT need to be listed in
``mcp.enabled.model``, and that model's per-operation Allow flags do NOT apply --
the tool-level gate (plus the caller's ACLs) is the control, so a custom tool can
perform an operation disabled on ``mcp.enabled.model``.

This file holds the model, its constraints and security, plus the execution
runner (``_run_tool`` / ``_visible_tools``) that the ``tools/list`` and
``tools/call`` endpoints drive. ``tools/list`` searches this table live per
request, so edits take effect immediately (no cache to invalidate).
"""

import json
import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)

# MCP tool names must be safe identifiers a client can reference verbatim.
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class McpCustomTool(models.Model):
    # All access (read included) is admin-only (group_mcp_admin); MCP discovery is
    # sudo-mediated in the controller and gated by the non-su _user_can_run() in
    # _visible_tools(), so a caller never reads this model directly and only
    # name/description/schema are ever surfaced -- never the action code, which
    # stays group_system-restricted on ir.actions.server.
    #
    # Wiring a tool to a server action is itself a security boundary, closed by
    # two constraints below: _check_action_is_code does a non-su read check on
    # action_id (a delegated admin without base.group_system has no
    # ir.actions.server read ACL, so cannot wire any action), and
    # _check_is_readonly_system_only restricts is_readonly -- which governs the
    # mcp:read/mcp:write scope boundary -- to base.group_system. So in practice
    # only a system administrator defines a tool; _user_can_run() re-gates, non-su,
    # WHO may run each tool at call time, so defining one never widens execution rights.
    _name = "mcp.custom.tool"
    _description = "MCP Custom Tool"

    name = fields.Char(
        required=True,
        help="MCP tool name the LLM calls. Must match ^[A-Za-z0-9_-]{1,64}$, be "
        "unique, and not collide with a builtin tool name.",
    )
    description = fields.Text(
        required=True,
        help="This is the LLM's contract: describe what the tool does, when to "
        "call it, and each of its arguments. E.g.: 'Confirm a sale quotation. "
        "Call this when the user asks to confirm a quotation. Arguments: "
        "order_id (integer) - the ID of the sale order to confirm.'",
    )
    action_id = fields.Many2one(
        "ir.actions.server",
        string="Server Action",
        required=True,
        ondelete="cascade",
        help="The server action executed when this tool is called.",
    )
    input_schema = fields.Text(
        string="Input Schema",
        default='{"type": "object", "properties": {}}',
        help="JSON Schema for the tool's arguments, advertised to clients in "
        "tools/list. The action reads them from mcp['args']. E.g.: "
        '{"type": "object", "properties": {"order_id": {"type": "integer"}}, '
        '"required": ["order_id"]}',
    )
    is_readonly = fields.Boolean(
        string="Read-only",
        default=False,
        help="Advertised as the tool's readOnlyHint. Read-only OAuth sessions "
        "(scope mcp:read) may only call read-only tools. This is a trust label, "
        "not an enforced guarantee: set it True only when the linked action truly "
        "makes no writes, or a mcp:read client will be able to trigger those writes.",
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "unique_name",
            "UNIQUE(name)",
            "A custom tool with this name already exists.",
        )
    ]

    @api.constrains("name")
    def _check_name(self):
        builtins = self.env["mcp.mixin"]._get_mcp_tools()
        for tool in self:
            if not _TOOL_NAME_RE.fullmatch(tool.name or ""):
                raise ValidationError(
                    _(
                        "Invalid tool name '%(name)s': use 1-64 characters from "
                        "A-Z, a-z, 0-9, underscore or hyphen.",
                        name=tool.name,
                    )
                )
            # A builtin tool always wins at dispatch, so a colliding custom tool
            # would never be callable -- reject it at save for clarity.
            if tool.name in builtins:
                raise ValidationError(
                    _(
                        "'%(name)s' is the name of a builtin MCP tool; pick a "
                        "different name.",
                        name=tool.name,
                    )
                )

    @api.constrains("input_schema")
    def _check_input_schema(self):
        for tool in self:
            try:
                schema = json.loads(tool.input_schema or "")
            except (ValueError, TypeError) as err:
                raise ValidationError(
                    _("Input schema must be valid JSON: %s", err)
                ) from err
            if not isinstance(schema, dict):
                raise ValidationError(
                    _(
                        "Input schema must be a JSON object, e.g. "
                        '{"type": "object", "properties": {}}.'
                    )
                )
            schema_type = schema.get("type")
            if schema_type is not None and schema_type != "object":
                raise ValidationError(
                    _(
                        'Input schema \'type\' must be "object", got "%(type)s".',
                        type=schema_type,
                    )
                )
            # ``required`` (when present) must be a list of property-name strings;
            # the tools/call required-arg check iterates and joins them. Reject a
            # malformed value here at save.
            required = schema.get("required")
            if required is not None and (
                not isinstance(required, list)
                or not all(isinstance(item, str) for item in required)
            ):
                raise ValidationError(
                    _(
                        "Input schema 'required' must be a list of property "
                        "name strings."
                    )
                )

    @api.constrains("is_readonly")
    def _check_is_readonly_system_only(self):
        for tool in self:
            # is_readonly governs the mcp:read/mcp:write scope boundary, so a
            # delegated non-system MCP admin must not be able to mislabel a write
            # action as read-only.
            if tool.is_readonly and not self.env.user.has_group("base.group_system"):
                raise ValidationError(
                    _(
                        "Only a system administrator may mark a custom tool "
                        "read-only, because that flag governs the OAuth "
                        "read/write scope boundary."
                    )
                )

    @api.constrains("action_id")
    def _check_action_is_code(self):
        for tool in self:
            # The caller must be able to READ the wrapped action (wiring one is a
            # security boundary). @api.constrains runs on a SUDO'd recordset, so a
            # bare access check would be a no-op; re-bind to the real caller
            # (with_user, non-su) to actually enforce it.
            try:
                action = tool.action_id.with_user(self.env.user)
                action.check_access_rights("read")
                action.check_access_rule("read")
            except AccessError:
                raise ValidationError(
                    _("You do not have access to the selected server action.")
                ) from None
            action = tool.action_id.sudo()  # sudo: read action definition (state)
            if action.state != "code":
                raise ValidationError(
                    _(
                        "Custom tools must wrap a Python Code server action. Other "
                        "server-action types run once per selected record, and a "
                        "tool call passes no record, so the wrapped action would "
                        "silently do nothing and falsely report success."
                    )
                )

    # ------------------------------------------------------------------
    # Execution + visibility
    # ------------------------------------------------------------------
    def _sudo_action(self):
        """Return this tool's wrapped action reference read under sudo.

        Sudo reads the action DEFINITION only (groups_id/model_id/state; the code
        stays group_system-restricted on ir.actions.server). Sudo the RECORD so a
        caller without mcp.custom.tool read does not re-trigger that model-read ACL
        navigating action_id. The action is NOT executed under this sudo -- callers
        that RUN it re-bind to the caller's non-su env (``.with_env(self.env)``).
        """
        self.ensure_one()
        return self.sudo().action_id  # sudo: read action definition, not exec

    def _user_can_run(self):
        """Whether the CALLING user (non-su) may run this tool.

        The access boundary is the wrapped action's own: the action's Allowed
        Groups when set, else write access to the action's model (mirrors the
        groups/model-write gate inline in core ``run()``, ir_actions.py).
        Enforced here NON-su -- and NOT probed through a ``sudo()`` recordset --
        because under ``sudo()`` the access checks are a no-op, so a sudo'd probe
        of the model-access branch would pass for everyone. This is therefore
        the real gate, evaluated at both list and call time.
        """
        self.ensure_one()
        # Read the action DEFINITION (groups_id/model_id) under sudo via
        # _sudo_action; it is NOT executed here, and the gate below still
        # evaluates the CALLER (self.env).
        action = self._sudo_action()
        groups = action.groups_id
        if groups:
            return bool(groups & self.env.user.groups_id)
        model = action.model_id.model
        if not model:
            # A server action always has a model (model_id is required); fail
            # closed if one is somehow absent.
            return False
        if model not in self.env:
            # The action's model is not in the registry (e.g. its module was
            # uninstalled but the ir.model row lingered). Fail closed rather than
            # KeyError out of the whole tools/list when this tool is filtered.
            return False
        # Empty-groups fallback: the caller must have write access to the model.
        return self.env[model].check_access_rights("write", raise_exception=False)

    def _run_tool(self, arguments):
        """Execute the wrapped action as the calling user; return its result.

        Arguments flow in and the result flows out through a shared mutable dict
        placed on the context (see ``ir.actions.server._get_eval_context``). Core
        ``run()`` sudo-reads the definition but builds the eval-context env from
        the (non-su) caller recordset, so the action's code runs under the
        caller's ACLs: ``_user_can_run`` gates only WHO may invoke the tool, it
        never grants the code elevated rights.
        """
        self.ensure_one()
        if not self._user_can_run():
            raise AccessError(
                _("You are not allowed to run the '%(name)s' tool.", name=self.name)
            )
        # Read the action REFERENCE once under sudo via _sudo_action (mirroring
        # _user_can_run); the state guard and execution below both reuse it.
        action_su = self._sudo_action()
        # Runtime backstop for _check_action_is_code: that constraint only re-fires
        # when action_id is (re)assigned, so an admin can later flip the linked
        # action to a non-code type. A non-code action passes no active_ids for a
        # record-less tool call, so run() returns False and the client would be
        # told the call succeeded. Fail loud instead.
        if action_su.state != "code":  # sudo: read action definition
            raise UserError(
                _(
                    "This custom tool's server action is not a Python Code "
                    "action and cannot be run."
                )
            )
        shared = {"args": arguments or {}, "result": None}
        # with_env(self.env) re-binds the sudo-read action to the caller's non-su
        # env, so run() executes the action code under the caller's real ACLs.
        # Dropping with_env would run the code under sudo = privilege escalation.
        action_su.with_env(self.env).with_context(mcp_tool_call=shared).run()
        return shared["result"]

    def _parsed_input_schema(self):
        """Parse this tool's ``input_schema`` JSON, or ``None`` if malformed.

        The ``@api.constrains`` normally keeps this valid JSON, but a
        distributable module must tolerate a corrupted row. Returns the parsed
        value, or ``None`` on malformed JSON so each caller can apply its own
        fallback (``tools/list`` skips the row; ``tools/call`` degrades to an
        empty schema).
        """
        self.ensure_one()
        try:
            parsed = json.loads(self.input_schema or "{}")
        except ValueError as err:
            _logger.warning(
                "Custom tool %s: malformed input_schema: %s", self.name, err
            )
            return None
        if not isinstance(parsed, dict):
            _logger.warning(
                "Custom tool %s: input_schema is not a JSON object", self.name
            )
            return None
        return parsed

    def _visible_tools(self):
        """Active custom tools the calling user may run (drives ``tools/list``)."""
        # sudo bypasses the model-read ACL for tool DISCOVERY only; the real gate
        # is the non-su _user_can_run() below, so re-bind the sudo'd result to the
        # caller's env (with_env) before filtering -- otherwise the write check
        # short-circuits True under su (privilege escalation).
        tools = (
            self.sudo()  # sudo: bypass model-read ACL for DISCOVERY only
            .search([("active", "=", True)])
            .with_env(self.env)  # re-bind: _user_can_run() must run non-su
        )
        return tools.filtered(lambda tool: tool._user_can_run())

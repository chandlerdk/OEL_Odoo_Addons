"""Write-side MCP tools.

Contributes the write tools to :class:`McpMixin` via ``_inherit = 'mcp.mixin'``:
``create_record``, ``update_record``, ``delete_record``, their batch siblings
``create_records`` / ``update_records``, ``upload_attachment``, and the
method-call escape hatch ``call_model_method`` plus ``post_message``.

Every tool routes through the mixin chokepoints: :meth:`McpMixin._resolve_model`
applies the coarse model gate, then :meth:`McpMixin._check_op` applies the
per-operation (``create``/``write``/``unlink``) gate. The ORM call then runs as
the calling user (``self.env`` is already user-scoped, no ``sudo``),
so Odoo's ``ir.model.access``, record rules and required-field validation remain
the real enforcement boundary.

``call_model_method`` is the one exception to the per-operation gate: the
business-method escape hatch, gated instead by the per-model
``allow_method_calls`` opt-in flag (see :meth:`_validate_method_call` for the
boundary it enforces). ``post_message`` maps to ``message_post`` and is gated as
a plain **write** operation, not via ``allow_method_calls``.
"""

import ast
import base64
import json
import logging

from odoo import _, api, models
from odoo.exceptions import AccessError, MissingError, UserError

from ..controllers import utils
from ..controllers.mcp_route import MCP_MAX_CONTENT_LENGTH
from ..tools.uri_schema import build_attachment_uri
from .mcp_mixin import mcp_tool
from .mcp_tools_read import MAX_LIMIT, _tool_result

_logger = logging.getLogger(__name__)

# Essential fields read back after a create/update -- universally available
# (not every model has ``name``).
_ESSENTIAL_FIELDS = ["id", "display_name"]

# Default cap on entries per batch write call (``mcp_server.max_batch_size``);
# aligned with ``MAX_LIMIT`` so a batch never exceeds one read page.
MAX_BATCH_SIZE = 100

# Largest decoded payload ``upload_attachment`` accepts. Derived from the
# 10 MiB JSON-RPC body cap on every /mcp route (``MCP_MAX_CONTENT_LENGTH`` in
# ``mcp_route.py``): base64 inflates by 4/3, and the JSON envelope plus the
# other arguments need headroom, so the tool refuses early with a clear message
# instead of the client hitting an opaque transport 413.
MAX_UPLOAD_BYTES = MCP_MAX_CONTENT_LENGTH * 3 // 4 - 64 * 1024

# ``post_message`` subtype -> mail subtype XML id.
_SUBTYPE_XMLID = {"note": "mail.mt_note", "comment": "mail.mt_comment"}

# ORM CRUD / data-access / privilege methods that ``call_model_method`` refuses
# even when ``allow_method_calls`` is on -- otherwise the business-method hatch
# would silently grant full CRUD, bypassing the per-operation MCP gates
# (allow_read/create/write/unlink). CRUD goes through the dedicated
# create/update/delete tools.
#
# ``_validate_method_call`` already refuses the whole generic ORM surface via a
# ``hasattr(models.BaseModel, method)`` check. This explicit set is the backstop
# for what that check misses: (1) the core CRUD primitives (belt-and-suspenders)
# and (2) public data-access methods the *web* addon contributes onto ``base``
# (``read_progress_bar``, ``search_panel_*``, ``formatted_read_group*``) which
# are not ``BaseModel`` attributes. The public ``web_*`` family is covered by a
# dedicated prefix check, not listed here.
_BLOCKED_METHOD_CALLS = frozenset(
    {
        "create",
        "write",
        "unlink",
        "read",
        "search",
        "search_read",
        "search_count",
        "search_fetch",
        "fetch",
        "read_group",
        "formatted_read_group",
        "formatted_read_grouping_sets",
        "read_progress_bar",
        "name_search",
        "search_panel_select_range",
        "search_panel_select_multi_range",
        "copy",
        "browse",
        "_write",
        "sudo",
        "with_user",
        "with_env",
        "with_context",
        "fields_get",
        "load",
        "export_data",
        "name_create",
        # Self-escalating primitives: ir.actions.server.run() executes
        # server-action Python as superuser (``for action in self.sudo()``) and
        # ir.cron.method_direct_trigger runs the cron job as its own (often
        # privileged) user -- either voids the calling-user boundary the hatch
        # rests on. Blocked wholesale at the model level too (see
        # ``_METHOD_CALL_BLOCKED_MODELS``); listed here as a method-name backstop.
        "run",
        "method_direct_trigger",
    }
)

# Models whose public methods are self-elevating and are refused by
# ``call_model_method`` regardless of ``allow_method_calls``. ir.actions.* (esp.
# ir.actions.server.run) and ir.cron (method_direct_trigger) run code with
# elevated privileges, so exposing their methods would bypass the calling-user
# ACL/record-rule boundary. ir.module.* and the base.module.* wizards install,
# upgrade and uninstall modules. Scoped to these prefixes on purpose -- other
# ir.* models (ir.attachment, ...) stay callable; no blanket ir.% block.
_METHOD_CALL_BLOCKED_MODELS = ("ir.actions", "ir.cron", "ir.module", "base.module")


def _unstringify_list(value):
    """Return the list a JSON / Python-literal string encodes, else ``value``.

    Some LLM clients serialize a nested array argument as a string. Like the
    ``domain`` / ``fields`` tolerance of the read tools, a string that parses
    to a list is accepted; anything else is returned unchanged so the caller's
    own shape rule rejects it with its usual message.
    """
    if not isinstance(value, str):
        return value
    for parse in (json.loads, ast.literal_eval):
        try:
            parsed = parse(value)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            continue
        if isinstance(parsed, list):
            return parsed
        break
    return value


def _json_safe(value, max_records=MAX_LIMIT):
    """Coerce a method return value into MCP-serializable JSON.

    ``call_model_method`` can return anything a public Odoo method returns:
    recordsets, action dicts, dates, bytes, etc. Recordsets become a list of
    ``{id, display_name}``; containers recurse; JSON primitives pass through;
    everything else (dates, datetimes, bytes, ...) degrades to ``str`` so the
    result always serializes and never leaks a non-JSON object to the client.

    A recordset return is capped at ``max_records`` (the read ``MAX_LIMIT`` by
    default) -- both at the top level and nested inside containers -- so a method
    returning a huge recordset cannot serialize one ``display_name`` per record
    unbounded (an authenticated output-size DoS).
    """
    if isinstance(value, models.BaseModel):
        # Recordset -> id/display_name pairs; fall back to bare ids when
        # display_name can't be computed (e.g. a record rule on a related field).
        capped = value[:max_records]
        try:
            out = [{"id": rec.id, "display_name": rec.display_name} for rec in capped]
        except Exception:  # noqa: BLE001 - best-effort; keep ids on failure
            _logger.debug(
                "call_model_method: display_name unavailable for %s", value._name
            )
            out = list(capped.ids)
        if len(value) > max_records:
            out.append(
                _(
                    "... [truncated: %(shown)d of %(total)d records shown]",
                    shown=max_records,
                    total=len(value),
                )
            )
        return out
    if isinstance(value, dict):
        return {str(key): _json_safe(val, max_records) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, max_records) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


class McpToolsWrite(models.AbstractModel):
    """Write tools contributed to the ``mcp.mixin`` tool layer."""

    # Split from the read-tool layer on purpose (write tools are gated
    # separately); both legitimately contribute to the same mixin.
    _inherit = "mcp.mixin"  # pylint: disable=consider-merging-classes-inherited

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    def _record_web_url(self, model, record_id):
        """Best-effort web URL to a record.

        Returns ``""`` when no base URL is configured, else the form view
        URL of the web client (see :meth:`_web_form_url`).
        """
        base_url = (
            self.env["ir.config_parameter"]
            .sudo()  # sudo: read system web.base.url param (not user data)
            .get_param("web.base.url")
        )
        if not base_url:
            return ""
        return self._web_form_url(base_url, model, record_id)

    @staticmethod
    def _web_form_url(base_url, model, record_id):
        """The ``/web#id=...`` URL of a record's form view."""
        return f"{base_url}/web#id={record_id}&model={model}&view_type=form"

    @staticmethod
    def _format_write_text(message, record, url):
        """Render a concise confirmation block for create/update."""
        lines = [message]
        display_name = record.get("display_name")
        if display_name:
            lines.append(_("Name: %s", display_name))
        if url:
            lines.append(_("URL: %s", url))
        return "\n".join(lines)

    def _max_batch_size(self):
        """Effective entries-per-batch cap for the batch write tools.

        A misconfigured 0 / negative / garbage value falls back to the module
        default rather than uncapping (or refusing) every batch.
        """
        cap = self._mcp_int_config("mcp_server.max_batch_size", MAX_BATCH_SIZE)
        return cap if cap > 0 else MAX_BATCH_SIZE

    def _resolve_record_ids_or_raise(
        self, model, model_rs, record_ids, cap, deduplicate=False
    ):
        """Coerce, cap, (optionally) dedupe and existence-check a list of ids.

        Shared by ``call_model_method`` and ``update_records``. The caller
        owns the non-list / empty-list guards (an empty list means a
        model-level call for ``call_model_method``). The cap is applied to the
        raw list so duplicates cannot smuggle a batch past it; ``deduplicate``
        then drops repeats (order-preserving). Any id missing from the DB
        raises one ``MissingError`` listing every missing id. ``exists()`` is
        record-rule-blind: rule/ACL-denied records pass here and surface as
        the ORM's ``AccessError`` at call time, like the single-record tools.
        """
        try:
            record_ids = [self._coerce_record_id(rid) for rid in record_ids]
        except (TypeError, ValueError) as err:
            raise UserError(_("'record_ids' must be a list of integers.")) from err
        # Bound the batch: an unbounded id list is a large IN(...) plus
        # unbounded result serialization.
        if len(record_ids) > cap:
            raise UserError(
                _(
                    "Too many record_ids: %(count)s (max %(max)s). Split the "
                    "batch into smaller calls.",
                    count=len(record_ids),
                    max=cap,
                )
            )
        if deduplicate:
            record_ids = list(dict.fromkeys(record_ids))
        target = model_rs.browse(record_ids).exists()
        # Reject the whole call if ANY requested id is missing, rather than
        # silently narrowing to the survivors and reporting success -- the
        # single-record tools raise the same way, and a partial run the caller
        # can't detect is worse than a clean error.
        existing = set(target.ids)
        missing = [rid for rid in record_ids if rid not in existing]
        if missing:
            raise MissingError(
                _(
                    "Records not found: %(model)s with IDs %(ids)s",
                    model=model,
                    ids=missing,
                )
            )
        return target

    def _batch_write_confirmation(self, model, records, message):
        """Read the written records back and build the batch confirmation.

        Pluralized sibling of :meth:`_write_confirmation`, shared by
        create_records / update_records: one batched ``read`` of the essential
        fields, a web URL per record and a text listing (one line per record).
        ``web.base.url`` is read once for the whole batch.
        """
        base_url = (
            self.env["ir.config_parameter"]
            .sudo()  # sudo: read system web.base.url param (not user data)
            .get_param("web.base.url")
        )
        rows = []
        lines = [message]
        for data in records.read(_ESSENTIAL_FIELDS):
            url = self._web_form_url(base_url, model, data["id"]) if base_url else ""
            rows.append(
                {"id": data["id"], "display_name": data["display_name"], "url": url}
            )
            line = "- [%s] %s" % (data["id"], data["display_name"] or "")
            lines.append(f"{line} {url}".rstrip())
        structured = {
            "success": True,
            "count": len(rows),
            "records": rows,
            "message": message,
        }
        return _tool_result("\n".join(lines), structured)

    # ------------------------------------------------------------------
    # create_record
    # ------------------------------------------------------------------
    @mcp_tool(
        name="create_record",
        title="Create Record",
        description=(
            "Create a new record in a model. Pass 'values' as a field->value "
            "mapping. Runs as the calling user, so Odoo's access rights, record "
            "rules and required-field validation apply. To create several "
            "records at once, use create_records instead of repeated calls."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Technical model name (e.g. 'res.partner').",
                },
                "values": {
                    "type": "object",
                    "description": (
                        'Field values for the new record, e.g. {"name": "ACME"}.'
                    ),
                },
            },
            "required": ["model", "values"],
            "additionalProperties": False,
        },
        operation="create",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
    @api.model
    def create_record(self, model, values):
        """Create one record as the calling user and confirm it."""
        model_rs = self._resolve_model(model)
        self._check_op(model, "create")

        if not isinstance(values, dict) or not values:
            raise UserError(_("No values provided for record creation."))
        values = self._normalize_write_values(model_rs, values)
        self._check_write_values(model, model_rs.browse(), [values], creating=True)

        # Runs as the calling user -> ORM enforces create ACLs, record rules
        # and required-field validation.
        record = model_rs.create(values)
        message = _(
            "Successfully created %(model)s record with ID %(id)s",
            model=model,
            id=record.id,
        )
        return self._write_confirmation(model, record, message)

    # ------------------------------------------------------------------
    # create_records (batch)
    # ------------------------------------------------------------------
    @mcp_tool(
        name="create_records",
        title="Create Records",
        description=(
            "Create several records in one model with a single call. Pass "
            "'records' as a list of field->value mappings, one per record. "
            "Runs as the calling user, so Odoo's access rights, record rules "
            "and required-field validation apply. The batch is atomic: if any "
            "entry fails, the whole batch is rolled back and nothing is "
            "created. Odoo may not name the failing entry; on an ambiguous "
            "failure, retry in smaller batches or bisect. Prefer this over "
            "repeated create_record calls."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Technical model name (e.g. 'res.partner').",
                },
                "records": {
                    "type": "array",
                    "items": {"type": "object", "minProperties": 1},
                    "minItems": 1,
                    "description": "One field->value mapping per record to create.",
                },
            },
            "required": ["model", "records"],
            "additionalProperties": False,
        },
        operation="create",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
    @api.model
    def create_records(self, model, records):
        """Create N records in one ORM call as the calling user."""
        model_rs = self._resolve_model(model)
        self._check_op(model, "create")

        # The dispatcher only checks required-presence, so validate shape here.
        records = _unstringify_list(records)
        if not isinstance(records, list) or not records:
            raise UserError(_("'records' must be a non-empty list of objects."))
        max_entries = self._max_batch_size()
        if len(records) > max_entries:
            raise UserError(
                _(
                    "Too many records: %(count)s (max %(max)s). Split the batch "
                    "into smaller calls.",
                    count=len(records),
                    max=max_entries,
                )
            )
        for index, entry in enumerate(records):
            if not isinstance(entry, dict) or not entry:
                raise UserError(
                    _(
                        "Entry %(index)s of 'records' must be a non-empty "
                        "field->value object.",
                        index=index,
                    )
                )
        records = [self._normalize_write_values(model_rs, entry) for entry in records]
        self._check_write_values(model, model_rs.browse(), records, creating=True)

        # One ORM call (batched defaults/computes); runs as the calling user ->
        # ORM enforces create ACLs, record rules and required-field validation.
        # Any failure rolls the whole batch back via the dispatcher savepoint.
        created = model_rs.create(records)
        message = _(
            "Successfully created %(count)s %(model)s records",
            count=len(created),
            model=model,
        )
        return self._batch_write_confirmation(model, created, message)

    # ------------------------------------------------------------------
    # update_record
    # ------------------------------------------------------------------
    @mcp_tool(
        name="update_record",
        title="Update Record",
        description=(
            "Update an existing record by ID. Pass 'values' as a field->value "
            "mapping. Runs as the calling user, so Odoo's access rights and "
            "record rules apply. To apply the same values to several records, "
            "use update_records instead of repeated calls."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Technical model name (e.g. 'res.partner').",
                },
                "record_id": {
                    "type": "integer",
                    "description": "The record ID to update.",
                },
                "values": {
                    "type": "object",
                    "description": (
                        'Field values to update, e.g. {"name": "New name"}.'
                    ),
                },
            },
            "required": ["model", "record_id", "values"],
            "additionalProperties": False,
        },
        operation="write",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
    @api.model
    def update_record(self, model, record_id, values):
        """Update one record as the calling user and confirm it."""
        model_rs = self._resolve_model(model)
        self._check_op(model, "write")

        if not isinstance(values, dict) or not values:
            raise UserError(_("No values provided for record update."))

        record = self._browse_record_or_raise(model, model_rs, record_id)
        self._check_write_fields(model_rs, values)
        values = self._normalize_write_values(model_rs, values)
        self._check_write_values(model, record, [values])

        # Runs as the calling user -> ORM enforces write ACLs / record rules.
        record.write(values)
        message = _(
            "Successfully updated %(model)s record with ID %(id)s",
            model=model,
            id=record.id,
        )
        return self._write_confirmation(model, record, message)

    # ------------------------------------------------------------------
    # update_records (batch)
    # ------------------------------------------------------------------
    @mcp_tool(
        name="update_records",
        title="Update Records",
        description=(
            "Update several records of one model in a single call. Two forms: "
            "(a) the same values for every record: pass 'record_ids' and one "
            "'values' field->value mapping; (b) different values per record: "
            "pass 'updates' as a list of {id, values} entries and omit "
            "'record_ids' / 'values'. Runs as the calling user, so Odoo's "
            "access rights and record rules apply. The batch is atomic: if any "
            "record fails (missing, denied or invalid), the whole batch is "
            "rolled back and nothing is written. Prefer this over repeated "
            "update_record calls."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Technical model name (e.g. 'res.partner').",
                },
                "record_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 1,
                    "description": (
                        "Form (a): the record IDs that all receive 'values'."
                    ),
                },
                "values": {
                    "type": "object",
                    "minProperties": 1,
                    "description": (
                        "Form (a): field values applied to every record in "
                        "'record_ids'."
                    ),
                },
                "updates": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "values": {"type": "object", "minProperties": 1},
                        },
                        "required": ["id", "values"],
                        "additionalProperties": False,
                    },
                    "description": (
                        "Form (b): one {id, values} entry per record, each with "
                        "its own field values. Use instead of record_ids + "
                        "values; each id may appear once."
                    ),
                },
            },
            "required": ["model"],
            "additionalProperties": False,
        },
        operation="write",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
    @api.model
    def update_records(self, model, record_ids=None, values=None, updates=None):
        """Write to N records in one call: shared values or per-record values."""
        model_rs = self._resolve_model(model)
        self._check_op(model, "write")

        shared = record_ids is not None or values is not None
        if updates is not None and shared:
            raise UserError(
                _("Pass either 'record_ids' + 'values' or 'updates', not both.")
            )
        if updates is not None:
            records = self._update_records_each(model, model_rs, updates)
        elif shared:
            records = self._update_records_shared(model, model_rs, record_ids, values)
        else:
            raise UserError(
                _(
                    "Pass 'record_ids' + 'values', or 'updates' (a list of "
                    "{id, values})."
                )
            )

        message = _(
            "Successfully updated %(count)s %(model)s records",
            count=len(records),
            model=model,
        )
        return self._batch_write_confirmation(model, records, message)

    @staticmethod
    def _check_write_fields(model_rs, values):
        """Refuse a field name that ``model_rs`` does not have.

        Some ``write`` overrides read every key before core checks it (for
        example ``res.partner.write``), so an unknown name would end as a
        ``KeyError``, shown as an internal error. The message has the wording
        of core's create. A name that ``fields_get`` offers without being a
        field (the ``in_group_<id>`` keys of ``res.users`` up to Odoo 18) stays
        writable.
        """
        unknown = [name for name in values if name not in model_rs._fields]
        if unknown:
            offered = model_rs.fields_get(attributes=["type"])
            unknown = [name for name in unknown if name not in offered]
        if unknown:
            raise UserError(
                _(
                    "Invalid field '%(field)s' on model '%(model)s'",
                    field=unknown[0],
                    model=model_rs._name,
                )
            )

    def _normalize_write_values(self, model_rs, values):
        """Return ``values`` with each many2one value as a record id.

        The read tools return a many2one as ``[id, "name"]``, and clients send
        that pair back. Core's ``write`` stores NULL for a list, and its
        ``create`` fails in SQL. So the pair (whatever its name), ``[id]`` and
        ``{"id": id}`` become the id, an empty string or list clears the field
        as in core, and any other shape is refused. The values of x2many create
        and update commands are handled the same way.
        """
        normalized = {}
        for name, value in values.items():
            field = model_rs._fields.get(name)
            if field is not None and field.type == "many2one":
                value = self._many2one_value(model_rs, name, value)
            elif (
                field is not None
                and field.type in ("one2many", "many2many")
                and isinstance(value, list)
            ):
                comodel = model_rs.env[field.comodel_name]
                value = [
                    (
                        [
                            command[0],
                            command[1],
                            self._normalize_write_values(comodel, command[2]),
                        ]
                        if isinstance(command, (list, tuple))
                        and len(command) == 3
                        and command[0] in (0, 1)
                        and isinstance(command[2], dict)
                        else command
                    )
                    for command in value
                ]
            normalized[name] = value
        return normalized

    def _many2one_value(self, model_rs, name, value):
        """The record id that ``value`` gives for many2one ``name``, or refuse it."""
        if value is False or value is None:
            return value
        if value in ("", [], ()):
            return False
        candidate = value
        if isinstance(value, (list, tuple)) and len(value) in (1, 2):
            candidate = value[0]
        elif isinstance(value, dict) and "id" in value:
            candidate = value["id"]
        try:
            return self._coerce_record_id(candidate)
        except (TypeError, ValueError):
            raise UserError(
                _(
                    "Field '%(field)s' on model '%(model)s' takes a record id "
                    "(an integer), or false to clear it. Got: %(value)s",
                    field=name,
                    model=model_rs._name,
                    value=json.dumps(value, default=str)[:80],
                )
            ) from None

    def _update_records_shared(self, model, model_rs, record_ids, values):
        """Form (a): one ``write(values)`` on the deduplicated id list."""
        if not isinstance(values, dict) or not values:
            raise UserError(_("No values provided for record update."))
        record_ids = _unstringify_list(record_ids)
        if not isinstance(record_ids, list) or not record_ids:
            raise UserError(_("'record_ids' must be a non-empty list of integers."))

        records = self._resolve_record_ids_or_raise(
            model, model_rs, record_ids, self._max_batch_size(), deduplicate=True
        )
        self._check_write_fields(model_rs, values)
        values = self._normalize_write_values(model_rs, values)
        self._check_write_values(model, records, [values])
        # Runs as the calling user -> ORM enforces write ACLs / record rules.
        # Any failure rolls the whole batch back via the dispatcher savepoint.
        records.write(values)
        return records

    def _update_records_each(self, model, model_rs, updates):
        """Form (b): one ``write`` per ``{id, values}`` entry, in one savepoint.

        Odoo has no batched per-record write, so this is a server-side loop;
        the client still saves N round trips, gets one audit row and
        all-or-nothing semantics. A repeated id is refused rather than
        resolved last-wins: two value sets for one record is a client error.
        """
        updates = _unstringify_list(updates)
        if not isinstance(updates, list) or not updates:
            raise UserError(_("'updates' must be a non-empty list of {id, values}."))
        ids, values_list = [], []
        for index, entry in enumerate(updates):
            bad = (
                not isinstance(entry, dict)
                or set(entry) != {"id", "values"}
                or not isinstance(entry["values"], dict)
                or not entry["values"]
            )
            if bad:
                raise UserError(
                    _(
                        "Entry %(index)s of 'updates' must be an object with "
                        "'id' and a non-empty 'values'.",
                        index=index,
                    )
                )
            self._check_write_fields(model_rs, entry["values"])
            ids.append(entry["id"])
            values_list.append(self._normalize_write_values(model_rs, entry["values"]))

        # Coerce + cap + existence check; duplicates are then a client error.
        records = self._resolve_record_ids_or_raise(
            model, model_rs, ids, self._max_batch_size()
        )
        seen, duplicates = set(), []
        for rid in records.ids:
            if rid in seen and rid not in duplicates:
                duplicates.append(rid)
            seen.add(rid)
        if duplicates:
            raise UserError(
                _(
                    "Duplicate ids in 'updates': %(ids)s. List each record once.",
                    ids=duplicates,
                )
            )
        self._check_write_values(model, records, values_list)

        # Runs as the calling user -> ORM enforces write ACLs / record rules.
        # Any failure rolls the whole batch back via the dispatcher savepoint.
        for record, vals in zip(records, values_list, strict=True):
            record.write(vals)
        return records

    def _write_confirmation(self, model, record, message):
        """Read the written record back and build the confirmation tool result.

        Shared by create_record / update_record: both read the essential fields,
        build the record web URL and return the same structured confirmation --
        only ``message`` differs.
        """
        data = record.read(_ESSENTIAL_FIELDS)[0]
        url = self._record_web_url(model, record.id)
        structured = {
            "success": True,
            "record": data,
            "url": url,
            "message": message,
        }
        return _tool_result(self._format_write_text(message, data, url), structured)

    # ------------------------------------------------------------------
    # delete_record
    # ------------------------------------------------------------------
    @mcp_tool(
        name="delete_record",
        title="Delete Record",
        description=(
            "Delete a record by ID. Runs as the calling user, so Odoo's access "
            "rights and record rules apply. This action is destructive and "
            "cannot be undone."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Technical model name (e.g. 'res.partner').",
                },
                "record_id": {
                    "type": "integer",
                    "description": "The record ID to delete.",
                },
            },
            "required": ["model", "record_id"],
            "additionalProperties": False,
        },
        operation="unlink",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    )
    @api.model
    def delete_record(self, model, record_id):
        """Delete one record as the calling user and confirm it."""
        model_rs = self._resolve_model(model)
        self._check_op(model, "unlink")

        record = self._browse_record_or_raise(model, model_rs, record_id)
        self._check_write_values(model, record, [])

        # Capture identity before unlink; display_name may be False for models
        # without a meaningful name (e.g. mail.message).
        deleted_id = record.id
        deleted_name = record.display_name or _("ID %s", deleted_id)

        # Runs as the calling user -> ORM enforces unlink ACLs / record rules.
        record.unlink()

        message = _(
            "Successfully deleted %(model)s record '%(name)s' (ID: %(id)s)",
            model=model,
            name=deleted_name,
            id=deleted_id,
        )
        structured = {
            "success": True,
            "deleted_id": deleted_id,
            "deleted_name": deleted_name,
            "message": message,
        }
        return _tool_result(message, structured)

    # ------------------------------------------------------------------
    # call_model_method (gated by allow_method_calls)
    # ------------------------------------------------------------------
    @staticmethod
    def _is_api_private(model_rs, method):
        """Whether a class of the model's MRO marks ``method`` with ``@api.private``.

        A module that extends a model can mark a method private without being the
        last to define it, so every level is checked, as core does.
        """
        return any(
            getattr(getattr(cls, method, None), "_api_private", False)
            for cls in type(model_rs).mro()
        )

    def _validate_method_call(self, model, model_rs, method):
        """Reject anything that is not a genuine callable business method.

        Enforces the boundary for ``call_model_method``: no private
        methods, no public ``web_*`` data-access family, no hard-blocked ORM
        methods, known data-access methods stay gated by their per-op flag, and
        the rest of the generic ``BaseModel`` API is refused wholesale.
        """
        # Refuse private methods (covers dunders too -- all start with '_').
        if method.startswith("_"):
            raise AccessError(_("Private methods cannot be called via MCP: %s", method))

        # Refuse method calls on the self-elevating action/cron models outright
        # (ir.actions.server.run runs server-action code as superuser; ir.cron
        # .method_direct_trigger runs a cron job as its privileged owner). These
        # stay blocked whatever ``allow_method_calls`` says; other ir.* models
        # (ir.attachment, ...) remain callable.
        if any(
            model == blocked or model.startswith(blocked + ".")
            for blocked in _METHOD_CALL_BLOCKED_MODELS
        ):
            raise AccessError(
                _(
                    "Method calls on '%s' are not permitted via MCP: its methods "
                    "run with elevated privileges.",
                    model,
                )
            )

        # Refuse the public ``web_*`` family (web_save / web_read / web_search_read
        # / ...). Defined on ``base`` via the web addon, they perform
        # CRUD/data-access (``web_save`` writes/creates) and would bypass the
        # allow_create/write/read gates. A prefix check covers future ``web_*``
        # additions too.
        if method.startswith("web_"):
            raise AccessError(
                _(
                    "Method '%s' is a data-access method; use the dedicated CRUD "
                    "tools. call_model_method is for business methods.",
                    method,
                )
            )

        # Hard-blocked ORM CRUD / data-access methods -- see
        # ``_BLOCKED_METHOD_CALLS`` for why these stay refused even with
        # ``allow_method_calls`` on.
        if method in _BLOCKED_METHOD_CALLS:
            raise AccessError(
                _(
                    "Method '%s' is a data-access method; use the dedicated CRUD "
                    "tools. call_model_method is for business methods.",
                    method,
                )
            )

        # Two-tier boundary so ``allow_method_calls`` permits a model's OWN public
        # business methods only, never the generic ORM/data-access API:
        #
        #  * KNOWN data-access methods (in ``utils.XMLRPC_METHOD_OPERATION_MAP``)
        #    stay gated by their matching per-op flag -- e.g. ``message_post``
        #    (write), ``default_get`` (read), ``action_delete`` (unlink) obey their
        #    flag rather than slipping through on ``allow_method_calls`` alone. The
        #    if/elif matters: a mapped method that clears its per-op check is a
        #    permitted data method and must NOT fall into the generic-ORM block.
        #  * Any OTHER attribute of ``BaseModel`` *or* the ``base`` registry model
        #    is a generic ORM/CRUD/recordset helper (``update``, ``mapped``,
        #    ``browse``, ``get_views``, ...), not a business method -- refuse
        #    wholesale; a denylist can't enumerate it.
        #
        # What remains -- not private, not ``web_*``, not hard-blocked, not mapped,
        # not on ``BaseModel`` or ``base`` -- is a genuine business method (e.g.
        # ``action_confirm``), gated by ``allow_method_calls`` only, as intended.
        mapped_op = utils.map_method_to_operation(method)
        if mapped_op:
            if not utils.check_model_operation_allowed(self.env, model, mapped_op):
                raise AccessError(
                    _(
                        "Method '%(method)s' maps to the '%(op)s' operation, which "
                        "is not enabled for model '%(model)s' via MCP.",
                        method=method,
                        op=mapped_op,
                        model=model,
                    )
                )
        elif hasattr(models.BaseModel, method) or hasattr(self.env["base"], method):
            # ``self.env["base"]`` also catches generic API that addons contribute
            # to the ``base`` registry model (get_views / get_view / onchange from
            # web+base, ...) -- these are NOT ``BaseModel`` Python attributes, so
            # the ``BaseModel`` check alone misses them, yet they are cross-model
            # data access (get_views leaks field/view metadata like fields_get),
            # never per-model business methods.
            raise AccessError(
                _(
                    "Method '%s' is part of the generic ORM API, not a model "
                    "business method; use the dedicated create/update/delete/search "
                    "tools. call_model_method is for business methods.",
                    method,
                )
            )

        # Core's RPC also refuses a method marked @api.private at any level of
        # the MRO (get_public_method). Checked after the refusals above, so each
        # of them keeps its own message.
        if self._is_api_private(model_rs, method):
            raise AccessError(_("Private methods cannot be called via MCP: %s", method))

        # As core's RPC (get_public_method): the name must be a method of the
        # class, called on a record. The class attribute is read, so a
        # property never runs, and a classmethod or staticmethod is refused.
        cls_attr = getattr(type(model_rs), method, None)
        if not callable(cls_attr):
            raise UserError(
                _(
                    "'%(method)s' is not a callable method on model '%(model)s'.",
                    method=method,
                    model=model,
                )
            )
        if cls_attr == getattr(model_rs, method, None):
            raise AccessError(
                _(
                    "Method '%s' cannot be called via MCP: it is not a method of "
                    "the records.",
                    method,
                )
            )

    @mcp_tool(
        name="call_model_method",
        title="Call Model Method",
        description=(
            "Call a public method on a model -- the workflow escape hatch for "
            "actions not covered by CRUD (e.g. 'action_confirm'). Allowed only "
            "when the model is MCP-enabled AND its 'allow_method_calls' flag is "
            "set; private methods (underscore-prefixed or marked private in "
            "Odoo) are refused. Runs as the "
            "calling user, so Odoo's access rights and record rules apply. Prefer "
            "create_record / update_record / delete_record when sufficient."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Technical model name (e.g. 'sale.order').",
                },
                "method": {
                    "type": "string",
                    "description": (
                        "Public method name to call. Private methods "
                        "(underscore-prefixed or marked private in Odoo) are "
                        "rejected."
                    ),
                },
                "record_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": (
                        "Record IDs to call the method on. Omit to call on the "
                        "model itself (e.g. 'default_get')."
                    ),
                },
                "args": {
                    "type": "array",
                    "description": "Positional arguments for the method.",
                },
                "kwargs": {
                    "type": "object",
                    "description": "Keyword arguments for the method.",
                },
            },
            "required": ["model", "method"],
            "additionalProperties": False,
        },
        # Not a single CRUD op: gated by allow_method_calls, not _check_op.
        operation=None,
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
    @api.model
    def call_model_method(self, model, method, record_ids=None, args=None, kwargs=None):
        """Call a public model method as the calling user."""
        # Odoo's ``_()`` reads a local named ``kwargs`` as a dict holding a
        # ``context`` to find the language, so a client value bound to that name
        # (a string, or ``{"context": "x"}``) would crash every message below.
        method_kwargs, kwargs = kwargs, None
        model_rs = self._resolve_model(model)

        # Per-model opt-in gate: enabled AND allow_method_calls=True.
        if not utils.check_model_method_allowed(self.env, model):
            raise AccessError(
                _("Method calls are not enabled for model '%s' via MCP.", model)
            )

        if not isinstance(method, str) or not method.strip():
            raise UserError(_("No method name provided."))
        method = method.strip()
        self._validate_method_call(model, model_rs, method)

        args = args or []
        method_kwargs = method_kwargs or {}
        if not isinstance(args, list):
            raise UserError(_("'args' must be a list."))
        if not isinstance(method_kwargs, dict):
            raise UserError(_("'kwargs' must be an object."))
        if record_ids is not None and not isinstance(record_ids, list):
            raise UserError(_("'record_ids' must be a list."))

        if record_ids:
            # Reuse the read cap for consistency (unlike the read tools this
            # path has no built-in size limit); _limit_bounds already falls a
            # 0 / negative setting back to the default. Duplicates are kept on
            # purpose: the browse keeps the client's order and multiplicity.
            target = self._resolve_record_ids_or_raise(
                model, model_rs, record_ids, self._limit_bounds()[1]
            )
        else:
            target = model_rs

        # Audit what was called, not the values -- args/kwargs may carry PII.
        _logger.info(
            "call_model_method: model=%s method=%s record_count=%s",
            model,
            method,
            len(target) if record_ids else 0,
        )

        # Runs as the calling user -> Odoo ACLs / record rules apply.
        raw = getattr(target, method)(*args, **method_kwargs)
        result = _json_safe(raw)

        message = _(
            "Successfully called %(model)s.%(method)s",
            model=model,
            method=method,
        )
        structured = {"success": True, "result": result, "message": message}
        try:
            rendered = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            rendered = str(result)
        text = "%s\n%s" % (message, _("Result: %s", rendered))
        return _tool_result(text, structured)

    # ------------------------------------------------------------------
    # upload_attachment
    # ------------------------------------------------------------------
    @mcp_tool(
        name="upload_attachment",
        title="Upload Attachment",
        description=(
            "Create an ir.attachment from base64 file content, optionally "
            "linked to a record ('model' + 'record_id' together; attaching to a "
            "record requires write access to it via MCP and in Odoo). Without "
            "a record the attachment is standalone (requires ir.attachment "
            "create access via MCP). Effective size limit about 7 MiB of file "
            "content. Returns the attachment id and its odoo://attachment/{id} "
            "uri; read it back with read_attachment (readable when the parent "
            "model, or ir.attachment, is MCP-enabled for read), or link it to "
            "a chatter message with post_message(attachment_ids=[id]). Note: "
            "Odoo stores HTML/XML/SVG uploads from non-privileged users as "
            "text/plain."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "File name, e.g. 'invoice.pdf'.",
                },
                "data": {
                    "type": "string",
                    "description": "File content, base64-encoded.",
                },
                "model": {
                    "type": ["string", "null"],
                    "description": (
                        "Technical model of the record to attach to (with "
                        "'record_id'). Omit for a standalone attachment."
                    ),
                },
                "record_id": {
                    "type": ["integer", "null"],
                    "description": "The record to attach to (with 'model').",
                },
                "mimetype": {
                    "type": ["string", "null"],
                    "description": (
                        "Content type, e.g. 'application/pdf'. Guessed from "
                        "the name/content when omitted."
                    ),
                },
            },
            "required": ["name", "data"],
            "additionalProperties": False,
        },
        operation="create",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
    @api.model
    def upload_attachment(self, name, data, model=None, record_id=None, mimetype=None):
        """Create an attachment as the calling user (record-linked or standalone).

        Record-linked uploads are gated as **write on the parent** (MCP
        ``allow_write`` + Odoo's own ``ir.attachment.check()``, which requires
        write access to the parent record): attaching a file is part of
        writing the record, mirroring core semantics, so ``ir.attachment``
        ``allow_create`` is deliberately NOT required as well. Standalone
        uploads are gated on ``ir.attachment`` ``allow_create``.
        """
        if not isinstance(name, str) or not name.strip():
            raise UserError(_("No attachment name provided."))
        if (model is None) != (record_id is None):
            raise UserError(_("Pass 'model' and 'record_id' together, or neither."))
        if not isinstance(data, str):
            raise UserError(_("'data' must be a base64 string."))
        if mimetype is not None and not isinstance(mimetype, str):
            raise UserError(_("'mimetype' must be a string."))
        try:
            raw = base64.b64decode(data, validate=True)
        except ValueError as err:  # binascii.Error is a ValueError
            raise UserError(_("'data' is not valid base64.")) from err
        if len(raw) > MAX_UPLOAD_BYTES:
            raise UserError(
                _(
                    "File is %(size)s bytes, above the %(cap)s-byte upload " "limit.",
                    size=len(raw),
                    cap=MAX_UPLOAD_BYTES,
                )
            )

        values = {"name": name.strip(), "raw": raw, "type": "binary"}
        if mimetype:
            values["mimetype"] = mimetype
        if model is not None:
            model_rs = self._resolve_model(model)
            self._check_op(model, "write")
            record = self._browse_record_or_raise(model, model_rs, record_id)
            values.update({"res_model": model, "res_id": record.id})
        elif not utils.check_model_operation_allowed(
            self.env, "ir.attachment", "create"
        ):
            raise AccessError(
                _(
                    "Standalone attachment upload via MCP requires "
                    "'ir.attachment' to be MCP-enabled for create."
                )
            )

        # Runs as the calling user -> ir.attachment's own access check binds
        # (write access on the parent record for a linked upload).
        attachment = self.env["ir.attachment"].create(values)
        uri = build_attachment_uri(attachment.id)
        confirmation = _(
            "Uploaded attachment %(id)s (%(name)s, %(mimetype)s, %(size)s bytes)",
            id=attachment.id,
            name=attachment.name,
            mimetype=attachment.mimetype,
            size=attachment.file_size,
        )
        lines = [confirmation, _("URI: %s", uri)]
        if model is not None:
            lines.append(_("Attached to %(model)s/%(id)s", model=model, id=record.id))
        structured = {
            "attachment_id": attachment.id,
            "uri": uri,
            "name": attachment.name,
            "mimetype": attachment.mimetype,
            "file_size": attachment.file_size,
        }
        if model is not None:
            structured.update({"model": model, "record_id": record.id})
        return _tool_result("\n".join(lines), structured)

    # ------------------------------------------------------------------
    # post_message (-> message_post, gated as a write op)
    # ------------------------------------------------------------------
    @mcp_tool(
        name="post_message",
        title="Post Message",
        description=(
            "Post a message to a record's chatter (mail.thread). subtype 'note' "
            "(default) logs an internal note; 'comment' notifies followers. "
            "Gated as a write operation; runs as the calling user, so Odoo's "
            "access rights and record rules apply."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Technical model name (e.g. 'res.partner').",
                },
                "record_id": {
                    "type": "integer",
                    "description": "The record ID to post to.",
                },
                "body": {
                    "type": "string",
                    "description": (
                        "Message body (plain text by default; HTML when "
                        "body_is_html=True)."
                    ),
                },
                "subject": {
                    "type": "string",
                    "description": "Optional message subject.",
                },
                "subtype": {
                    "type": "string",
                    "enum": ["note", "comment"],
                    "description": (
                        "'note' (internal, default) or 'comment' (notifies "
                        "followers)."
                    ),
                },
                "message_type": {
                    "type": "string",
                    "enum": ["comment", "notification"],
                    "description": "Message type; defaults to 'comment'.",
                },
                "partner_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Optional res.partner IDs to additionally notify.",
                },
                "attachment_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Optional existing ir.attachment IDs to link.",
                },
                "body_is_html": {
                    "type": "boolean",
                    "description": "Treat body as HTML rather than plain text.",
                },
            },
            "required": ["model", "record_id", "body"],
            "additionalProperties": False,
        },
        operation="write",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
    @api.model
    def post_message(
        self,
        model,
        record_id,
        body,
        subject=None,
        subtype="note",
        message_type="comment",
        partner_ids=None,
        attachment_ids=None,
        body_is_html=False,
    ):
        """Post a chatter message as the calling user (gated as ``write``)."""
        model_rs = self._resolve_model(model)
        self._check_op(model, "write")

        if not isinstance(body, str) or not body.strip():
            raise UserError(_("No message body provided."))

        # Clean error (not a traceback) when the model has no chatter.
        if not hasattr(model_rs, "message_post"):
            raise UserError(
                _(
                    "Model '%s' does not support chatter "
                    "(no mail.thread inheritance).",
                    model,
                )
            )

        record = self._browse_record_or_raise(model, model_rs, record_id)

        subtype = subtype or "note"
        if isinstance(subtype, str):
            subtype = subtype.strip().lower()
        if not isinstance(subtype, str) or subtype not in _SUBTYPE_XMLID:
            raise UserError(
                _("Invalid subtype '%s'; expected 'note' or 'comment'.", subtype)
            )

        # Omit partner_ids/attachment_ids when None (empty list can mean
        # "clear all" in some chatter contexts).
        post_kwargs = {
            "body": body,
            "message_type": message_type,
            "subtype_xmlid": _SUBTYPE_XMLID[subtype],
        }
        if subject:
            post_kwargs["subject"] = subject
        if partner_ids is not None:
            post_kwargs["partner_ids"] = partner_ids
        if attachment_ids is not None:
            post_kwargs["attachment_ids"] = attachment_ids
        if body_is_html:
            # Odoo 17+ escapes a plain str body -- opt-in flag preserves HTML.
            post_kwargs["body_is_html"] = True

        # Runs as the calling user -> ORM enforces write ACLs / record rules.
        message_rec = record.message_post(**post_kwargs)

        confirmation = _(
            "Posted message %(message_id)s to %(model)s record with ID %(id)s",
            message_id=message_rec.id,
            model=model,
            id=record.id,
        )
        structured = {
            "success": True,
            "message_id": message_rec.id,
            "message": confirmation,
        }
        return _tool_result(confirmation, structured)

"""Read-side MCP tools.

Contributes the read tools to :class:`McpMixin` via ``_inherit = 'mcp.mixin'``:
``list_models``, ``get_record``, ``get_fields``, ``search_records``,
``aggregate_records``, ``read_attachment``, ``list_record_attachments``,
``list_resource_templates`` and ``get_current_context``.
The LLM-friendly text output reuses the
``tools.formatters`` / ``tools.uri_schema`` / ``tools.smart_fields`` helpers.

Every tool routes through the mixin chokepoints: :meth:`McpMixin._resolve_model`
applies the coarse model gate, then :meth:`McpMixin._check_op` applies the
per-operation (``read``) gate. The tools run as the calling user (``self.env``
is already user-scoped), so Odoo's ``ir.model.access`` and record
rules remain the real enforcement boundary.

**Tool-result shape.** Each tool returns a dict in MCP ``CallToolResult`` form::

    {"content": [{"type": "text", "text": <formatted text>}],
     "structuredContent": {<machine-readable result>}}

The ``/mcp`` controller's ``tools/call`` handler forwards this verbatim,
adding ``isError: false``.
"""

import ast
import base64
import json
import time
from datetime import date, datetime, timezone
from decimal import Decimal

from odoo import _, api, models
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.tools import ormcache

from ..controllers import utils
from ..controllers.download import download_token
from ..tools.formatters import DatasetFormatter, RecordFormatter
from ..tools.smart_fields import (
    DEFAULT_MAX_SMART_FIELDS,
    get_schema_default_fields,
    get_smart_default_fields,
    is_sensitive_field_name,
)
from ..tools.uri_schema import (
    URIParseError,
    build_attachment_uri,
    build_field_uri,
    parse_attachment_uri,
    parse_field_uri,
)
from .mcp_mixin import (
    _BINARY_FIELD_TYPES,
    MAX_EXTRACT_BYTES,
    MAX_INLINE_BLOB_BYTES,
    MAX_INLINE_TEXT_BYTES,
    MAX_INLINE_TEXT_CHARS,
    mcp_tool,
)

# Pagination fallback defaults. The live values come from the MCP settings
# (``mcp_server.default_limit`` / ``mcp_server.max_limit``); these apply only
# when the corresponding setting is unset.
DEFAULT_LIMIT = 25
MAX_LIMIT = 100

# Aggregate functions that need a specific field type. Core validates only the
# function name, so ``name:sum`` would reach SQL and fail there as an internal
# error. max and min take any column but a boolean one (PostgreSQL has no
# max(boolean)); count, array_agg, ... take any column.
_TYPED_AGGREGATES = {
    "sum": ("integer", "float", "monetary"),
    "avg": ("integer", "float", "monetary"),
    "bool_and": ("boolean",),
    "bool_or": ("boolean",),
}

# Cap on selection options per field in get_fields' curated default view; an
# explicit field_names request (or ["__all__"]) returns the full list.
_SELECTION_OPTIONS_CAP = 20

# Deep pagination is query-cost amplification: Postgres still walks (and discards)
# every skipped row, so a huge offset is expensive even though limit is capped.
# Bound the offset to this many max-size pages -- generous for real paging, but
# it stops an ``offset=999999999`` forcing an unbounded skip.
MAX_OFFSET_PAGES = 1000

# Fallback for the maximum number of related records rendered inline by
# ``get_record`` (live value: ``mcp_server.max_related_items``). Kept low: each
# shown collection costs one extra name-resolution read, and larger ones just
# collapse to a count plus a search hint.
DEFAULT_MAX_RELATED_ITEMS = 3

# Fallback for the characters of one text value shown by ``search_records``
# and by ``get_record`` without named fields (live value:
# ``mcp_server.max_field_length``). A field named in get_record's ``fields``
# shows up to MAX_INLINE_TEXT_CHARS instead.
DEFAULT_MAX_FIELD_LENGTH = RecordFormatter.MAX_FIELD_DISPLAY_LENGTH

# ``fields`` sentinel: explicit request for every field on the model.
_ALL_FIELDS_SENTINEL = "__all__"

# Computed fields that Odoo 17 refuses record by record, with the access that
# every record needs. ``signup_url`` (auth_signup) checks write access to
# res.users for a contact with an internal user, and to res.partner for one
# with a portal user. A probe of one record cannot see that, so the bulk reads
# leave such a field out for a caller without the access.
_RECORD_ACCESS_FIELDS = {
    "signup_url": (("res.users", "write"), ("res.partner", "write")),
}

# Validity of a ``read_attachment`` download link, in hours (live value:
# ``mcp_server.link_ttl_hours``). The link is a delegated-access bearer URL
# redeemed by core as sudo and cannot be revoked, so the default covers only
# the conversation that minted it (the model re-mints one when needed). Capped
# at 42 days -- this module's ceiling, mirroring the longest validity core gives
# its own auto-generated tokens (core does not bound an explicit expiry).
DEFAULT_LINK_TTL_HOURS = 1
MAX_LINK_TTL_HOURS = 42 * 24

# ``read_attachment`` output formats.
_ATTACHMENT_FORMATS = ("auto", "link", "blob")


def _append_skipped_fields_note(text, structured, skipped):
    """Name the fields a bulk read left out because the caller cannot read them."""
    if not skipped:
        return text
    structured["skipped_fields"] = skipped
    note = _("Fields you cannot read were left out: %s", ", ".join(skipped))
    return text + "\n\n" + note


def _append_binary_note(text, structured):
    """Add the ``read_attachment`` hint next to swapped ``odoo://`` binary URIs.

    Sibling key on purpose: ``get_record``'s ``metadata["note"]`` (smart
    fields) must survive. Returns the text with the hint appended.
    """
    note = _(
        "Binary fields are shown as odoo:// URIs. Pass a URI to "
        "read_attachment to get the content; list_record_attachments lists "
        "the files attached to a record."
    )
    structured["binary_note"] = note
    return text + "\n\n" + note


# Compact attribute set ``get_fields`` returns when the caller does not request
# specific attributes -- enough to discover a model's schema without the noise.
_CURATED_FIELD_ATTRIBUTES = [
    "type",
    "string",
    "required",
    "readonly",
    "relation",
    "selection",
]

# get_record / search_records need a few more attributes than get_fields exposes:
# smart-field scoring reads ``store``/``searchable``, and the formatter reads
# ``digits`` (float precision) and ``relation_field`` (a one2many's inverse, used
# to build the "view all" search_records hint). Restricting fields_get to this
# superset -- rather than requesting the full field description -- skips the
# costly per-field sortable/groupable/aggregator work while keeping selection +
# formatting behaviour identical.
_RECORD_FIELD_ATTRIBUTES = _CURATED_FIELD_ATTRIBUTES + [
    "store",
    "searchable",
    "digits",
    "relation_field",
]


def _tool_result(text, structured_content=None):
    """Build the shared MCP tool-result dict (see module docstring)."""
    result = {"content": [{"type": "text", "text": text}]}
    if structured_content is not None:
        result["structuredContent"] = structured_content
    return result


def _parse_list_arg(text, kind):
    """Parse a JSON / Python-literal list argument for a coercer.

    Tries JSON first, then a Python literal; raises ``UserError`` (message keyed
    by ``kind`` -- ``"domain"`` or ``"fields"``) when neither parses. Shared by
    :meth:`McpToolsRead._coerce_domain` and :meth:`McpToolsRead._coerce_fields`.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(text)
        except (ValueError, SyntaxError) as err:
            if kind == "domain":
                message = _("Invalid domain: expected a list, got %s", text[:100])
            else:
                message = _("Invalid fields: expected a list, got %s", text[:100])
            raise UserError(message) from err


class McpToolsRead(models.AbstractModel):
    """Read tools contributed to the ``mcp.mixin`` tool layer."""

    _inherit = "mcp.mixin"

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    def _mcp_int_config(self, key, default):
        """Read an integer tuning setting from the MCP configuration.

        sudo: ``ir.config_parameter`` reads are admin-only, but these are
        non-sensitive tuning values. Falls back to ``default`` when the
        parameter is unset or not an integer.
        """
        params = self.env["ir.config_parameter"].sudo()  # non-sensitive tuning param
        value = params.get_param(key, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _max_field_length(self):
        """Effective characters of one text value, from
        ``mcp_server.max_field_length``.

        A malformed / non-positive value falls back to the default; a larger
        one is clamped to ``MAX_INLINE_TEXT_CHARS``, the ceiling for one field.
        """
        length = self._mcp_int_config(
            "mcp_server.max_field_length", DEFAULT_MAX_FIELD_LENGTH
        )
        if length <= 0:
            length = DEFAULT_MAX_FIELD_LENGTH
        return min(length, MAX_INLINE_TEXT_CHARS)

    def _limit_bounds(self):
        """Return the effective ``(default, max)`` record limits from live config.

        Single source of truth for both :meth:`_effective_limit` and the
        advertised ``limit`` description, so the page size a fields-less call
        actually returns can never diverge from what ``tools/list`` promises.
        A misconfigured 0/negative falls back to the module defaults -- Odoo
        treats ``limit <= 0`` as "no limit", which would silently uncap a
        full-table fetch -- and the default is clamped to the max so the cap
        always wins (a Default set above the Maximum still returns at most
        Maximum rows).
        """
        default_limit = self._mcp_int_config("mcp_server.default_limit", DEFAULT_LIMIT)
        max_limit = self._mcp_int_config("mcp_server.max_limit", MAX_LIMIT)
        if default_limit <= 0:
            default_limit = DEFAULT_LIMIT
        if max_limit <= 0:
            max_limit = MAX_LIMIT
        return min(default_limit, max_limit), max_limit

    def _mcp_live_input_schema(self, schema):
        """Return ``schema`` with live limit values filled into its ``limit`` arg.

        The advertised ``limit`` description carries ``%(default)s``/``%(max)s``
        placeholders so ``tools/list`` can reflect the configured default/maximum
        record limits. Returns the schema unchanged when it has no such argument;
        otherwise returns a shallow copy (the cached schema is never mutated).
        """
        props = schema.get("properties", {})
        limit = props.get("limit")
        description = limit.get("description", "") if isinstance(limit, dict) else ""
        if "%(default)s" not in description:
            return schema
        default_limit, max_limit = self._limit_bounds()
        filled = description % {"default": default_limit, "max": max_limit}
        new_limit = {**limit, "description": filled}
        return {**schema, "properties": {**props, "limit": new_limit}}

    @staticmethod
    def _coerce_domain(domain):
        """Coerce a domain argument into an Odoo domain list.

        Accepts a list (passed through), a JSON / Python-literal string, or
        ``None`` (-> ``[]``). Tolerant parsing so LLM
        clients that stringify the domain still work.
        """
        if domain is None:
            return []
        if isinstance(domain, (list, tuple)):
            return list(domain)
        if isinstance(domain, str):
            text = domain.strip()
            if not text:
                return []
            parsed = _parse_list_arg(text, "domain")
            if not isinstance(parsed, (list, tuple)):
                raise UserError(_("Domain must be a list."))
            return list(parsed)
        raise UserError(_("Domain must be a list."))

    @staticmethod
    def _coerce_fields(fields):
        """Coerce a ``fields`` argument into a list, ``None`` or the sentinel.

        Accepts a list, a JSON / Python-literal string, or ``None``. Returns
        ``None`` for an absent/empty selection (caller applies smart defaults).
        """
        if fields is None:
            return None
        if isinstance(fields, str):
            text = fields.strip()
            if not text:
                return None
            fields = _parse_list_arg(text, "fields")
        if isinstance(fields, (list, tuple)):
            fields = list(fields)
            return fields or None
        raise UserError(_("Fields must be a list of field names."))

    def _resolve_fields(self, fields, fields_metadata):
        """Decide which fields to read and how the selection was made.

        :return: ``(selection_method, fields_to_read)`` where ``selection_method``
            is one of ``smart_defaults``/``all``/``explicit`` and
            ``fields_to_read`` is a list (or ``None`` to read all fields).
        """
        fields = self._coerce_fields(fields)
        if fields is None:
            max_fields = self._mcp_int_config(
                "mcp_server.max_smart_fields", DEFAULT_MAX_SMART_FIELDS
            )
            return "smart_defaults", get_smart_default_fields(
                fields_metadata, max_fields=max_fields
            )
        if fields == [_ALL_FIELDS_SENTINEL]:
            return "all", None
        return "explicit", fields

    def _effective_limit(self, limit):
        """Apply the default/cap policy to a requested ``limit`` (live config)."""
        # ``_limit_bounds`` already applies the 0/negative fallback and clamps
        # the default down to the max, so the no-limit path below stays capped.
        default_limit, max_limit = self._limit_bounds()
        if not limit:  # None / 0 / "" -> use the (already max-clamped) default
            return default_limit
        # Coerce before comparing: a client may send a string limit, and
        # ``"abc" <= 0`` would raise a raw TypeError. A non-integer is a clean
        # client error (surfaced as an isError result), not a -32602.
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            raise UserError(_("'limit' must be an integer.")) from None
        if limit <= 0:
            return default_limit
        return min(limit, max_limit)

    def _effective_offset(self, offset):
        """Coerce and upper-bound a requested pagination ``offset`` (live config).

        Mirrors ``_effective_limit``'s coercion -- a non-integer offset is a clean
        client error (surfaced as an isError result), not a raw -32602 -- then
        caps the offset at ``MAX_OFFSET_PAGES`` max-size pages so a caller cannot
        force Postgres to skip an unbounded row count (query-cost amplification).
        """
        try:
            offset = max(0, int(offset or 0))
        except (TypeError, ValueError):
            raise UserError(_("'offset' must be an integer.")) from None
        _default_limit, max_limit = self._limit_bounds()
        return min(offset, max_limit * MAX_OFFSET_PAGES)

    @staticmethod
    def _strip_sensitive_fields(record):
        """Drop credential-named fields from a read result in place.

        Applied only on the *bulk* read paths -- the smart-default selection
        (which already scores these to 0) and the ``["__all__"]`` sentinel -- so a
        field named like a credential (``*_api_key``, ``*password``,
        ``webhook_secret`` ...) is not surfaced by a caller that did not ask for
        it by name. An explicitly-named field is honored (never stripped): Odoo
        field-level ``groups=`` is the real ACL there, this is best-effort
        defense in depth. Mutates ``record`` in place; returns nothing.
        """
        for name in [name for name in record if is_sensitive_field_name(name)]:
            del record[name]

    @staticmethod
    def _binary_field_names(fields_metadata):
        """Names of binary/image fields in ``fields_metadata``."""
        return {
            name
            for name, meta in fields_metadata.items()
            if (meta or {}).get("type") in _BINARY_FIELD_TYPES
        }

    def _search_read_bulk(
        self, model_rs, domain, names, *, offset, limit, order, skip_unreadable
    ):
        """``search_read`` that can leave out the fields the caller cannot read.

        Returns ``(rows, skipped)``. Only bulk selections skip fields; an
        explicit field list keeps failing with the access error.
        """
        known = (
            self._unreadable_fields(model_rs._name, tuple(names))
            if skip_unreadable
            else ()
        )
        try:
            rows = model_rs.search_read(
                domain,
                [name for name in names if name not in known] or names,
                offset=offset,
                limit=limit,
                order=order,
            )
            return rows, list(known)
        except AccessError:
            probe = model_rs.search(domain, offset=offset, limit=1, order=order)
            if not (skip_unreadable and probe):
                raise
            readable, skipped = self._drop_unreadable_fields(probe, names)
            if not readable:
                raise
        rows = model_rs.search_read(
            domain, readable, offset=offset, limit=limit, order=order
        )
        return rows, skipped

    @api.model
    @ormcache(
        "self.env.uid", "self.env.su", "tuple(self.env.companies.ids)", "model", "names"
    )
    def _unreadable_fields(self, model, names):
        """Probe one record of ``model`` for the unreadable fields of ``names``.

        Lets the bulk reads leave such fields out without a failed read and a
        probe on every call. Cached per user and companies; changes to access
        rights and to groups clear the cache. A model without
        records yields no field, and a read that still fails falls back to the
        uncached probe. A field of ``_RECORD_ACCESS_FIELDS`` is unreadable when
        the caller lacks its access.
        """
        refused = tuple(
            name
            for name in names
            if any(
                not self.env[comodel].check_access_rights(mode, raise_exception=False)
                for comodel, mode in _RECORD_ACCESS_FIELDS.get(name, ())
            )
        )
        names = [name for name in names if name not in refused]
        probe = (
            self.env[model]
            .with_context(active_test=False, bin_size=True)
            .search([], limit=1)
        )
        if not (probe and names):
            return refused
        try:
            probe.read(names)
        except AccessError:
            return refused + tuple(self._drop_unreadable_fields(probe, names)[1])
        return refused

    @staticmethod
    def _drop_unreadable_fields(probe, names):
        """Split ``names`` into the fields ``probe`` can be read with and the rest.

        A computed field may read a model the caller cannot access (for example
        the accounting totals on a contact) and then fails the whole read. The
        bulk selections leave such fields out instead. One record is probed: the
        failure comes from model access, not from the record.
        """
        readable, skipped = [], []
        for name in names:
            try:
                probe.read([name])
            except AccessError:
                skipped.append(name)
            else:
                readable.append(name)
        return readable, skipped

    # ------------------------------------------------------------------
    # list_models
    # ------------------------------------------------------------------
    @mcp_tool(
        name="list_models",
        title="List Models",
        description=(
            "List all models enabled for MCP access with their allowed "
            "operations (read, create, write, unlink)."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    @api.model
    def list_models(self):
        """Return the MCP-enabled models with their allowed operations."""
        # get_enabled_models already returns dicts with model/name/operations.
        enriched = utils.get_enabled_models(self.env)

        lines = [
            "=" * 60,
            _("MCP-enabled models (%s)", len(enriched)),
            "=" * 60,
        ]
        if not enriched:
            lines.append(_("No models are currently enabled for MCP access."))
        for entry in enriched:
            ops = entry["operations"] or {}
            allowed = ", ".join(
                op for op in ("read", "create", "write", "unlink") if ops.get(op)
            )
            ops_label = allowed or _("no operations")
            lines.append(f"- {entry['name']} ({entry['model']}) [{ops_label}]")

        return _tool_result("\n".join(lines), {"models": enriched})

    # ------------------------------------------------------------------
    # get_record
    # ------------------------------------------------------------------
    @mcp_tool(
        name="get_record",
        title="Get Record",
        description=(
            "Get a single record by ID with smart field selection. Omit "
            "'fields' for a smart default selection, pass a list of field "
            'names for specific fields, or ["__all__"] for every field. '
            "To fetch several records, do not call this tool repeatedly: "
            "use search_records with a domain like "
            '[["id", "in", [1, 2, 3]]] in one call. Long text values are cut. '
            "Name the field in 'fields' "
            '(not ["__all__"]) to get up to 100,000 characters of it.'
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
                    "description": "The record ID to retrieve.",
                },
                "fields": {
                    # A JSON string of the list is tolerated (_coerce_fields).
                    "type": ["array", "string", "null"],
                    "items": {"type": "string"},
                    "description": (
                        "Field selection: omit/null for smart defaults, a list "
                        'of field names, or ["__all__"] for all fields.'
                    ),
                },
            },
            "required": ["model", "record_id"],
            "additionalProperties": False,
        },
        operation="read",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    @api.model
    def get_record(self, model, record_id, fields=None):
        """Read one record, formatting it for LLM consumption."""
        model_rs = self._resolve_model(model)
        self._check_op(model, "read")

        fields_metadata = model_rs.fields_get(attributes=_RECORD_FIELD_ATTRIBUTES)
        selection_method, fields_to_read = self._resolve_fields(fields, fields_metadata)

        record_rs = self._browse_record_or_raise(model, model_rs, record_id)

        # Runs as the calling user -> ORM enforces read ACLs / record rules.
        # bin_size: binary fields are only ever turned into odoo:// URIs below
        # (never returned inline), so load their size placeholder rather than the
        # full base64 blob -- the bytes are re-fetched separately on resources/read.
        reader = record_rs.with_context(bin_size=True)
        names = list(fields_metadata) if fields_to_read is None else fields_to_read
        skipped = []
        if selection_method != "explicit":
            skipped = list(self._unreadable_fields(model_rs._name, tuple(names)))
            names = [name for name in names if name not in skipped] or names
        try:
            data = reader.read(names)[0]
        except AccessError:
            if selection_method == "explicit":
                raise
            names, more = self._drop_unreadable_fields(reader, names)
            skipped += [name for name in more if name not in skipped]
            if not names:
                raise
            data = reader.read(names)[0]
        # Guard the bulk paths only (smart defaults + the ``["__all__"]``
        # sentinel); an explicit field list is honored -- see
        # _strip_sensitive_fields.
        if selection_method != "explicit":
            self._strip_sensitive_fields(data)
        render_metadata = self._replace_binaries(
            model, record_rs.id, data, fields_metadata
        )

        max_related_items = self._mcp_int_config(
            "mcp_server.max_related_items", DEFAULT_MAX_RELATED_ITEMS
        )
        related_summaries = self._resolve_related_summaries(
            data, fields_metadata, max_related_items
        )
        enabled_relations = self._enabled_relation_models(data, fields_metadata)
        # A field named in ``fields`` is shown up to the per-field ceiling; the
        # bulk paths keep the configured cap, whose marker points back here.
        if selection_method == "explicit":
            cap = MAX_INLINE_TEXT_CHARS
        else:
            cap = self._max_field_length()
        text = RecordFormatter(
            model,
            max_field_length=cap,
            full_value_hint=cap < MAX_INLINE_TEXT_CHARS,
            full_value_limit=MAX_INLINE_TEXT_CHARS,
        ).format_record(
            data,
            render_metadata,
            related_summaries=related_summaries,
            enabled_relations=enabled_relations,
        )

        structured = {"record": data}
        if selection_method == "smart_defaults":
            structured["metadata"] = {
                "fields_returned": len(data),
                "field_selection_method": selection_method,
                "total_fields_available": len(fields_metadata),
                "note": _(
                    'Smart field selection used. Pass fields=["__all__"] for '
                    "every field, or a list of field names for a specific subset."
                ),
            }
        # A swapped binary re-types its metadata (see _replace_binaries), so
        # an untouched mapping means no URI was emitted.
        if render_metadata is not fields_metadata:
            text = _append_binary_note(text, structured)
        text = _append_skipped_fields_note(text, structured, skipped)
        return _tool_result(text, structured)

    def _replace_binaries(self, model, record_id, data, fields_metadata):
        """Swap populated binary values for ``odoo://`` URIs (in place).

        Returns the field metadata to hand the formatter: a shallow copy with
        the replaced fields re-typed to ``char`` so the URI string renders
        plainly instead of the generic ``[Binary data]`` placeholder. Untouched
        when the record carries no populated binary field.
        """
        render_metadata = fields_metadata
        for name in self._binary_field_names(fields_metadata):
            if data.get(name):
                data[name] = build_field_uri(model, record_id, name)
                if render_metadata is fields_metadata:
                    render_metadata = dict(fields_metadata)
                render_metadata[name] = {**fields_metadata[name], "type": "char"}
        return render_metadata

    def _resolve_related_summaries(self, data, fields_metadata, max_related_items):
        """Resolve display names for small x2many collections (inline preview).

        For each one2many/many2many field in ``data`` holding between 1 and
        ``max_related_items`` ids, read the related records' ``display_name``
        under the calling user's env (so record rules apply) and return
        ``{field_name: [(id, name), ...]}`` for the formatter to list inline.
        A field the caller cannot read is omitted -- the formatter falls back to
        the count plus a search hint. Larger collections are skipped so the read
        stays cheap and the output short.

        The relation is first gated on the same per-model opt-in as
        ``search_records`` (model enabled + ``read`` allowed): an inline preview
        must never expose display names from a model the admin did not expose via
        MCP, even when the caller's Odoo ACL alone would permit the read.
        """
        summaries = {}
        if max_related_items <= 0:
            return summaries
        for name, value in data.items():
            meta = fields_metadata.get(name) or {}
            if meta.get("type") not in ("one2many", "many2many"):
                continue
            relation = meta.get("relation")
            if not relation or not isinstance(value, list):
                continue
            if not 0 < len(value) <= max_related_items:
                continue
            if not self._relation_mcp_read_enabled(relation):
                continue
            try:
                related = self.env[relation].browse(value).read(["display_name"])
            except (AccessError, MissingError):
                continue
            summaries[name] = [
                (rec["id"], rec.get("display_name") or f"id {rec['id']}")
                for rec in related
            ]
        return summaries

    def _enabled_relation_models(self, data, fields_metadata):
        """Return the x2many target models in ``data`` that are MCP-read-enabled.

        The formatter uses this to decide whether a collapsed relation may offer
        a ``search_records`` "view all" hint: a model that is not exposed via MCP
        would only error, so no hint is advertised for it. Mirrors the gate
        ``search_records`` itself applies (model enabled + ``read`` allowed).
        """
        enabled = set()
        for name, value in data.items():
            meta = fields_metadata.get(name) or {}
            if meta.get("type") not in ("one2many", "many2many"):
                continue
            relation = meta.get("relation")
            if not relation or relation in enabled or not value:
                continue
            if not self._relation_mcp_read_enabled(relation):
                continue
            enabled.add(relation)
        return enabled

    def _relation_mcp_read_enabled(self, relation):
        """Whether ``relation`` is exposed for MCP read (enabled + ``read`` op).

        Mirrors the gate ``search_records`` applies. Shared by the inline x2many
        preview (``_related_summaries``) and the "view all" hint gate
        (``_enabled_relation_models``): a model not exposed via MCP would only
        error, so neither touches it.
        """
        try:
            self._resolve_model(relation)
            self._check_op(relation, "read")
        except (AccessError, UserError):
            return False
        return True

    # ------------------------------------------------------------------
    # get_fields
    # ------------------------------------------------------------------
    @mcp_tool(
        name="get_fields",
        title="Get Fields",
        description=(
            "Describe a model's fields: type, label, required/readonly, "
            "relation target, and selection options. Use it to discover a "
            "model's schema before reading or writing records. Returns the "
            "most relevant fields by default; pass 'field_names' for specific "
            'fields or ["__all__"] for the complete schema.'
        ),
        input_schema={
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Technical model name (e.g. 'res.partner').",
                },
                "field_names": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": (
                        "Restrict the result to these field names, or "
                        '["__all__"] for every field on the model. Omit/null '
                        "for a curated selection of the most relevant fields."
                    ),
                },
                "attributes": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": (
                        "Which field attributes to return. Omit/null for the "
                        "curated default set (type, string, required, readonly, "
                        "relation, selection). Pass an explicit list to request "
                        "more, e.g. help or store."
                    ),
                },
            },
            "required": ["model"],
            "additionalProperties": False,
        },
        operation="read",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    @api.model
    def get_fields(self, model, field_names=None, attributes=None):
        """Describe a model's fields for schema discovery."""
        model_rs = self._resolve_model(model)
        self._check_op(model, "read")

        selected_attributes = attributes or _CURATED_FIELD_ATTRIBUTES
        show_all = bool(field_names) and "__all__" in field_names
        explicit_names = field_names if field_names and not show_all else None
        # Fetch 'store' internally even when not requested: the schema-relevance
        # scoring needs it to demote computed fields; stripped again below.
        internal_attributes = set(selected_attributes) | {"store"}
        # Runs as the calling user (no sudo) -> field-level group ACLs apply.
        fields_metadata = model_rs.fields_get(
            explicit_names, attributes=list(internal_attributes)
        )

        omitted = 0
        if not explicit_names and not show_all:
            keep = set(get_schema_default_fields(fields_metadata))
            omitted = len(fields_metadata) - len(keep)
            if omitted:
                fields_metadata = {
                    fname: meta
                    for fname, meta in fields_metadata.items()
                    if fname in keep
                }
            # Cap giant selection lists (res.partner's tz alone has ~500
            # options) in the curated view; naming the field or passing
            # ["__all__"] returns the full list.
            for meta in fields_metadata.values():
                selection = meta.get("selection")
                if selection and len(selection) > _SELECTION_OPTIONS_CAP:
                    meta["selection_more"] = len(selection) - _SELECTION_OPTIONS_CAP
                    meta["selection"] = selection[:_SELECTION_OPTIONS_CAP]
        if "store" not in selected_attributes:
            for meta in fields_metadata.values():
                meta.pop("store", None)

        fields = [
            {"name": fname, **meta} for fname, meta in sorted(fields_metadata.items())
        ]
        structured = {"model": model, "fields": fields, "total": len(fields)}
        if omitted:
            structured["omitted"] = omitted
        return _tool_result(
            self._format_fields_text(model, fields, omitted=omitted), structured
        )

    @staticmethod
    def _format_fields_text(model, fields, omitted=0):
        """Render field definitions as concise, LLM-friendly text."""
        count = (
            _("%(shown)s of %(total)s", shown=len(fields), total=len(fields) + omitted)
            if omitted
            else str(len(fields))
        )
        lines = [
            "=" * 60,
            _("Fields: %(model)s (%(count)s)", model=model, count=count),
            "=" * 60,
        ]
        for field in fields:
            ftype = field.get("type") or "?"
            relation = field.get("relation")
            type_part = f"{ftype}→{relation}" if relation else ftype
            line = f"{field['name']} ({type_part})"
            label = field.get("string")
            if label:
                line += f" — {label}"
            flags = []
            if field.get("required"):
                flags.append(_("required"))
            if field.get("readonly"):
                flags.append(_("readonly"))
            if flags:
                line += f" [{', '.join(flags)}]"
            selection = field.get("selection")
            if selection:
                line += ": " + ", ".join(str(option[0]) for option in selection)
                more = field.get("selection_more")
                if more:
                    line += _(", ... +%(count)s more options", count=more)
            lines.append(line)
        if omitted:
            lines.append(
                _(
                    "+ %(count)s more fields not shown. Pass field_names=[...] "
                    'for specific fields or ["__all__"] for the complete '
                    "schema.",
                    count=omitted,
                )
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # search_records
    # ------------------------------------------------------------------
    @mcp_tool(
        name="search_records",
        title="Search Records",
        description=(
            "Search for records in a model. Returns a smart field selection by "
            "default with pagination. Use 'domain' to filter, 'fields' to pick "
            "columns, and 'limit'/'offset'/'order' to page and sort. Also the "
            "efficient way to read many records at once: one call with "
            '[["id", "in", ids]] or [["name", "in", names]] replaces repeated '
            "get_record calls."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Technical model name (e.g. 'res.partner').",
                },
                "domain": {
                    "type": ["array", "string", "null"],
                    "description": (
                        "Odoo domain filter as a list (e.g. "
                        '[["is_company", "=", true]]) or JSON string. '
                        "Omit for all records."
                    ),
                },
                "fields": {
                    # A JSON string of the list is tolerated (_coerce_fields).
                    "type": ["array", "string", "null"],
                    "items": {"type": "string"},
                    "description": (
                        "Field selection: omit/null for smart defaults, a list "
                        'of field names, or ["__all__"] for all fields.'
                    ),
                },
                "limit": {
                    "type": ["integer", "null"],
                    # Live default/max filled in at tools/list serve time.
                    "description": (
                        "Max records to return. Defaults to %(default)s, capped "
                        "at %(max)s."
                    ),
                },
                "offset": {
                    "type": "integer",
                    "default": 0,
                    "description": "Number of records to skip (pagination).",
                },
                "order": {
                    "type": ["string", "null"],
                    "description": "Sort order, e.g. 'name asc'.",
                },
            },
            "required": ["model"],
            "additionalProperties": False,
        },
        operation="read",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
    @api.model
    def search_records(
        self, model, domain=None, fields=None, limit=None, offset=0, order=None
    ):
        """Search records and format the page for LLM consumption."""
        model_rs = self._resolve_model(model)
        self._check_op(model, "read")

        parsed_domain = self._coerce_domain(domain)
        limit = self._effective_limit(limit)
        offset = self._effective_offset(offset)

        fields_metadata = model_rs.fields_get(attributes=_RECORD_FIELD_ATTRIBUTES)
        selection_method, fields_to_read = self._resolve_fields(fields, fields_metadata)

        # bin_size: any binary field is replaced by an odoo:// URI below, so load
        # size placeholders instead of full blobs (the bytes are re-fetched on
        # resources/read). Only affects binary fields; others read normally.
        rows, skipped = self._search_read_bulk(
            model_rs.with_context(bin_size=True),
            parsed_domain,
            list(fields_metadata) if fields_to_read is None else fields_to_read,
            offset=offset,
            limit=limit,
            order=order or None,
            skip_unreadable=selection_method != "explicit",
        )
        # Guard the bulk paths only (smart defaults + the ``["__all__"]``
        # sentinel); an explicit field list is honored -- see
        # _strip_sensitive_fields.
        if selection_method != "explicit":
            for row in rows:
                self._strip_sensitive_fields(row)

        # Skip the extra count query when the page already reveals the total: a
        # first page that came back short of its limit holds every match.
        # Otherwise an exact count is needed for the pagination math.
        if offset == 0 and (not limit or len(rows) < limit):
            total = len(rows)
        else:
            total = model_rs.search_count(parsed_domain)

        binary_names = self._binary_field_names(fields_metadata)
        binary_swapped = False
        if binary_names:
            for row in rows:
                row_id = row.get("id")
                for name in binary_names:
                    if row_id and row.get(name):
                        row[name] = build_field_uri(model, row_id, name)
                        binary_swapped = True

        total_pages = (total + limit - 1) // limit if limit else 1
        current_page = (offset // limit) + 1 if limit else 1
        next_hint = prev_hint = None
        if offset + len(rows) < total:
            next_hint = _(
                "search_records with offset=%(offset)s, limit=%(limit)s",
                offset=offset + limit,
                limit=limit,
            )
            max_limit = self._limit_bounds()[1]
            if limit and limit < max_limit:
                # Steer agents toward fewer, larger pages instead of looping
                # through many small ones at the current limit.
                next_hint += _(
                    " (or raise limit up to %(max)s to fetch more per call)",
                    max=max_limit,
                )
        if offset > 0:
            prev_hint = _(
                "search_records with offset=%(offset)s, limit=%(limit)s",
                offset=max(0, offset - limit),
                limit=limit,
            )

        # One page holds many rows, so named fields keep the configured cap too.
        cap = self._max_field_length()
        text = DatasetFormatter(
            model,
            max_field_length=cap,
            full_value_hint=cap < MAX_INLINE_TEXT_CHARS,
            full_value_limit=MAX_INLINE_TEXT_CHARS,
        ).format_search_results(
            rows,
            domain=parsed_domain or None,
            fields=fields_to_read,
            limit=limit,
            offset=offset,
            total_count=total,
            fields_metadata=fields_metadata,
            next_hint=next_hint,
            prev_hint=prev_hint,
            current_page=current_page,
            total_pages=total_pages,
        )

        structured = {
            "records": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
            "model": model,
        }
        if binary_swapped:
            text = _append_binary_note(text, structured)
        text = _append_skipped_fields_note(text, structured, skipped)
        return _tool_result(text, structured)

    # ------------------------------------------------------------------
    # aggregate_records
    # ------------------------------------------------------------------
    @api.model
    def _check_aggregate_types(self, model_rs, aggregates):
        """Refuse an aggregate whose function does not fit the field type.

        Unknown fields and functions are left to core, which rejects them with a
        clear ``ValueError``.
        """
        for spec in aggregates:
            if not isinstance(spec, str):
                continue
            fname, _sep, func = spec.partition(":")
            field = model_rs._fields.get(fname)
            if field is not None and func in ("max", "min") and field.type == "boolean":
                raise UserError(
                    _(
                        "Aggregate '%(spec)s' cannot use %(func)s on the boolean "
                        "field '%(field)s'. Use bool_or or bool_and.",
                        spec=spec,
                        func=func,
                        field=fname,
                    )
                )
            allowed_types = _TYPED_AGGREGATES.get(func)
            if field is not None and allowed_types and field.type not in allowed_types:
                raise UserError(
                    _(
                        "Aggregate '%(spec)s' needs a field of type %(types)s; "
                        "'%(field)s' is a %(type)s field.",
                        spec=spec,
                        types=" or ".join(allowed_types),
                        field=fname,
                        type=field.type,
                    )
                )

    @mcp_tool(
        name="aggregate_records",
        title="Aggregate Records",
        description=(
            "Aggregate records server-side (totals/counts/groupings) via Odoo's "
            "grouping engine. Use this instead of search_records when the "
            "question is about quantities per group rather than a list of rows."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Technical model name (e.g. 'sale.order').",
                },
                "groupby": {
                    # A bare string is one group expression (see below).
                    "type": ["array", "string"],
                    "items": {"type": "string"},
                    "description": (
                        "Group expressions. Field names, optionally with a "
                        "date/datetime granularity suffix, e.g. "
                        '["date_order:month"], ["partner_id"].'
                    ),
                },
                "aggregates": {
                    # A bare string is one aggregate expression (see below).
                    "type": ["array", "string", "null"],
                    "items": {"type": "string"},
                    "description": (
                        'Aggregate expressions "field:operator" (e.g. '
                        '["amount_total:sum"]). Defaults to ["__count"].'
                    ),
                },
                "domain": {
                    "type": ["array", "string", "null"],
                    "description": "Odoo domain filter as a list or JSON string.",
                },
                "order": {
                    "type": ["string", "null"],
                    "description": "Sort over groupby keys / aggregates.",
                },
                "limit": {
                    "type": ["integer", "null"],
                    # Live default/max filled in at tools/list serve time.
                    "description": (
                        "Max number of groups. Defaults to %(default)s, capped "
                        "at %(max)s."
                    ),
                },
                "offset": {
                    "type": "integer",
                    "default": 0,
                    "description": "Number of groups to skip.",
                },
            },
            "required": ["model", "groupby"],
            "additionalProperties": False,
        },
        operation="read",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
    @api.model
    def aggregate_records(
        self,
        model,
        groupby,
        aggregates=None,
        domain=None,
        order=None,
        limit=None,
        offset=0,
    ):
        """Group records server-side via ``_read_group``."""
        model_rs = self._resolve_model(model)
        self._check_op(model, "read")

        if not groupby:
            raise UserError(
                _("groupby must not be empty (use search_records for a flat list).")
            )
        if isinstance(groupby, str):
            groupby = [groupby]

        # Coerce a bare string just like ``groupby`` above: a client may send
        # ``"amount:sum"`` instead of ``["amount:sum"]``; without this
        # ``list(...)`` would explode it into a list of characters.
        if isinstance(aggregates, str):
            aggregates = [aggregates]

        parsed_domain = self._coerce_domain(domain)
        limit = self._effective_limit(limit)
        offset = self._effective_offset(offset)
        effective_aggregates = list(aggregates) if aggregates else ["__count"]
        self._check_aggregate_types(model_rs, effective_aggregates)

        # Runs as the calling user -> raises AccessError when read is not
        # permitted. _read_group offers no cheap "count of groups", so peek
        # one group past the page (limit+1): if it comes back, the result is
        # truncated and we signal has_more rather than pass off a partial "top
        # N" as complete. Mirrors search_records' next_hint.
        # v19's formatted_read_group is a web-addon wrapper over _read_group;
        # on v18 we call _read_group directly (identical signature) and shape
        # the rows ourselves. Mirror the wrapper's input normalization:
        # ':recordset' aggregates run as ':array_agg', and an empty order
        # defaults to the groupby specs.
        rows = model_rs._read_group(
            parsed_domain,
            groupby=list(groupby),
            aggregates=[
                agg.replace(":recordset", ":array_agg") for agg in effective_aggregates
            ],
            offset=offset,
            limit=limit + 1 if limit else None,
            order=order or ", ".join(groupby),
        )
        groups = self._format_read_group_rows(list(groupby), effective_aggregates, rows)
        has_more = bool(limit) and len(groups) > limit
        if has_more:
            groups = groups[:limit]

        # Drop the grouping engine's internal per-group keys (e.g.
        # ``__extra_domain``, ``__fold``) so they never reach the client. The
        # requested aggregates are kept even when ``__``-prefixed (the default
        # ``__count`` is the count value the caller asked for).
        cleaned_groups = [
            {
                key: value
                for key, value in group.items()
                if not key.startswith("__") or key in effective_aggregates
            }
            for group in groups
        ]

        next_hint = None
        if has_more:
            next_hint = _(
                "aggregate_records with offset=%(offset)s, limit=%(limit)s",
                offset=offset + limit,
                limit=limit,
            )

        text = self._format_aggregate_text(
            model, list(groupby), effective_aggregates, cleaned_groups, next_hint
        )
        structured = {
            "groups": cleaned_groups,
            "model": model,
            "groupby": list(groupby),
            "aggregates": effective_aggregates,
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
        }
        return _tool_result(text, structured)

    @staticmethod
    def _format_read_group_rows(groupby, aggregates, rows):
        """Shape raw ``_read_group`` tuples like v19 ``formatted_read_group``.

        ``_read_group`` returns one tuple per group: the groupby values first
        (recordsets for relational fields, raw date/datetime for granularity
        buckets), then the aggregate values in spec order. Rebuild the
        ``{spec: value}`` dicts the tool's structured output promises:
        relational values become ``(id, display_name)`` pairs (``False`` when
        unset, display_name read under sudo exactly like the v19 formatter),
        and date/datetime buckets are string-coerced so the payload stays
        JSON-serializable (v19 additionally emits a babel-localized label;
        on v18 the tool returns the raw bucket start instead).
        """
        groups = []
        for row in rows:
            group = {}
            for spec, value in zip(groupby, row[: len(groupby)]):
                if isinstance(value, models.BaseModel):
                    # Mirrors core v19 _web_read_group_format: sudo covers the
                    # group-label display_name only; row access was already
                    # enforced by the non-su _read_group above.
                    if value:
                        value = (value.id, value.sudo().display_name)  # sudo: label
                    else:
                        value = False
                elif isinstance(value, (date, datetime)):
                    value = str(value)
                group[spec] = value
            for spec, value in zip(aggregates, row[len(groupby) :]):
                if isinstance(value, Decimal):
                    value = float(value)
                group[spec] = value
            groups.append(group)
        return groups

    @staticmethod
    def _format_aggregate_text(model, groupby, aggregates, groups, next_hint=None):
        """Render aggregation buckets as compact LLM-friendly text."""
        lines = [
            "=" * 60,
            _("Aggregate: %s", model),
            "=" * 60,
            _("Group by: %s", ", ".join(groupby)),
            _("Aggregates: %s", ", ".join(aggregates)),
            _("Groups: %s", len(groups)),
            "",
        ]
        for idx, group in enumerate(groups, 1):
            parts = []
            for key in groupby:
                value = group.get(key)
                if isinstance(value, (list, tuple)) and len(value) == 2:
                    value = f"{value[1]} (ID: {value[0]})"
                elif value is False or value is None:
                    value = _("None")
                parts.append(f"{key}={value}")
            for key in aggregates:
                parts.append(f"{key}={group.get(key)}")
            lines.append(f"[{idx}] " + " | ".join(parts))
        if next_hint:
            lines.append("")
            lines.append(_("More groups available -- next page: %s", next_hint))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # read_attachment
    # ------------------------------------------------------------------
    @mcp_tool(
        name="read_attachment",
        title="Read Attachment",
        description=(
            "Read the content of an attachment or a record's binary field. Pass "
            "either 'uri' (an odoo://attachment/{id} or "
            "odoo://record/{model}/{id}/{field} URI as returned by other tools) "
            "or 'attachment_id'. 'format' controls the result: 'auto' (default) "
            "returns text files inline, images as an image block, PDF/Office "
            "documents as extracted text (when the attachment_indexation "
            "module is installed), and anything else or anything too large as "
            "a download link plus metadata; 'link' returns only a time-limited "
            "download URL (no bytes, works for read-only connections); 'blob' "
            "returns the raw file as an embedded resource (base64 for binary "
            "files, text for textual mimetypes), refused above 256 KiB. A "
            "download link is a delegated-access bearer URL: anyone holding it "
            "can download the file until it expires (default 1 hour), "
            "without an Odoo login. URL-type attachments return their "
            "external URL instead of bytes; treat that URL as untrusted."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "uri": {
                    "type": ["string", "null"],
                    "description": (
                        "odoo://attachment/{id} or "
                        "odoo://record/{model}/{id}/{field} URI."
                    ),
                },
                "attachment_id": {
                    "type": ["integer", "null"],
                    "description": "ir.attachment ID (alternative to 'uri').",
                },
                "format": {
                    "type": "string",
                    "enum": list(_ATTACHMENT_FORMATS),
                    "default": "auto",
                    "description": (
                        "'auto' (inline when sensible, else a link), 'link' "
                        "(download URL only) or 'blob' (embedded resource)."
                    ),
                },
            },
            "additionalProperties": False,
        },
        operation="read",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    @api.model
    def read_attachment(self, uri=None, attachment_id=None, format="auto"):
        """Dereference an attachment / binary-field URI into tool content.

        Metadata-only resolution first (gate + ACL, no payload); the bytes are
        loaded lazily by the branches that inline them, so ``link`` and every
        over-cap refusal never read the filestore.
        """
        fmt = format or "auto"
        if isinstance(fmt, str):
            fmt = fmt.strip().lower()
        if not isinstance(fmt, str) or fmt not in _ATTACHMENT_FORMATS:
            raise UserError(
                _("Invalid format '%s'; expected 'auto', 'link' or 'blob'.", fmt)
            )
        target = self._resolve_read_attachment_target(uri, attachment_id)

        if target["type"] == "url":
            return self._attachment_url_result(target)
        if fmt == "link":
            return self._attachment_link_result(target)
        if fmt == "blob":
            return self._attachment_blob_result(target)
        return self._attachment_auto_result(target)

    def _resolve_read_attachment_target(self, uri, attachment_id):
        """Validate the ``uri`` XOR ``attachment_id`` input and resolve it."""
        if (uri is None) == (attachment_id is None):
            raise UserError(_("Pass exactly one of 'uri' or 'attachment_id'."))
        if attachment_id is not None:
            try:
                attachment_id = self._coerce_record_id(attachment_id)
            except (TypeError, ValueError) as err:
                raise UserError(_("'attachment_id' must be an integer.")) from err
            return self._resolve_attachment_for_tool(
                build_attachment_uri(attachment_id), attachment_id
            )

        if not isinstance(uri, str) or not uri.startswith("odoo://"):
            raise UserError(_("Invalid resource URI: %s", uri))
        try:
            ref = parse_field_uri(uri)
        except URIParseError:
            ref = None
        if ref is not None:
            return self._resolve_record_field_for_tool(uri, ref)
        try:
            parsed_id = parse_attachment_uri(uri)
        except URIParseError as err:
            raise UserError(_("Unsupported resource URI: %s", uri)) from err
        return self._resolve_attachment_for_tool(uri, parsed_id)

    # -- result builders ------------------------------------------------
    @staticmethod
    def _attachment_structured(target, format_used, **extra):
        """``structuredContent`` shared by every ``read_attachment`` result."""
        structured = {
            "name": target["name"],
            "mimetype": target["mimetype"],
            "file_size": target["file_size"],
            "type": target["type"],
            "format_used": format_used,
            "uri": target["uri"],
        }
        if target["attachment"] is not None:
            structured["attachment_id"] = target["attachment"].id
        else:
            structured["model"] = target["record"]._name
            structured["record_id"] = target["record"].id
            structured["field"] = target["field"]
        structured.update(extra)
        return structured

    def _attachment_headline(self, target):
        """One-line ``name (mimetype, size)`` description of a target."""
        name = target["name"] or target["uri"]
        if target["type"] == "url":
            details = [_("external URL")]
        else:
            details = [target["mimetype"] or _("unknown type")]
            if target["file_size"] is not None:
                details.append(_("%s bytes", target["file_size"]))
        return f"{name} ({', '.join(details)})"

    def _attachment_url_result(self, target):
        """``type='url'`` attachment: no bytes exist, return the external URL."""
        url = target["url"] or ""
        text = "\n".join(
            [
                _("Attachment: %s", self._attachment_headline(target)),
                _("External URL (untrusted, do not fetch blindly): %s", url),
            ]
        )
        return _tool_result(text, self._attachment_structured(target, "url", url=url))

    def _attachment_link_result(self, target, notice=None):
        """Download-link result: metadata text + link fields, no bytes."""
        link = self._attachment_download_link(target)
        lines = []
        if notice:
            lines.append(notice)
        lines.append(_("Attachment: %s", self._attachment_headline(target)))
        lines.append(
            _("Download URL: %s", link["download_url"] or link["download_path"])
        )
        lines.append(
            _(
                "The link is valid until %s (UTC) and grants download access to "
                "anyone holding it, without an Odoo login.",
                link["expires_at"],
            )
        )
        return _tool_result(
            "\n".join(lines), self._attachment_structured(target, "link", **link)
        )

    @staticmethod
    def _check_blob_cap(size):
        """Refuse an inline blob above ``MAX_INLINE_BLOB_BYTES`` (``None``: unknown)."""
        if size is not None and size > MAX_INLINE_BLOB_BYTES:
            raise UserError(
                _(
                    "Attachment is %(size)s bytes, above the %(cap)s-byte inline "
                    "limit; call read_attachment with format='link' instead.",
                    size=size,
                    cap=MAX_INLINE_BLOB_BYTES,
                )
            )

    def _attachment_blob_result(self, target):
        """Embedded-resource result (base64 / text per ``_build_content_entry``)."""
        # Checked on the stored size before any load, and again on the actual
        # bytes for a plain binary column whose size is unknown up front.
        self._check_blob_cap(target["file_size"])
        raw = self._load_target_bytes(target)
        self._check_blob_cap(len(raw))
        entry = self._build_content_entry(target["uri"], target["mimetype"], raw)
        result = _tool_result(
            _("Attachment: %s", self._attachment_headline(target)),
            self._attachment_structured(target, "blob"),
        )
        result["content"].append({"type": "resource", "resource": entry})
        return result

    def _attachment_auto_result(self, target):
        """``format='auto'`` decision ladder (see the tool description)."""
        mimetype = target["mimetype"]
        size = target["file_size"]
        if not mimetype or size is None:
            # Plain binary column / missing mimetype: the bytes must be read
            # to know either, and they live in the DB row rather than the
            # filestore.
            self._load_target_bytes(target)
            mimetype, size = target["mimetype"], target["file_size"]

        if self._is_text_mimetype(mimetype):
            if size > MAX_INLINE_TEXT_BYTES:
                return self._attachment_link_result(
                    target, notice=_("The file is too large to return inline.")
                )
            text = self._load_target_bytes(target).decode("utf-8", errors="replace")
            return self._attachment_inline_text(target, text, "text")

        base = mimetype.split(";", 1)[0].strip().lower()
        if base.startswith("image/"):
            if size > MAX_INLINE_BLOB_BYTES:
                return self._attachment_link_result(
                    target, notice=_("The image is too large to return inline.")
                )
            raw = self._load_target_bytes(target)
            result = _tool_result(
                _("Attachment: %s", self._attachment_headline(target)),
                self._attachment_structured(target, "image"),
            )
            result["content"].append(
                {
                    "type": "image",
                    "data": base64.b64encode(raw).decode("ascii"),
                    "mimeType": base,
                }
            )
            return result

        if self._is_extractable_mimetype(base):
            if size > MAX_EXTRACT_BYTES:
                return self._attachment_link_result(
                    target,
                    notice=_("The document is too large for inline text extraction."),
                )
            text = self._extract_attachment_text(target)
            if text:
                return self._attachment_inline_text(target, text, "extracted_text")
            return self._attachment_link_result(
                target,
                notice=_(
                    "No inline text could be extracted from this document "
                    "(install the attachment_indexation module for inline "
                    "PDF/Office text). Use the download link below."
                ),
            )

        return self._attachment_link_result(
            target, notice=_("This file type is not returned inline.")
        )

    def _attachment_inline_text(self, target, text, format_used):
        """Inline-text result, truncated at ``MAX_INLINE_TEXT_CHARS``.

        The text is carried in ``structuredContent`` as well: per the MCP spec
        the structured result holds the same information as the content
        blocks, and clients that render only ``structuredContent`` (Claude
        Code does) would otherwise never see the file content.
        """
        truncated = len(text) > MAX_INLINE_TEXT_CHARS
        extra = {"truncated": truncated}
        if truncated:
            text = text[:MAX_INLINE_TEXT_CHARS]
            link = self._attachment_download_link(target)
            extra.update(link)
            text += "\n\n" + _(
                "[Truncated after %(chars)s characters. Full file: %(url)s "
                "(valid until %(expires)s UTC)]",
                chars=MAX_INLINE_TEXT_CHARS,
                url=link["download_url"] or link["download_path"],
                expires=link["expires_at"],
            )
        return _tool_result(
            text, self._attachment_structured(target, format_used, text=text, **extra)
        )

    def _link_ttl_seconds(self):
        """Effective download-link validity, in seconds, from
        ``mcp_server.link_ttl_hours``.

        A malformed / non-positive value falls back to the default; an
        excessive one is clamped to ``MAX_LINK_TTL_HOURS`` (42 days).
        """
        hours = self._mcp_int_config(
            "mcp_server.link_ttl_hours", DEFAULT_LINK_TTL_HOURS
        )
        if hours <= 0:
            hours = DEFAULT_LINK_TTL_HOURS
        return min(hours, MAX_LINK_TTL_HOURS) * 3600

    def _attachment_download_link(self, target):
        """Time-limited ``/mcp/download`` link for a target.

        Built with the stateless ``download_token`` -- an HMAC over (model, id,
        field, expiry) -- so nothing is written: no attachment, access-token or
        business-data mutation and no ``sudo``. The token is a delegated-access
        bearer: the route serves the bytes as ``sudo`` to whoever holds it, so
        the expiry is explicit and short (``_link_ttl_seconds``). The relative
        ``download_path`` is canonical; ``download_url`` prefixes it with
        ``web.base.url`` (may be stale behind a proxy).
        """
        expires = int(time.time()) + self._link_ttl_seconds()
        attachment = target["attachment"]
        if attachment is not None:
            record, field = attachment, "raw"
        else:
            record, field = target["record"], target["field"]
        token = download_token(record, field, expires)
        download_path = (
            f"/mcp/download/{record._name}/{record.id}/{field}?access_token={token}"
        )
        params = self.env["ir.config_parameter"].sudo()  # sudo: system web.base.url
        base_url = params.get_param("web.base.url")
        download_url = f"{base_url.rstrip('/')}{download_path}" if base_url else None
        expires_at = (
            datetime.fromtimestamp(expires, tz=timezone.utc)
            .replace(tzinfo=None)
            .isoformat(timespec="seconds")
            + "Z"
        )
        return {
            "download_path": download_path,
            "download_url": download_url,
            "expires_at": expires_at,
        }

    # ------------------------------------------------------------------
    # list_record_attachments
    # ------------------------------------------------------------------
    @mcp_tool(
        name="list_record_attachments",
        title="List Record Attachments",
        description=(
            "List the files attached to a record (ir.attachment rows linked to "
            "it, e.g. chatter attachments), with name, mimetype, size, type "
            "and a uri. Pass a returned 'uri' to read_attachment to get the "
            "content. Binary fields of the record itself are not listed here: "
            "they appear as odoo://record/... URIs in get_record output."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Technical model name (e.g. 'account.move').",
                },
                "record_id": {
                    "type": "integer",
                    "description": "The record whose attachments to list.",
                },
                "limit": {
                    "type": ["integer", "null"],
                    # Live default/max filled in at tools/list serve time.
                    "description": (
                        "Max attachments to return. Defaults to %(default)s, "
                        "capped at %(max)s."
                    ),
                },
                "offset": {
                    "type": "integer",
                    "default": 0,
                    "description": "Number of attachments to skip (pagination).",
                },
            },
            "required": ["model", "record_id"],
            "additionalProperties": False,
        },
        operation="read",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    @api.model
    def list_record_attachments(self, model, record_id, limit=None, offset=0):
        """List a record's attachments (gated like ``read_attachment``).

        Same allow-list rule as ``read_attachment``
        (:meth:`McpMixin._attachment_read_allowed`): the parent ``model``
        MCP-read-enabled OR ``ir.attachment`` itself MCP-read-enabled.
        ``_resolve_model`` cannot be used here -- it refuses every non-enabled
        model, which would wrongly block the ``ir.attachment``-enabled branch --
        so the name is validated separately.
        """
        if not model or model not in self.env:
            raise UserError(_("Unknown model: %s", model))
        if not self._attachment_read_allowed(model):
            raise AccessError(
                _(
                    "Listing attachments via MCP requires '%s' or 'ir.attachment' "
                    "to be MCP-enabled for read.",
                    model,
                )
            )
        record = self._browse_record_or_raise(model, self.env[model], record_id)
        # The user must be able to read the parent record itself, not merely
        # know its id (raises AccessError).
        record.check_access_rights("read")
        record.check_access_rule("read")

        limit = self._effective_limit(limit)
        offset = self._effective_offset(offset)
        # Runs as the calling user -> ir.attachment ACL + record rules bind.
        # ``res_field = False``: chatter/document attachments only, never the
        # blobs backing the record's own binary fields.
        domain = [
            ("res_model", "=", model),
            ("res_id", "=", record.id),
            ("res_field", "=", False),
        ]
        attachments = self.env["ir.attachment"]
        rows = attachments.search_read(
            domain,
            ["name", "mimetype", "type", "file_size", "create_date", "create_uid"],
            offset=offset,
            limit=limit,
            order="id desc",
        )
        if offset == 0 and len(rows) < limit:
            total = len(rows)
        else:
            total = attachments.search_count(domain)

        entries = []
        for row in rows:
            create_uid = row.get("create_uid")
            entries.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "mimetype": row["mimetype"] or None,
                    "type": row["type"],
                    "file_size": row["file_size"],
                    "create_date": row["create_date"],
                    "create_uid": create_uid[1] if create_uid else None,
                    "uri": build_attachment_uri(row["id"]),
                }
            )

        lines = [
            "=" * 60,
            _(
                "Attachments of %(model)s/%(id)s (%(shown)s of %(total)s)",
                model=model,
                id=record.id,
                shown=len(entries),
                total=total,
            ),
            "=" * 60,
        ]
        if not entries:
            lines.append(_("No attachments."))
        for idx, entry in enumerate(entries, offset + 1):
            details = [entry["mimetype"] or _("unknown type")]
            if entry["type"] == "url":
                details.append(_("url"))
            elif entry["file_size"] is not None:
                details.append(_("%s bytes", entry["file_size"]))
            lines.append(
                f"[{idx}] {entry['name']} ({', '.join(details)}) -> {entry['uri']}"
            )
        if offset + len(entries) < total:
            lines.append("")
            lines.append(
                _(
                    "More attachments available -- next page: "
                    "list_record_attachments with offset=%(offset)s, "
                    "limit=%(limit)s",
                    offset=offset + limit,
                    limit=limit,
                )
            )
        if entries:
            lines.append("")
            lines.append(_("Pass a uri to read_attachment to get the content."))

        structured = {
            "attachments": entries,
            "total": total,
            "limit": limit,
            "offset": offset,
            "model": model,
            "record_id": record.id,
        }
        return _tool_result("\n".join(lines), structured)

    # ------------------------------------------------------------------
    # list_resource_templates
    # ------------------------------------------------------------------
    @mcp_tool(
        name="list_resource_templates",
        title="List Resource Templates",
        description=(
            "List the available odoo:// resource URI templates (record binary "
            "fields and attachments). A URI can be passed to the "
            "read_attachment tool (works in every client) or to resources/read."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    @api.model
    def list_resource_templates(self):
        """Advertise the native ``odoo://`` resource templates."""
        models_info = utils.get_enabled_models(self.env)
        model_names = [info["model"] for info in models_info]

        # Emit the LITERAL template strings (matching the controller's
        # resources/templates/list); do NOT route the {placeholder} forms
        # through build_field_uri, which validates the model name and would
        # reject the literal "{model}" token.
        templates = [
            {
                "uri_template": "odoo://record/{model}/{id}/{field}",
                "description": _(
                    "Fetch a record's binary/image field (e.g. an attachment "
                    "or image) instead of inlining base64. Pass the URI to "
                    "read_attachment, or to resources/read."
                ),
                "parameters": {
                    "model": _("Odoo model name (e.g. res.partner)"),
                    "id": _("Record ID (e.g. 10)"),
                    "field": _("Binary field name (e.g. image_1920)"),
                },
                "example": build_field_uri("res.partner", 10, "image_1920"),
            },
            {
                "uri_template": "odoo://attachment/{id}",
                "description": _(
                    "Fetch an ir.attachment by ID. Pass the URI to "
                    "read_attachment, or to resources/read."
                ),
                "parameters": {
                    "id": _("ir.attachment record ID"),
                },
                "example": build_attachment_uri(42),
            },
        ]

        note = _(
            "Resource URIs do not support query parameters. Use the "
            "search_records / get_record tools for filtering, pagination and "
            "field selection. Clients without resources/read support can "
            "dereference any URI with the read_attachment tool."
        )
        text_lines = ["=" * 60, _("Resource templates"), "=" * 60]
        for template in templates:
            text_lines.append(
                f"- {template['uri_template']}: {template['description']}"
            )
        text_lines.append("")
        text_lines.append(note)

        structured = {
            "templates": templates,
            "enabled_models": model_names[:10],
            "total_models": len(model_names),
            "note": note,
        }
        return _tool_result("\n".join(text_lines), structured)

    # ------------------------------------------------------------------
    # get_current_context
    # ------------------------------------------------------------------
    @mcp_tool(
        name="get_current_context",
        title="Get Current Context",
        description=(
            "Return the current session context: the connected user, their "
            "timezone, the active company plus any other allowed companies, and "
            "UTC datetime-handling guidance. Call it when unsure which user or "
            "company a request runs as, or how to interpret datetimes. "
            "Spec-compliant clients also receive this via the initialize "
            "response."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        operation=None,
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    @api.model
    def get_current_context(self):
        """Return the calling user's session context.

        Not model-gated: it routes through neither ``_resolve_model`` nor
        ``_check_op``, so it is reachable whenever the global MCP switch is on
        regardless of the ``mcp.enabled.model`` registry. It exposes only the
        caller's own user/company info -- no new data surface.
        """
        return _tool_result(utils.build_user_context(self.env))

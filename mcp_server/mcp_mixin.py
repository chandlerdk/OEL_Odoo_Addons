"""Transport-agnostic MCP tool layer.

MCP tools live as ordinary methods on the :class:`McpMixin` ``AbstractModel``,
each tagged with the :func:`mcp_tool` decorator. The actual tool methods live
in separate files via ``_inherit = 'mcp.mixin'`` -- because Odoo
composes every ``_inherit`` contribution into a single registry class, an MRO
scan of the live ``mcp.mixin`` class discovers them all, no central list to
keep in sync.

Two concerns are centralised here:

* **Discovery** -- :meth:`McpMixin._get_mcp_tools` builds (and caches) the tool
  index the ``/mcp`` controller serves as ``tools/list`` and dispatches on.
* **Gating** -- :meth:`McpMixin._resolve_model` is the single chokepoint that
  validates a model name and applies the coarse MCP model gate; tools then call
  :meth:`McpMixin._check_op` for their per-operation gate. Odoo's own ACLs and
  record rules remain the real enforcement boundary.
"""

import base64
import logging
import math

from odoo import _, api, models
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.tools import ormcache
from odoo.tools.mimetypes import guess_mimetype

from ..controllers import utils
from ..tools.uri_schema import URIParseError, parse_attachment_uri, parse_field_uri

_logger = logging.getLogger(__name__)

# Largest float accepted as a record id: beyond 2**53 - 1 an IEEE-754 double
# can no longer tell ``v`` from ``v + 1``, so the value may already sit on a
# neighbouring integer the client never named.
MAX_SAFE_INTEGER = 2**53 - 1

# Inline-content ceilings for the ``read_attachment`` tool. Every cap is checked
# against the stored ``file_size`` BEFORE the bytes are loaded, so an over-cap
# attachment never touches the filestore -- the tool answers with a download
# link instead. Text is additionally truncated at ``MAX_INLINE_TEXT_CHARS``
# once decoded (``MAX_INLINE_TEXT_BYTES`` is its 4-bytes-per-char pre-load
# bound).
MAX_INLINE_TEXT_CHARS = 100_000
MAX_INLINE_TEXT_BYTES = 4 * MAX_INLINE_TEXT_CHARS
MAX_INLINE_BLOB_BYTES = 256 * 1024
# A document parser must never be fed an arbitrarily large file.
MAX_EXTRACT_BYTES = 10 * 1024 * 1024
# resources/read returns the whole value in one message, so it is capped too.
MAX_RESOURCE_BYTES = 10 * 1024 * 1024

# Document mimetypes ``ir.attachment._index()`` can turn into text when the
# ``attachment_indexation`` module is installed (pdf + the Office / OpenDocument
# formats it handles). Anything else is never fed to the parser.
_EXTRACTABLE_MIMETYPES = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.presentation",
    }
)

# Mimetypes that carry textual payloads -- returned inline as ``text`` in a
# ``resources/read`` content entry instead of a base64 ``blob``. Everything
# else (images, audio, PDFs, archives, ...) is returned as a blob.
_TEXT_MIMETYPES = frozenset(
    {
        "application/json",
        "application/ld+json",
        "application/xml",
        "application/javascript",
        "application/ecmascript",
        "application/csv",
        "application/yaml",
        "application/x-yaml",
        "application/x-sh",
        "application/sql",
        "application/graphql",
        "image/svg+xml",
    }
)

# Binary-bearing field types eligible for the record-field resource scheme.
_BINARY_FIELD_TYPES = ("binary", "image")


def mcp_tool(
    name, description, input_schema, operation=None, title=None, **annotations
):
    """Mark a method as an MCP tool by attaching its metadata.

    The decorator does not wrap the method -- it only stamps a ``_mcp_tool``
    dict onto it, which :meth:`McpMixin._get_mcp_tools` later reads during the
    MRO scan. Apply it as the *outermost* decorator so the metadata lands on the
    object stored in the class ``__dict__``::

        @mcp_tool(
            name="search_records",
            title="Search Records",
            description="Search for records in a model.",
            input_schema={...},
            operation="read",
            readOnlyHint=True,
        )
        @api.model
        def search_records(self, **params):
            ...

    :param name: the MCP tool name exposed to clients.
    :param description: human/LLM-facing tool description.
    :param input_schema: explicit JSON-Schema dict for the tool's arguments.
    :param operation: model operation this tool performs, one of
        ``read``/``create``/``write``/``unlink``, or ``None`` when the tool is
        not gated on a single model operation (e.g. ``list_models``).
    :param title: optional human-readable display title surfaced top-level in
        ``tools/list``.
    :param annotations: optional MCP tool annotations (``readOnlyHint``,
        ``destructiveHint``, ...) forwarded verbatim to ``tools/list``.
    """

    def decorator(method):
        method._mcp_tool = {
            "name": name,
            "title": title,
            "description": description,
            "input_schema": input_schema,
            "operation": operation,
            "annotations": dict(annotations),
        }
        return method

    return decorator


class McpMixin(models.AbstractModel):
    """Registry + chokepoint for the native MCP tool layer."""

    _name = "mcp.mixin"
    _description = "MCP Tool Mixin"

    @api.model
    @ormcache()
    def _get_mcp_tools(self):
        """Return the tool index ``{tool_name: {...metadata}}``.

        Scans the MRO of the live ``mcp.mixin`` registry class, so tool methods
        contributed by other modules via ``_inherit = 'mcp.mixin'`` are picked
        up automatically. The first definition encountered (most-derived class)
        wins, allowing overrides.

        Cached on the registry's default ``ormcache`` pool (v18 has no ``stable``
        cache): tool definitions are code, so they only change on a module
        upgrade -- a registry-wide ``clear_cache()`` also drops them, costing
        only a cheap MRO re-scan. The returned dict holds plain metadata (no
        recordsets) and must be treated as read-only by callers.
        """
        index = {}
        seen = set()
        for klass in type(self).mro():
            for method_name, attr in vars(klass).items():
                meta = getattr(attr, "_mcp_tool", None)
                if meta is None or method_name in seen:
                    continue
                seen.add(method_name)
                if meta["name"] in index:
                    continue
                index[meta["name"]] = {
                    "method_name": method_name,
                    "title": meta.get("title"),
                    "description": meta["description"],
                    "input_schema": meta["input_schema"],
                    "operation": meta["operation"],
                    "annotations": meta["annotations"],
                }
        return index

    def _resolve_model(self, model):
        """Validate ``model`` and apply the coarse MCP model gate.

        The single chokepoint every tool routes through. Raises before any data
        is touched when the model is unknown or not MCP-enabled; per-operation
        gating is the caller's job via :meth:`_check_op`.

        :param model: technical model name (e.g. ``res.partner``).
        :return: an empty recordset for ``model`` bound to the calling user's
            environment.
        :raises UserError: when ``model`` is not a known model.
        :raises AccessError: when ``model`` is not enabled for MCP access.
        """
        if not model or model not in self.env:
            raise UserError(_("Unknown model: %s", model))
        if not utils.is_model_mcp_enabled(self.env, model):
            raise AccessError(_("Model '%s' is not enabled for MCP access.", model))
        return self.env[model]

    def _check_op(self, model, operation):
        """Apply the per-operation MCP gate for ``model``.

        Coarse opt-in check only; Odoo's ORM still enforces ``ir.model.access``
        and record rules when the operation actually runs.

        :param model: technical model name.
        :param operation: one of ``read``/``create``/``write``/``unlink``.
        :raises AccessError: when the operation is not allowed via MCP.
        """
        if not utils.check_model_operation_allowed(self.env, model, operation):
            raise AccessError(
                _(
                    "Operation '%(operation)s' is not allowed on model "
                    "'%(model)s' via MCP.",
                    operation=operation,
                    model=model,
                )
            )

    def _check_write_values(self, model, records, values_list, creating=False):
        """Apply the MCP gate to the models that a write reaches through its values.

        x2many commands reach the comodel, and an attachment write reaches the
        owner model (see :func:`utils.denied_write_operation`). ``creating``
        says that ``values_list`` creates records. Odoo's ORM still enforces
        its own access rights when the write runs.

        :raises UserError: for values in a shape the gate does not read.
        :raises AccessError: when one of those operations is not allowed via MCP.
        """
        try:
            denied = utils.denied_write_operation(
                self.env, model, records, values_list, creating
            )
        except utils.UnreadableWriteValues as e:
            raise UserError(str(e)) from None
        if denied:
            raise AccessError(
                _(
                    "Operation '%(operation)s' is not allowed on model "
                    "'%(model)s' via MCP (field '%(field)s').",
                    operation=denied[1],
                    model=denied[0],
                    field=denied[2],
                )
            )

    @staticmethod
    def _coerce_record_id(value):
        """Coerce one client-supplied record id to ``int`` or raise ``ValueError``.

        Accepts int, digit strings (legacy tolerance) and integral floats
        (JSON Schema treats ``3.0`` as an integer). Rejects bool, non-integral
        floats (``int(1.5)`` would silently target record 1), non-finite
        values and floats beyond ``MAX_SAFE_INTEGER``.
        """
        if isinstance(value, bool):
            raise ValueError(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if not math.isfinite(value) or abs(value) > MAX_SAFE_INTEGER:
                raise ValueError(value)
            if not value.is_integer():
                raise ValueError(value)
            return int(value)
        if isinstance(value, str):
            return int(value.strip())
        raise ValueError(value)

    def _browse_record_or_raise(self, model, model_rs, record_id):
        """Browse a single record by id or raise a uniform ``MissingError``.

        Shared by every single-record tool (get/update/delete/post_message and
        the record-field resource) so a missing id always surfaces the same
        ``MissingError`` -- not a mix of ``UserError``/``MissingError``. The id
        goes through :meth:`_coerce_record_id` (a ``UserError`` on a bad one),
        the same policy as the batch tools.
        """
        try:
            record_id = self._coerce_record_id(record_id)
        except (TypeError, ValueError) as err:
            raise UserError(_("'record_id' must be an integer.")) from err
        record = model_rs.browse(record_id).exists()
        if not record:
            raise MissingError(
                _(
                    "Record not found: %(model)s with ID %(id)s",
                    model=model,
                    id=record_id,
                )
            )
        return record

    # ------------------------------------------------------------------
    # Resources
    # ------------------------------------------------------------------
    def _read_resource(self, uri):
        """Resolve an ``odoo://`` resource URI to an MCP content entry.

        Two native schemes are supported (the ones tool output emits in place
        of inline base64):

        * ``odoo://record/{model}/{id}/{field}`` -- a binary/image field on a
          record. Gated through :meth:`_resolve_model` + :meth:`_check_op`
          (``read``); the field read runs as the calling user, so Odoo's ACLs
          and record rules still apply.
        * ``odoo://attachment/{id}`` -- an ``ir.attachment``. Gated by the MCP
          allow-list (see :meth:`_check_attachment_allowed`) *and* read as the
          calling user (no ``sudo``), so ir.attachment's own access checks bind
          it too.

        :return: a single ``resources/read`` content entry, either
            ``{uri, mimeType, text}`` (textual mimetype) or
            ``{uri, mimeType, blob}`` (base64, everything else).
        :raises UserError: unknown/unsupported URI, model or field; empty field.
        :raises MissingError: the record or attachment does not exist.
        :raises AccessError: the model/field is not MCP-enabled or ACL denies it.
        """
        if not isinstance(uri, str) or not uri.startswith("odoo://"):
            raise UserError(_("Invalid resource URI: %s", uri))

        try:
            ref = parse_field_uri(uri)
        except URIParseError:
            ref = None
        if ref is not None:
            return self._read_record_field(uri, ref)

        try:
            attachment_id = parse_attachment_uri(uri)
        except URIParseError as err:
            raise UserError(_("Unsupported resource URI: %s", uri)) from err
        return self._read_attachment(uri, attachment_id)

    def _resolve_binary_field(self, ref):
        """Gate + validate a ``odoo://record/...`` reference; return its model.

        Applies :meth:`_resolve_model` and :meth:`_check_op` (``read``) for the
        parent model, then checks the field exists and is binary/image. Shared
        by ``resources/read`` and the ``read_attachment`` tool.
        """
        model_rs = self._resolve_model(ref.model)
        self._check_op(ref.model, "read")

        field = model_rs._fields.get(ref.field)
        if field is None:
            raise UserError(
                _(
                    "Unknown field '%(field)s' on model '%(model)s'.",
                    field=ref.field,
                    model=ref.model,
                )
            )
        if field.type not in _BINARY_FIELD_TYPES:
            raise UserError(
                _(
                    "Field '%(field)s' on '%(model)s' is not a binary field.",
                    field=ref.field,
                    model=ref.model,
                )
            )
        return model_rs

    def _read_record_field(self, uri, ref):
        """Read a binary/image field via the ``odoo://record/...`` scheme."""
        model_rs = self._resolve_binary_field(ref)
        record = self._browse_record_or_raise(ref.model, model_rs, ref.record_id)

        # Runs as the calling user -> ORM enforces read ACLs / record rules.
        value = record.read([ref.field])[0].get(ref.field)
        if not value:
            raise self._empty_field_error(ref)

        # Checked on the base64 length before the bytes are decoded.
        self._check_resource_size(len(value) * 3 // 4)
        raw = base64.b64decode(value)
        mimetype = self._record_field_mimetype(ref.model, ref.record_id, ref.field, raw)
        return self._build_content_entry(uri, mimetype, raw)

    @staticmethod
    def _check_resource_size(size):
        """Refuse a ``resources/read`` value above ``MAX_RESOURCE_BYTES``."""
        if size > MAX_RESOURCE_BYTES:
            raise UserError(
                _(
                    "Content is %(size)s bytes, above the %(cap)s-byte limit of "
                    "resources/read; call read_attachment with format='link' "
                    "instead.",
                    size=size,
                    cap=MAX_RESOURCE_BYTES,
                )
            )

    @staticmethod
    def _empty_field_error(ref):
        """``MissingError`` for a binary field that holds no data."""
        return MissingError(
            _(
                "Field '%(field)s' on %(model)s/%(id)s holds no data.",
                field=ref.field,
                model=ref.model,
                id=ref.record_id,
            )
        )

    def _read_attachment(self, uri, attachment_id):
        """Read an ``ir.attachment`` via the ``odoo://attachment/...`` scheme."""
        # No sudo: ir.attachment's own access checks bind the read to the user.
        attachment = self.env["ir.attachment"].browse(attachment_id).exists()
        if not attachment:
            raise MissingError(_("Attachment not found: %s", attachment_id))

        # MCP allow-list gate on top of Odoo's ACL (below): without this an rpc
        # key could read any attachment beyond the configured models.
        self._check_attachment_allowed(attachment)

        # Reading a stored field forces the attachment ACL check (AccessError
        # when the user may not read it).
        mimetype = attachment.mimetype
        # Checked on the stored size before the bytes are loaded.
        self._check_resource_size(attachment.file_size or 0)
        raw = attachment.raw or b""
        if not mimetype:
            mimetype = guess_mimetype(raw, default="application/octet-stream")
        return self._build_content_entry(uri, mimetype, raw)

    def _attachment_read_allowed(self, res_model):
        """The MCP allow-list rule for reading attachments of ``res_model``.

        True when *either* ``ir.attachment`` itself is MCP-enabled for read
        (expose attachments broadly), *or* the parent record model is
        MCP-enabled for read (the attachment rides its parent's exposure).
        The single definition behind :meth:`_check_attachment_allowed` and
        ``list_record_attachments``; Odoo's own ACL still applies on top.
        """
        if utils.check_model_operation_allowed(self.env, "ir.attachment", "read"):
            return True
        return bool(res_model) and utils.check_model_operation_allowed(
            self.env, res_model, "read"
        )

    def _check_attachment_allowed(self, attachment):
        """Enforce the MCP allow-list for an attachment read.

        Keeps attachment access inside the per-model opt-in (see
        :meth:`_attachment_read_allowed`).

        :raises AccessError: when neither rule grants access.
        """
        if self._attachment_read_allowed(attachment.res_model):
            return
        raise AccessError(
            _(
                "Attachment access via MCP requires 'ir.attachment' or the "
                "attachment's parent model to be MCP-enabled for read."
            )
        )

    # ------------------------------------------------------------------
    # read_attachment targets (metadata-only resolution, lazy bytes)
    # ------------------------------------------------------------------
    def _resolve_attachment_for_tool(self, uri, attachment_id):
        """Resolve an ``ir.attachment`` for the ``read_attachment`` tool.

        Same gate + ACL binding as :meth:`_read_attachment` -- the MCP
        allow-list, then Odoo's own attachment ACL via a *metadata* read (no
        ``sudo``) -- but the bytes are NOT loaded here: the caller decides per
        output format whether the filestore is touched at all.

        :return: a target dict ``{uri, attachment, record, field, name,
            mimetype, file_size, type, url}``.
        """
        # No sudo: ir.attachment's own access checks bind the read to the user.
        attachment = self.env["ir.attachment"].browse(attachment_id).exists()
        if not attachment:
            raise MissingError(_("Attachment not found: %s", attachment_id))
        self._check_attachment_allowed(attachment)
        # Reading stored fields forces the attachment ACL check (AccessError
        # when the user may not read it) without loading the payload.
        meta = attachment.read(["name", "mimetype", "file_size", "type", "url"])[0]
        return {
            "uri": uri,
            "attachment": attachment,
            "record": None,
            "field": None,
            "name": meta["name"],
            "mimetype": meta["mimetype"] or None,
            "file_size": meta["file_size"],
            "type": meta["type"],
            "url": meta["url"] or None,
        }

    def _resolve_record_field_for_tool(self, uri, ref):
        """Resolve a ``odoo://record/...`` reference for ``read_attachment``.

        Gates through :meth:`_resolve_binary_field` (parent model enabled for
        read -- ``ir.attachment`` enablement never unlocks arbitrary binary
        fields), then proves the user's access with ``check_access_rights`` +
        ``check_access_rule`` + ``check_field_access_rights`` -- an ACL probe
        that fetches no bytes.
        Metadata comes from the backing ``ir.attachment`` when the field is
        attachment-stored; a plain binary column has no reliable name / size /
        mimetype until its bytes are read, so those stay ``None``.
        """
        model_rs = self._resolve_binary_field(ref)
        record = self._browse_record_or_raise(ref.model, model_rs, ref.record_id)
        record.check_access_rights("read")
        record.check_access_rule("read")
        record.check_field_access_rights("read", [ref.field])

        target = {
            "uri": uri,
            "attachment": None,
            "record": record,
            "field": ref.field,
            "name": None,
            "mimetype": None,
            "file_size": None,
            "type": "binary",
            "url": None,
        }
        # The explicit res_field condition disables the ORM's default res_field
        # filtering; the search runs as the user (field access granted above).
        backing = self.env["ir.attachment"].search(
            [
                ("res_model", "=", ref.model),
                ("res_id", "=", ref.record_id),
                ("res_field", "=", ref.field),
            ],
            limit=1,
        )
        if backing:
            target["name"] = backing.name
            target["mimetype"] = backing.mimetype or None
            target["file_size"] = backing.file_size
        # No backing attachment: an attachment-stored field is then empty, and
        # a plain column may be. ``bin_size`` reads a size placeholder, not the
        # bytes, so this stays a metadata-only probe (same MissingError as
        # ``resources/read``).
        elif not record.with_context(bin_size=True).read([ref.field])[0].get(ref.field):
            raise self._empty_field_error(ref)
        return target

    def _load_target_bytes(self, target):
        """Load the payload of a ``read_attachment`` target (as the user).

        Called only by the output branches that inline content; ``link`` and
        over-cap refusals never reach it. Fills in ``mimetype`` / ``file_size``
        when they were unknown (plain binary column, missing mimetype). The
        bytes are memoised on the target so a branch that first sniffed them
        does not fetch them twice.
        """
        if "raw" in target:
            return target["raw"]
        attachment = target["attachment"]
        if attachment is not None:
            raw = attachment.raw or b""
        else:
            record, field = target["record"], target["field"]
            # Runs as the calling user -> ORM enforces read ACLs / record rules.
            value = record.read([field])[0].get(field)
            raw = base64.b64decode(value) if value else b""
        target["raw"] = raw
        if target["file_size"] is None:
            target["file_size"] = len(raw)
        if not target["mimetype"]:
            target["mimetype"] = guess_mimetype(raw, default="application/octet-stream")
        return raw

    def _extract_attachment_text(self, target):
        """Best-effort text of a PDF / Office target via ``ir.attachment._index``.

        Uses the stored ``index_content`` first -- it is recomputed on every
        ``raw`` write with the installed ``_index`` override, so it is fresh for
        attachments written after ``attachment_indexation`` was installed --
        and otherwise runs ``_index`` on the bytes. Returns ``None`` when no
        text is available: the base ``_index`` yields nothing for non-text
        mimetypes (older cores return a ``mimetype.split('/')[0]`` placeholder
        such as ``"application"``, rejected here too), and a parser failure is
        logged rather than raised.

        The result is deliberately not written back to ``index_content``: this
        is a read-only tool and the user may lack write access. A document
        with no extractable text (a scanned PDF) is therefore re-parsed on
        every ``auto`` call; the cost is bounded by ``MAX_EXTRACT_BYTES``.
        """
        attachment = target["attachment"]
        mimetype = target["mimetype"] or ""
        placeholder = mimetype.split("/", 1)[0]
        if attachment is not None:
            stored = attachment.index_content
            if stored and stored != placeholder:
                return stored
        raw = self._load_target_bytes(target)
        checksum = attachment.checksum if attachment is not None else None
        try:
            text = self.env["ir.attachment"]._index(raw, mimetype, checksum=checksum)
        except Exception:  # noqa: BLE001 - parser failure degrades to a link
            _logger.warning(
                "MCP read_attachment: text extraction failed for %s",
                target["uri"],
                exc_info=True,
            )
            return None
        if not text or text == placeholder:
            return None
        return text

    @staticmethod
    def _is_extractable_mimetype(mimetype):
        """Whether ``mimetype`` is a document ``_index()`` may turn into text."""
        base = (mimetype or "").split(";", 1)[0].strip().lower()
        return base in _EXTRACTABLE_MIMETYPES

    def _record_field_mimetype(self, model, record_id, field, raw):
        """Best-effort mimetype for a record binary field.

        Prefer the backing ``ir.attachment`` (for attachment-stored fields such
        as ``image_1920``); otherwise sniff the bytes. The explicit ``res_field``
        condition disables the ORM's default res_field filtering, and the search
        runs as the user (field access already granted by the field read).
        """
        attachment = self.env["ir.attachment"].search(
            [
                ("res_model", "=", model),
                ("res_id", "=", record_id),
                ("res_field", "=", field),
            ],
            limit=1,
        )
        if attachment and attachment.mimetype:
            return attachment.mimetype
        return guess_mimetype(raw, default="application/octet-stream")

    def _build_content_entry(self, uri, mimetype, raw):
        """Build one ``resources/read`` content entry from raw bytes.

        Textual mimetypes are decoded inline as ``text``; everything else
        (images, audio, PDFs, ...) is returned as a base64 ``blob``.
        """
        mimetype = mimetype or "application/octet-stream"
        if self._is_text_mimetype(mimetype):
            return {
                "uri": uri,
                "mimeType": mimetype,
                "text": raw.decode("utf-8", errors="replace"),
            }
        return {
            "uri": uri,
            "mimeType": mimetype,
            "blob": base64.b64encode(raw).decode("ascii"),
        }

    @staticmethod
    def _is_text_mimetype(mimetype):
        """Whether ``mimetype`` denotes textual (inline-able) content."""
        base = (mimetype or "").split(";", 1)[0].strip().lower()
        if not base:
            return False
        if base.startswith("text/"):
            return True
        if base in _TEXT_MIMETYPES:
            return True
        return base.endswith("+json") or base.endswith("+xml")

"""Client-facing error sanitization for the native MCP endpoint.

Single chokepoint that decides what an MCP client may see when something goes
wrong: never leak tracebacks, source paths, SQL/driver internals, constraint
names or memory addresses to clients.

Two cases:

* **User-facing Odoo exceptions** (``UserError``, ``AccessError``,
  ``ValidationError``, ``MissingError`` -- including the module's own MCP gate
  errors, which are raised as ``UserError``/``AccessError``) carry messages
  authored for end users. Their message passes through, still defensively
  scrubbed in case it ever embeds an internal detail.
* **Database constraint failures** (``psycopg2.IntegrityError``: NOT NULL,
  foreign-key and unique violations) are translated with Odoo's own
  ``_as_validation_error`` -- the field/model-naming text the web client
  shows -- then scrubbed; the driver's raw fallback text is genericized.
* **Every other exception** is reduced to a single generic message while the
  full traceback is preserved server-side via the logger -- so an unexpected
  ORM/psycopg/Python error can be diagnosed from the server log without ever
  reaching the client.

A mapping that *rewrites* raw XML-RPC fault strings into friendlier text is
intentionally omitted: native tools raise clean ``_()``-translated Odoo
messages directly, so rewriting them would only lose information and could mask
the user-safe gate messages the suite relies on.
"""

import logging
import re

from psycopg2 import errors as pg_errors

from odoo import _
from odoo.exceptions import (
    AccessError,
    MissingError,
    RedirectWarning,
    UserError,
    ValidationError,
)
from odoo.service.model import _as_validation_error
from odoo.tools import SQL

_logger = logging.getLogger(__name__)

# Odoo exceptions whose message is authored for end users and may be surfaced
# to MCP clients verbatim (the module's own MCP gate errors are raised as these).
SAFE_EXCEPTIONS = (
    UserError,
    AccessError,
    ValidationError,
    MissingError,
    RedirectWarning,
)

# Returned for any non-safe (unexpected/internal) exception. Kept as the single
# generic message so the dispatcher / ir.http error renders stay consistent.
GENERIC_ERROR_MESSAGE = "Internal server error"
# A unique violation Odoo cannot attribute to fields (a unique *index* has no
# pg_constraint row to resolve columns from) -- still a data error, so it is
# named as such without leaking the index or table name.
UNIQUE_ERROR_MESSAGE = "A record with the same unique values already exists."

# Marker identifying traceback-shaped text and the shape of the final exception
# line that ends one.
_TRACEBACK_MARKER = "Traceback (most recent call last)"
_EXCEPTION_LINE_RE = re.compile(
    r"^[A-Za-z_][\w.]*(?:Error|Exception|Warning|Violation)\b"
)
_POSTGRES_DIAG_RE = re.compile(r"^(DETAIL|HINT|CONTEXT|LINE \d)", re.IGNORECASE)

# Internal-detail patterns scrubbed even from "safe" messages (defensive).
# Order matters: traceback frame lines are removed before the looser
# path/line-number patterns run.
_SCRUB_PATTERNS = (
    # Traceback frame lines.
    (re.compile(r'^\s*File "[^"]+", line \d+.*$', re.MULTILINE), ""),
    (re.compile(r"Traceback \(most recent call last\):"), ""),
    # Postgres diagnostic lines (DETAIL/HINT/CONTEXT/QUERY/LINE n). _reduce_traceback
    # only drops these when a traceback marker is present; a SAFE exception that
    # wraps a constraint violation can carry a bare "DETAIL: Key (email)=(x)
    # already exists" with NO traceback, leaking a column name and another
    # record's value. Strip such lines unconditionally. The trailing colon that
    # Postgres always emits is required so prose that merely starts with one of
    # these words ("Detailed steps ...", "Line 5 quantity must be positive") is
    # left intact.
    (
        re.compile(
            r"^[ \t]*(?:DETAIL|HINT|CONTEXT|QUERY|LINE \d+):.*$",
            re.IGNORECASE | re.MULTILINE,
        ),
        "",
    ),
    # File paths and *.py references.
    (re.compile(r"(?:/[^/\s]+)+/[^/\s]+\.py\b"), ""),
    (re.compile(r'"[^"]+\.py"'), ""),
    # Line numbers.
    (re.compile(r",?\s*line\s+\d+"), ""),
    # Class reprs and memory addresses / object references. Removed *before* the
    # module/driver scrubs below so a structured repr is stripped whole -- else
    # an inner ``psycopg2.*`` match would gut its content and leave a bare
    # ``<class ''>`` remnant.
    (re.compile(r"<class '[^']+'>"), ""),
    (re.compile(r"\b[Oo]bject at 0x[0-9a-fA-F]+"), "object"),
    (re.compile(r"\bat 0x[0-9a-fA-F]+"), ""),
    # Module / driver internal paths.
    (re.compile(r"\b(?:odoo|mcp_server)\.[A-Za-z0-9_.]+:"), ""),
    (re.compile(r"\bpsycopg2(?:\.[A-Za-z0-9_.]+)?\b", re.IGNORECASE), ""),
)


def sanitize_exception(exc, log_context=None):
    """Return a client-safe message for ``exc`` (the public entry point).

    :param exc: the caught exception.
    :param log_context: short description of where the error happened, used as
        the server log message when ``exc`` is genericized.
    :return: the user-facing Odoo message (scrubbed) for a safe exception, or
        :data:`GENERIC_ERROR_MESSAGE` for anything else.
    """
    if isinstance(exc, SAFE_EXCEPTIONS):
        return sanitize_message(_exc_message(exc))
    # Unexpected error: keep the full traceback server-side, return generic.
    _logger.error(log_context or "Unhandled MCP error", exc_info=exc)
    return GENERIC_ERROR_MESSAGE


def sanitize_integrity_error(env, exc, log_context=None):
    """Client-safe message for a database constraint failure.

    A NOT NULL / foreign-key / unique violation raised by the ORM flush is a
    data error the model can act on, not an internal fault. Odoo's own
    ``_as_validation_error`` (what the web client shows) names the field and
    model, or the constraint's authored message; a dangling foreign key on
    insert / update gets its own wording first (see :func:`_insert_fk_message`),
    because core's FK text describes the delete case. Core translates only
    NOT NULL, foreign-key and ``_sql_constraints`` violations (see
    :func:`_is_translated_by_core`); anything else would echo the raw driver
    text (table / constraint names), which is mapped to a fixed message
    instead: ``UNIQUE_ERROR_MESSAGE`` for a unique violation (typically a
    unique *index*, whose columns Odoo cannot resolve), the generic message
    otherwise. The result is scrubbed like any safe message, which also drops
    the ``Detail:`` line a unique violation carries (it embeds the conflicting
    value).

    The cursor must be usable again (the caller's savepoint rolled back).
    Never raises: a failure while translating falls back to the generic
    message.
    """
    _logger.warning(log_context or "MCP database constraint error", exc_info=exc)
    try:
        table = exc.diag.table_name
        model = env["base"]
        for rclass in env.registry.values():
            if table == rclass._table:
                model = env[rclass._name]
                break
        message = _insert_fk_message(model, exc)
        if not message and _is_translated_by_core(env, exc):
            message = _as_validation_error(env, exc).args[0]
        if not message:
            if isinstance(exc, pg_errors.UniqueViolation):
                return UNIQUE_ERROR_MESSAGE
            return GENERIC_ERROR_MESSAGE
        return sanitize_message(message)
    except Exception:  # noqa: BLE001 - translation must never raise
        _logger.exception("Could not translate database constraint error")
        return GENERIC_ERROR_MESSAGE


def _is_translated_by_core(env, exc):
    """Whether ``_as_validation_error`` names the field / constraint for ``exc``.

    Mirrors its dispatch: NOT NULL and foreign-key violations, plus a
    constraint registered in ``_sql_constraints``. Everything else gets the
    raw driver text, which must not reach the client.
    """
    if isinstance(exc, (pg_errors.NotNullViolation, pg_errors.ForeignKeyViolation)):
        return True
    return exc.diag.constraint_name in env.registry._sql_constraints


def _columns_from_diagnostics(cr, diag):
    """Column names behind a constraint failure, from the driver diagnostics.

    The diagnostics carry the column only for NOT NULL; a foreign-key
    violation names the constraint, whose columns are resolved from
    ``pg_constraint``. Empty when nothing can be resolved.
    """
    if diag.column_name:
        return [diag.column_name]
    cr.execute(
        SQL(
            """
            SELECT ARRAY(
                SELECT attname FROM pg_attribute
                WHERE attrelid = conrelid AND attnum = ANY(conkey)
            ) AS "columns"
            FROM pg_constraint
            JOIN pg_class t ON t.oid = conrelid
            WHERE conname = %s
                AND t.relname = %s
                AND t.relnamespace = current_schema::regnamespace
            """,
            diag.constraint_name,
            diag.table_name,
        )
    )
    row = cr.fetchone()
    return row[0] if row else []


def _insert_fk_message(model, exc):
    """Message for a dangling foreign key on INSERT / UPDATE, else ``None``.

    Odoo's own FK text is written for the DELETE case ("another model is using
    the record you are trying to delete"), which misleads a client that just
    passed a non-existent id in a create / update. Postgres distinguishes the
    two in the primary message, so name the field and say the referenced
    record does not exist. Columns come from the constraint diagnostics like
    core does; the raw fallback (no resolvable field) still gets a field-less
    message rather than the delete-oriented one.
    """
    if not isinstance(exc, pg_errors.ForeignKeyViolation):
        return None
    primary = exc.diag.message_primary or ""
    if not primary.startswith("insert or update"):
        return None
    model_string = model.env["ir.model"]._get(model._name).name or model._description
    columns = _columns_from_diagnostics(model.env.cr, exc.diag)
    if len(columns) == 1 and (field := model._fields.get(columns[0])):
        return _(
            "The value for %(field)s refers to a record that does not exist.\n"
            "Model: %(model)s",
            field=f"'{field._description_string(model.env)}' ({field.name})",
            model=f"'{model_string}' ({model._name})",
        )
    return _(
        "A value refers to a record that does not exist.\nModel: %(model)s",
        model=f"'{model_string}' ({model._name})",
    )


def sanitize_message(text):
    """Scrub internal detail from ``text`` and return a client-safe string.

    Reduces a traceback-shaped message to its final exception line, removes the
    internal-detail patterns above, and tidies whitespace (single newlines kept
    so multi-line user/validation messages stay readable). Returns
    :data:`GENERIC_ERROR_MESSAGE` when nothing usable remains.
    """
    if not text:
        return GENERIC_ERROR_MESSAGE

    text = _reduce_traceback(str(text))
    for pattern, replacement in _SCRUB_PATTERNS:
        text = pattern.sub(replacement, text)

    # Collapse runs of spaces/tabs left by scrubbing; keep single newlines.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text or GENERIC_ERROR_MESSAGE


def _exc_message(exc):
    """Best-effort user message from an Odoo exception (its first string arg)."""
    args = getattr(exc, "args", None)
    if args and isinstance(args[0], str):
        return args[0]
    return str(exc)


def _reduce_traceback(text):
    """Reduce a traceback-shaped message to its final exception message.

    Intermediate frames expose source code, file structure, constraint names and
    data values; only the final exception message is user-relevant. Odoo business
    errors are often multi-line, so everything from the exception line onward is
    kept. Trailing Postgres diagnostics (DETAIL/HINT/...) are dropped.
    """
    if _TRACEBACK_MARKER not in text:
        return text

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    while lines and _POSTGRES_DIAG_RE.match(lines[-1]):
        lines.pop()
    if not lines:
        return GENERIC_ERROR_MESSAGE

    last_frame = max(
        (i for i, line in enumerate(lines) if line.startswith('File "')),
        default=-1,
    )
    tail = lines[last_frame + 1 :]
    exc_idx = next(
        (i for i, line in enumerate(tail) if _EXCEPTION_LINE_RE.match(line)),
        len(tail) - 1,
    )
    final = "\n".join(tail[exc_idx:])
    final = re.split(r"\s+DETAIL:", final)[0].strip()
    # A real exception message has words; a bare SQL pointer ("^") or empty
    # remnant is meaningless and not worth surfacing.
    if not re.search(r"[A-Za-z]", final):
        return GENERIC_ERROR_MESSAGE
    return final

"""``odoo://`` URI schema build/parse helpers.

The native *binary/attachment* scheme used by the module's MCP resources:
``odoo://record/{model}/{id}/{field}`` (a record's binary field) and
``odoo://attachment/{id}`` (an ``ir.attachment``). These replace inline base64
payloads in tool output so the LLM can fetch blobs on demand.
"""

import re
from dataclasses import dataclass


@dataclass
class RecordFieldURI:
    """Parsed ``odoo://record/{model}/{id}/{field}`` URI."""

    model: str
    record_id: int
    field: str


class URIParseError(Exception):
    """Exception raised when URI parsing fails."""


# Patterns for the native binary/attachment schemes.
RECORD_FIELD_URI_PATTERN = re.compile(
    r"^odoo://record/([a-zA-Z][a-zA-Z0-9_.]*)/(\d+)/([a-zA-Z][a-zA-Z0-9_]*)$"
)
ATTACHMENT_URI_PATTERN = re.compile(r"^odoo://attachment/(\d+)$")


def build_field_uri(model: str, record_id: int, field: str) -> str:
    """Build a ``odoo://record/{model}/{id}/{field}`` binary-field URI.

    Used to replace populated binary/image fields in tool output with a
    fetchable resource reference instead of inline base64. Callers always pass a
    model already resolved through the MCP gate, so an invalid model/field here
    is a programming error, surfaced as ``ValueError``.

    Raises:
        ValueError: If the model name is invalid or the field name is empty
    """
    if not _is_valid_model_name(model):
        raise ValueError(f"Invalid model name: {model}")
    if not field:
        raise ValueError("Field name is required")
    return f"odoo://record/{model}/{record_id}/{field}"


def parse_field_uri(uri: str) -> RecordFieldURI:
    """Parse a ``odoo://record/{model}/{id}/{field}`` URI.

    Raises:
        URIParseError: If the URI does not match the record-field scheme
    """
    match = RECORD_FIELD_URI_PATTERN.match(uri)
    if not match:
        raise URIParseError(f"Invalid record field URI: {uri}")
    model, record_id_str, field = match.groups()
    return RecordFieldURI(model=model, record_id=int(record_id_str), field=field)


def build_attachment_uri(attachment_id: int) -> str:
    """Build a ``odoo://attachment/{id}`` URI for an ``ir.attachment``."""
    return f"odoo://attachment/{attachment_id}"


def parse_attachment_uri(uri: str) -> int:
    """Parse a ``odoo://attachment/{id}`` URI, returning the attachment id.

    Raises:
        URIParseError: If the URI does not match the attachment scheme
    """
    match = ATTACHMENT_URI_PATTERN.match(uri)
    if not match:
        raise URIParseError(f"Invalid attachment URI: {uri}")
    return int(match.group(1))


def _is_valid_model_name(model: str) -> bool:
    """Check if a model name is valid.

    Odoo model names must:
    - Start with a letter
    - Contain only letters, numbers, dots, and underscores
    - Not be empty
    """
    if not model:
        return False
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9_.]*$", model))

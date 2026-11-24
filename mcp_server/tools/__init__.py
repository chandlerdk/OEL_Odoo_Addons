"""Pure-Python helpers backing the native MCP tool layer.

These modules contain no Odoo model logic — only stdlib utilities consumed by
the native MCP tool layer (formatting records for LLMs, ``odoo://`` resource
URIs, and smart field selection). They are deliberately framework-agnostic so
they can be unit-tested and reused without a live Odoo connection.
"""

from . import formatters
from . import smart_fields
from . import uri_schema

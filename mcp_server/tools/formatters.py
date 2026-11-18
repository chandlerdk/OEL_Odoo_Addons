"""Data formatters for LLM-friendly output.

These formatters convert Odoo data (plain dicts as returned by ``read`` plus
``fields_get`` metadata) into a hierarchical text format optimized for LLM
consumption. They take only plain data — no live Odoo connection.
"""

import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional


class RecordFormatter:
    """Formats Odoo records for LLM consumption."""

    # Field types that should be omitted by default
    OMIT_FIELDS = {
        "__last_update",
        "write_date",
        "create_date",
        "write_uid",
        "create_uid",
        "message_follower_ids",
        "message_ids",
        "message_main_attachment_id",
    }

    # Binary field types
    BINARY_FIELDS = {"binary", "image", "file"}

    # Default per-field display cap — long values (pasted documents, base64
    # blobs) are truncated with an explicit marker to protect the LLM context
    MAX_FIELD_DISPLAY_LENGTH = 2000

    def __init__(
        self,
        model: str,
        max_field_length: int = MAX_FIELD_DISPLAY_LENGTH,
        full_value_hint: bool = True,
        full_value_limit: Optional[int] = None,
    ):
        """Initialize the formatter.

        ``max_field_length`` caps each text value. ``full_value_hint`` makes the
        truncation marker point at a get_record call that names the field; the
        tool layer turns it off when the cap is already the per-field ceiling,
        where that call would return the same cut value. ``full_value_limit``
        is how much of a value that call shows (``None``: all of it), so a
        longer value is not promised in full.
        """
        self.model = model
        self.max_field_length = max_field_length
        self.full_value_hint = full_value_hint
        self.full_value_limit = full_value_limit

    def format_record(
        self,
        record: Dict[str, Any],
        fields_metadata: Optional[Dict[str, Dict[str, Any]]] = None,
        indent_level: int = 0,
        related_summaries: Optional[Dict[str, List[tuple]]] = None,
        enabled_relations: Optional[set] = None,
    ) -> str:
        """Format a single record into hierarchical text.

        ``related_summaries`` optionally maps an x2many field name to a
        pre-resolved ``[(id, name), ...]`` list (built by the tool layer under
        the caller's ACLs); such fields are listed inline, others collapse to a
        count plus a search hint (see :meth:`_format_relation_field`).

        ``enabled_relations`` is the set of related model names that are exposed
        via MCP; a collapsed relation only advertises a search_records hint when
        its target is in this set. ``None`` means "unknown" -- the hint is shown
        regardless (the tool layer always passes a concrete set).
        """
        lines = []
        indent = "  " * indent_level
        related_summaries = related_summaries or {}

        # Record header. Odoo returns False (not None) for unset char
        # fields, so chain with `or` instead of relying on .get defaults.
        record_id = record.get("id", "Unknown")
        record_name = (
            record.get("display_name") or record.get("name") or f"Record {record_id}"
        )

        lines.append(f"{indent}{'=' * 50}")
        lines.append(f"{indent}Record: {self.model}/{record_id}")
        lines.append(f"{indent}Name: {record_name}")
        lines.append(f"{indent}{'=' * 50}")

        # Group fields by category
        simple_fields = []
        relation_fields = []

        for field_name, field_value in record.items():
            if field_name in self.OMIT_FIELDS or field_name.startswith("_"):
                continue

            # Skip ID and name as they're in the header
            if field_name in ("id", "name", "display_name"):
                continue

            field_meta = fields_metadata.get(field_name, {}) if fields_metadata else {}
            field_type = field_meta.get("type", "unknown")

            if field_type in ("many2one", "one2many", "many2many"):
                relation_fields.append((field_name, field_value, field_meta))
            else:
                simple_fields.append((field_name, field_value, field_meta))

        # Format simple fields first
        if simple_fields:
            lines.append(f"{indent}Fields:")
            for field_name, field_value, field_meta in simple_fields:
                formatted_value = self._format_field_value(
                    field_name, field_value, field_meta, record_id=record.get("id")
                )
                lines.append(f"{indent}  {field_name}: {formatted_value}")

        # Format relationship fields
        if relation_fields:
            lines.append(f"{indent}Relationships:")
            for field_name, field_value, field_meta in relation_fields:
                lines.extend(
                    self._format_relation_field(
                        field_name,
                        field_value,
                        field_meta,
                        indent_level + 1,
                        related_summaries.get(field_name),
                        parent_id=record.get("id"),
                        enabled_relations=enabled_relations,
                    )
                )

        return "\n".join(lines)

    def _format_field_value(
        self,
        field_name: str,
        value: Any,
        field_meta: Dict[str, Any],
        record_id: Optional[int] = None,
    ) -> str:
        """Format a field value based on its type.

        ``record_id`` only feeds the truncation marker of a long value.
        """
        field_type = field_meta.get("type", "unknown")

        # Decide "Yes/No" vs "Not set" from the field TYPE, not the Python value:
        # Odoo returns False for an empty char/text/selection field, which must
        # read "Not set" -- only a real boolean field renders Yes/No. When the
        # type is unknown (metadata unavailable) fall back to the Python type so
        # a bare boolean still reads Yes/No.
        if field_type == "boolean" or (
            field_type == "unknown" and isinstance(value, bool)
        ):
            return "Yes" if value else "No"

        if value is None or value is False:
            return "Not set"

        if field_type in ("char", "text", "html"):
            return self._truncate_value(str(value), field_name, record_id)

        elif field_type in ("integer", "float", "monetary"):
            return self._format_numeric_value(value, field_type, field_meta)

        elif field_type in ("date", "datetime"):
            return self._format_datetime_value(value, field_type)

        elif field_type == "selection":
            # Try to get the human-readable selection value
            selection = field_meta.get("selection", [])
            for key, label in selection:
                if key == value:
                    return f"{label} ({value})"
            return str(value)

        elif field_type in ("one2many", "many2many"):
            # Search path: x2many values arrive as id lists. Render a count
            # rather than a raw id dump. (get_record routes x2many through
            # _format_relation_field, which can resolve names inline.)
            if isinstance(value, list):
                return f"{len(value)} record(s)" if value else "No records"
            return str(value)

        elif field_type in self.BINARY_FIELDS:
            return f"[Binary data - use {self.model}/{field_name} to retrieve]"

        # Unknown type (typically: field metadata unavailable)
        else:
            # Render many2one-shaped values readably instead of as a raw repr
            if (
                isinstance(value, (list, tuple))
                and len(value) == 2
                and isinstance(value[0], int)
                and isinstance(value[1], str)
            ):
                return f"{value[1]} (ID: {value[0]})"
            # Cap long values (e.g. base64 blobs read without metadata)
            return self._truncate_value(str(value), field_name, record_id)

    def _format_numeric_value(
        self, value: Any, field_type: str, field_meta: Dict[str, Any]
    ) -> str:
        """Format integer/float/monetary values with thousand separators."""
        if field_type == "monetary":
            # NOTE: currency-symbol formatting via currency_field is out of scope.
            return f"{value:,.2f}"
        if field_type == "float":
            # fields_get() returns Odoo's digits as a (precision, scale) tuple.
            digits = field_meta.get("digits", (16, 2))
            precision = (
                digits[1]
                if isinstance(digits, (tuple, list)) and len(digits) == 2
                else 2
            )
            return f"{value:,.{precision}f}"
        return f"{value:,}"

    def _format_datetime_value(self, value: Any, field_type: str) -> str:
        """Format date/datetime values to ISO 8601 with a UTC offset."""
        if isinstance(value, str):
            # Odoo read() emits datetimes as "%Y-%m-%d %H:%M:%S" (UTC); render
            # them as ISO 8601 with an explicit +00:00 offset.
            if field_type == "datetime" and " " in value:
                try:
                    dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
                    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
                except ValueError:
                    return value
            return value  # No known datetime format matched -- return as-is
        if isinstance(value, (datetime, date)):
            if isinstance(value, datetime):
                # Ensure datetime includes timezone
                return value.strftime("%Y-%m-%dT%H:%M:%S+00:00")
            # Date only
            return value.isoformat()
        return str(value)

    def _truncate_value(
        self,
        text: str,
        field_name: Optional[str] = None,
        record_id: Optional[int] = None,
    ) -> str:
        """Cap a formatted value to keep responses LLM-context friendly.

        The marker says where the rest is: a get_record call that names the
        field (``full_value_hint``), or else structuredContent, which always
        carries the whole value.
        """
        limit = self.max_field_length
        if len(text) <= limit:
            return text
        if self.full_value_hint:
            target = f"model='{self.model}'"
            if isinstance(record_id, int):
                target += f", record_id={record_id}"
            if field_name:
                target += f", fields=['{field_name}']"
            shown = self.full_value_limit
            if shown is not None and len(text) > shown:
                where = (
                    f"the get_record tool with {target} shows the first {shown:,} "
                    "characters, the full value is in structuredContent"
                )
            else:
                where = f"for the full value use the get_record tool with {target}"
        else:
            where = (
                f"{limit:,} characters is the text limit for one field, the full "
                "value is in structuredContent"
            )
        return f"{text[:limit]}... [truncated, {len(text)} chars total; {where}]"

    def _format_relation_field(
        self,
        field_name: str,
        value: Any,
        field_meta: Dict[str, Any],
        indent_level: int,
        summary: Optional[List[tuple]] = None,
        parent_id: Optional[int] = None,
        enabled_relations: Optional[set] = None,
    ) -> List[str]:
        """Format a relationship field.

        ``summary`` (x2many only) is a pre-resolved ``[(id, name), ...]`` list
        built by the tool layer for small collections under the caller's ACLs;
        when present the related records are listed inline, otherwise the field
        collapses to a count plus a search_records hint.

        ``parent_id`` / ``enabled_relations`` shape that hint: for a one2many the
        hint filters on the inverse field (``[[inverse, "=", parent_id]]``) so it
        returns the whole collection, and the hint is only offered when the
        related model is in ``enabled_relations`` (otherwise the suggested call
        would just error).
        """
        lines = []
        indent = "  " * indent_level
        field_type = field_meta.get("type", "unknown")

        if field_type == "many2one":
            if value and isinstance(value, (list, tuple)) and len(value) == 2:
                related_id, related_name = value
                related_model = field_meta.get("relation", "unknown")
                # Render the related record inline as "name (model,id)" rather
                # than an odoo:// link: this server's resources/read only
                # materialises record binary fields and attachments, so a
                # record URI would be a dead link. Use the get_record tool with
                # this model/id to fetch the full record.
                related_ref = f"{related_name} ({related_model},{related_id})"
                lines.append(f"{indent}{field_name}: {related_ref}")
            else:
                lines.append(f"{indent}{field_name}: Not set")

        elif field_type in ("one2many", "many2many"):
            if value and isinstance(value, list):
                count = len(value)
                related_model = field_meta.get("relation", "unknown")
                lines.append(f"{indent}{field_name}: {count} record(s)")

                if summary:
                    # Small collection: list the related records inline (names
                    # resolved by the tool layer under the caller's ACLs).
                    lines.append(f"{indent}  Items:")
                    for idx, (rid, rname) in enumerate(summary, 1):
                        lines.append(f"{indent}    [{idx}] {rname} (id {rid})")
                elif related_model != "unknown":
                    # No inline summary (collection above the cap, or the caller
                    # cannot read the related records): point at search_records so
                    # the model can still fetch them. Resource URIs cannot carry a
                    # domain, so emit a tool hint rather than a dead link.
                    lines.extend(
                        self._view_all_hint(
                            indent,
                            field_type,
                            field_meta,
                            value,
                            related_model,
                            parent_id,
                            enabled_relations,
                        )
                    )
            else:
                lines.append(f"{indent}{field_name}: No records")

        return lines

    def _view_all_hint(
        self,
        indent: str,
        field_type: str,
        field_meta: Dict[str, Any],
        value: List[int],
        related_model: str,
        parent_id: Optional[int],
        enabled_relations: Optional[set],
    ) -> List[str]:
        """Build the "view all related records" line for a collapsed relation."""
        if enabled_relations is not None and related_model not in enabled_relations:
            # The related model is not exposed via MCP, so a search_records call
            # on it would only error -- say so instead of advertising a dead hint.
            return [
                f"{indent}  (related model '{related_model}' is not enabled "
                f"for MCP access, so its records cannot be listed)"
            ]

        relation_field = field_meta.get("relation_field")
        if field_type == "one2many" and relation_field and isinstance(parent_id, int):
            # A one2many is exactly the records whose inverse many2one points
            # back here, so filter on that field: the hint then returns the
            # whole collection (no id cap) and stays compact.
            domain_str = json.dumps([[relation_field, "=", parent_id]])
            suffix = ""
        else:
            # many2many (no scalar inverse) or a computed one2many without one:
            # fall back to an id-in domain, capped so the hint stays bounded.
            related_ids = [item for item in value if isinstance(item, int)]
            domain_str = json.dumps([["id", "in", related_ids[:50]]])
            suffix = " (first 50 ids)" if len(related_ids) > 50 else ""
        return [
            f"{indent}  → View all: use the search_records tool with "
            f"model='{related_model}', domain={domain_str}{suffix}"
        ]

    def _get_record_summary(self, record: Dict[str, Any]) -> str:
        """Get a one-line summary of a record."""
        summary_fields = [
            "display_name",
            "name",
            "complete_name",
            "partner_id",
            "title",
        ]

        for field in summary_fields:
            if field in record and record[field]:
                value = record[field]
                if isinstance(value, (list, tuple)) and len(value) == 2:
                    return f"{value[1]} (ID: {value[0]})"
                elif isinstance(value, str):
                    return value

        return f"ID: {record.get('id', 'Unknown')}"


class DatasetFormatter:
    """Formats datasets and search results for LLM consumption."""

    # Ceiling on the inline field values of one result page. Every requested
    # field is inlined (a text-only client sees nothing else), but fields x
    # rows x max_field_length is otherwise unbounded; past this many
    # characters no further field value is shown (the budget is checked per
    # field, so it overshoots by one value at most), every row keeps its
    # summary line, and a note tells the reader how to narrow the request.
    # structuredContent is not affected.
    MAX_INLINE_TEXT_CHARS = 200_000

    def __init__(
        self,
        model: str,
        max_field_length: int = RecordFormatter.MAX_FIELD_DISPLAY_LENGTH,
        full_value_hint: bool = True,
        full_value_limit: Optional[int] = None,
    ):
        """Initialize the dataset formatter (arguments: see RecordFormatter)."""
        self.model = model
        self.record_formatter = RecordFormatter(
            model,
            max_field_length=max_field_length,
            full_value_hint=full_value_hint,
            full_value_limit=full_value_limit,
        )

    def format_search_results(
        self,
        records: List[Dict[str, Any]],
        domain: Optional[List] = None,
        fields: Optional[List[str]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        total_count: Optional[int] = None,
        fields_metadata: Optional[Dict[str, Any]] = None,
        next_hint: Optional[str] = None,
        prev_hint: Optional[str] = None,
        current_page: Optional[int] = None,
        total_pages: Optional[int] = None,
    ) -> str:
        """Format search results with context and pagination."""
        lines = [
            f"{'=' * 60}",
            f"Search Results: {self.model}",
            f"{'=' * 60}",
        ]

        # Add search context
        if domain:
            lines.append(f"Search criteria: {self._format_domain(domain)}")

        # Add pagination info
        if total_count is not None:
            showing = len(records)
            if current_page and total_pages:
                lines.append(f"Page {current_page} of {total_pages}")
            if offset is not None:
                lines.append(
                    f"Showing records {offset + 1}-{offset + showing} of {total_count}"
                )
            else:
                lines.append(f"Showing {showing} of {total_count} records")
        else:
            lines.append(f"Found {len(records)} records")

        if fields:
            lines.append(f"Fields: {', '.join(fields)}")

        lines.append("")

        # Format each record
        if not records:
            lines.append("No records found matching the criteria.")
        else:
            lines.extend(
                self._format_result_records(records, fields, offset, fields_metadata)
            )

        # Add navigation hints
        navigation = []
        if prev_hint:
            navigation.append(f"← Previous page: {prev_hint}")
        if next_hint:
            navigation.append(f"→ Next page: {next_hint}")

        if navigation:
            lines.append("\nNavigation:")
            lines.extend(navigation)

        # Add summary statistics for large datasets
        if total_count and total_count > 100:
            lines.append("\nDataset Summary:")
            lines.append(f"Total records: {total_count:,}")
            if domain:
                lines.append("Use additional filters to refine results")

        return "\n".join(lines)

    def _format_result_records(
        self,
        records: List[Dict[str, Any]],
        fields: Optional[List[str]],
        offset: Optional[int],
        fields_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """Render the record list block (with optional inline fields)."""
        metadata = fields_metadata or {}
        lines = []
        inline_chars = 0
        cut_at = None  # (record index, field) of the first value left out
        for idx, record in enumerate(records, 1):
            if offset:
                idx = offset + idx
            lines.append(f"[{idx}] {self.record_formatter._get_record_summary(record)}")

            # Inline every fetched field: the values were already read from the
            # database, and a text-only client (structuredContent hidden) would
            # otherwise re-read each record one by one via get_record. Bounded
            # by MAX_INLINE_TEXT_CHARS across the page (see the constant).
            if fields:
                for field in fields:
                    if field in record and field not in (
                        "id",
                        "name",
                        "display_name",
                    ):
                        value = record[field]
                        meta = metadata.get(field, {})
                        # List view: spend no line on empty values -- the header
                        # names every fetched column, so a missing line reads as
                        # "empty". Boolean False is a real value and stays
                        # (same type-vs-value distinction as the formatter).
                        ftype = meta.get("type", "unknown")
                        boolean_like = ftype == "boolean" or (
                            ftype == "unknown" and isinstance(value, bool)
                        )
                        if not boolean_like and (
                            value is None
                            or value is False
                            or value == ""
                            or value == []
                        ):
                            continue
                        # Checked per shown value, so the note only appears
                        # when a value is really left out.
                        if inline_chars > self.MAX_INLINE_TEXT_CHARS:
                            if cut_at is None:
                                cut_at = (idx, field)
                            break
                        # Delegate to the type-aware record formatter so an empty
                        # char field reads "Not set" (not "No") and a many2one
                        # renders "name (ID: id)" -- the field metadata, when
                        # available, drives the distinction.
                        formatted = self.record_formatter._format_field_value(
                            field, value, meta, record_id=record.get("id")
                        )
                        line = f"    {field}: {formatted}"
                        inline_chars += len(line)
                        lines.append(line)

            lines.append("")
        if cut_at is not None:
            lines.append(
                f"Field values are shown as text up to {self.MAX_INLINE_TEXT_CHARS:,} "
                f"characters; from record [{cut_at[0]}] on, starting at field "
                f"'{cut_at[1]}', field values are not shown as text (all values "
                "remain in structuredContent). Request fewer 'fields' or a smaller "
                "'limit' to see every value as text."
            )
            lines.append("")
        return lines

    def _format_domain(self, domain: List) -> str:
        """Format a search domain in human-readable form."""
        if not domain:
            return "All records"

        conditions = []
        for condition in domain:
            if isinstance(condition, (list, tuple)) and len(condition) == 3:
                field, operator, value = condition
                conditions.append(f"{field} {operator} {value}")
            elif condition in ("&", "|", "!"):
                conditions.append(condition)

        return " ".join(conditions) if conditions else str(domain)

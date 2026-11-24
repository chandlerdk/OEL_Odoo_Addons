"""Smart field selection for LLM-friendly payloads.

Field-importance scoring and smart default selection: every function here
operates on plain ``fields_get`` metadata (a dict of
``field_name -> field attributes``) and returns the chosen field list. The
caller is responsible for fetching ``fields_get`` from the live model.
"""

import logging
from typing import Any, Dict, List

_logger = logging.getLogger(__name__)

# Default cap on the number of fields a smart selection returns.
DEFAULT_MAX_SMART_FIELDS = 15

# Default cap on the number of fields the get_fields schema view returns when
# no explicit field_names are requested. Wider than the read-path cap: schema
# discovery needs relations and structure, not just readable values.
DEFAULT_MAX_SCHEMA_FIELDS = 60

# Essential fields always included when present on a model.
ESSENTIAL_FIELDS = ["id", "name", "display_name", "active"]

# Single-segment credential markers: a field is sensitive when one of these is
# its FINAL ``_``-delimited segment (``password``, ``user_password``,
# ``webhook_secret``, ``client_secret``, ``apikey``, ``auth_token``). Matched only
# as the last segment, so a benign descriptor whose name merely *starts* with the
# word (``password_expiry_date``) or carries the marker mid-name ahead of a
# metadata suffix (``access_token_expiry``) is not caught. ``token`` is a marker
# here because a trailing ``_token`` almost always names an access/refresh/
# verification credential; ``key`` is deliberately NOT a single-segment marker
# (too common as a benign suffix -- ``sort_key``), so it only counts inside the
# credential compounds below.
SENSITIVE_FIELD_MARKERS = (
    "password",
    "passwd",
    "secret",
    "apikey",
    "token",
)

# Multi-segment ``*_key`` credential markers: matched as CONSECUTIVE segments
# anywhere in the name (so ``openai_api_key`` matches ``api_key`` and
# ``stripe_secret_key`` matches ``secret_key``). This is the only way a ``key``
# field is flagged -- bare ``key`` is not a single-segment marker (``sort_key``
# is benign), so it counts solely inside these credential compounds.
SENSITIVE_MARKER_SEQUENCES = (
    ("api", "key"),
    ("private", "key"),
    ("secret", "key"),
    ("access", "key"),
)

# A leading boolean-flag segment (``is_secret``) or a trailing ``_id`` relational
# reference (``token_id``) never holds a credential value.
_BOOLEAN_FLAG_PREFIXES = ("is", "has", "can")

# Credential fields whose name has no marker. ``signup_url`` (auth_signup, Odoo
# 17) embeds the contact's signup token: with a pending invitation or password
# reset, the link signs in as that user.
SENSITIVE_FIELD_NAMES = frozenset({"signup_url"})


def is_sensitive_field_name(field_name: str) -> bool:
    """Whether ``field_name`` looks like it holds a credential value.

    Best-effort, name-based defense-in-depth for the *bulk* read paths only: it
    de-scores such a field out of the smart-default selection and strips it from
    an ``["__all__"]`` read. Explicitly-named fields are always honored -- Odoo
    field-level ``groups=`` is the real ACL, not this heuristic.

    A field is sensitive when its final ``_``-delimited segment is a credential
    marker (``*password``, ``*secret``, ``*_token``, ``*apikey``) or a credential
    ``*_key`` compound appears anywhere (``api_key``, ``secret_key``). Markers
    match only on whole segments, so ``secretary_id`` (segments
    ``secretary``/``id``) is not mistaken for a ``secret`` and ``sort_key`` is not
    a key. A trailing ``_id`` reference (``token_id``), a leading
    ``is_``/``has_``/``can_`` boolean flag (``is_secret``), and a metadata suffix
    that pushes the marker off the final segment (``password_expiry_date``,
    ``access_token_expiry``) are never treated as sensitive.
    """
    if field_name in SENSITIVE_FIELD_NAMES:
        return True
    segments = field_name.lower().split("_")
    if segments[-1] == "id" or segments[0] in _BOOLEAN_FLAG_PREFIXES:
        return False
    for sequence in SENSITIVE_MARKER_SEQUENCES:
        width = len(sequence)
        if any(
            tuple(segments[i : i + width]) == sequence
            for i in range(len(segments) - width + 1)
        ):
            return True
    return segments[-1] in SENSITIVE_FIELD_MARKERS


def score_field_importance(field_name: str, field_info: Dict[str, Any]) -> int:
    """Score a field's importance for smart default selection.

    Returns:
        Importance score (higher = more important; 0 = exclude)
    """
    # Tier 1: Essential fields (always included)
    if field_name in ESSENTIAL_FIELDS:
        return 1000

    # Exclude system/technical fields by prefix
    exclude_prefixes = ("_", "message_", "activity_", "website_message_")
    if field_name.startswith(exclude_prefixes):
        return 0

    # Exclude specific technical fields
    exclude_fields = {
        "write_date",
        "create_date",
        "write_uid",
        "create_uid",
        "__last_update",
        "access_token",
        "access_warning",
        "access_url",
    }
    if field_name in exclude_fields:
        return 0

    # Never auto-surface obviously-sensitive fields to the LLM. The exact-name
    # blocklist above misses custom fields like ``openai_api_key`` or
    # ``webhook_secret``, and the business-pattern bonus below could otherwise
    # even boost them.
    if is_sensitive_field_name(field_name):
        return 0

    score = 0

    # Tier 2: Required fields are very important
    if field_info.get("required"):
        score += 500

    # Tier 3: Field type importance
    field_type = field_info.get("type", "")
    type_scores = {
        "char": 200,
        "boolean": 180,
        "selection": 170,
        "integer": 160,
        "float": 160,
        "monetary": 140,
        "date": 150,
        "datetime": 150,
        "many2one": 120,  # Relations useful but not primary
        "text": 80,
        # one2many/many2many/binary/image/html are intentionally omitted: they
        # are excluded unconditionally below (return 0), so any score they got
        # here -- including the .get() default -- would be discarded anyway.
    }
    score += type_scores.get(field_type, 50)

    # Tier 4: Storage and searchability bonuses
    if field_info.get("store", True):
        score += 80
    if field_info.get("searchable", True):
        score += 40

    # Tier 5: Business-relevant field patterns (bonus)
    business_patterns = [
        "state",
        "status",
        "stage",
        "priority",
        "company",
        "currency",
        "amount",
        "total",
        "date",
        "user",
        "partner",
        "email",
        "phone",
        "address",
        "street",
        "city",
        "country",
        "code",
        "ref",
        "number",
    ]
    if any(pattern in field_name.lower() for pattern in business_patterns):
        score += 60

    # Cap non-stored (computed/related) fields: reading them into the default
    # field set triggers per-row compute on every fields-less get/search. Note
    # fields_get() has no ``compute`` key (there is no ``_description_compute``
    # in the ORM), so gate on ``store`` -- store=False <=> computed or related.
    if not field_info.get("store", True):
        score = min(score, 30)

    # Exclude large field types completely
    if field_type in ("binary", "image", "html"):
        return 0

    # Exclude one2many and many2many fields (can be large)
    if field_type in ("one2many", "many2many"):
        return 0

    return max(score, 0)


def score_field_schema_relevance(field_name: str, field_info: Dict[str, Any]) -> int:
    """Score a field's relevance for the schema view (``get_fields`` default).

    Reuses :func:`score_field_importance`, but keeps the field types that the
    read path excludes for payload size: x2many and binary/html fields carry no
    readable value by default, yet they ARE the model's structure and belong in
    a schema listing. They are rescored under a value-safe proxy type of
    comparable rank so the boilerplate/sensitive exclusions still apply.
    """
    field_type = field_info.get("type", "")
    if field_type in ("one2many", "many2many", "binary", "image", "html"):
        proxy_type = "many2one" if field_type in ("one2many", "many2many") else "text"
        return score_field_importance(field_name, dict(field_info, type=proxy_type))
    return score_field_importance(field_name, field_info)


def get_schema_default_fields(
    fields_info: Dict[str, Dict[str, Any]],
    max_fields: int = DEFAULT_MAX_SCHEMA_FIELDS,
) -> List[str]:
    """Pick the default field subset for the ``get_fields`` schema view.

    Keeps positive-scoring fields, takes the top ``max_fields`` (ties broken
    alphabetically for determinism), and always appends the essential fields
    present on the model.
    """
    scored = [
        (field_name, score_field_schema_relevance(field_name, field_info))
        for field_name, field_info in fields_info.items()
    ]
    scored = [(name, score) for name, score in scored if score > 0]
    scored.sort(key=lambda item: (-item[1], item[0]))

    selected = [name for name, _ in scored[:max_fields]]
    for field in ESSENTIAL_FIELDS:
        if field in fields_info and field not in selected:
            selected.append(field)
    return selected


def get_smart_default_fields(
    fields_info: Dict[str, Dict[str, Any]],
    max_fields: int = DEFAULT_MAX_SMART_FIELDS,
) -> List[str]:
    """Pick a smart default set of fields for a model.

    Scores every field by importance, keeps the positive-scoring ones, takes
    the top ``max_fields``, and always appends the essential fields that exist
    on the model.

    Returns:
        Ordered list of field names to read by default. Falls back to the
        essential fields present on the model if nothing scores positively.
    """
    field_scores = []
    for field_name, field_info in fields_info.items():
        score = score_field_importance(field_name, field_info)
        if score > 0:
            field_scores.append((field_name, score))

    field_scores.sort(key=lambda x: x[1], reverse=True)

    selected_fields = [field_name for field_name, _ in field_scores[:max_fields]]

    for field in ESSENTIAL_FIELDS:
        if field in fields_info and field not in selected_fields:
            selected_fields.append(field)

    _logger.debug(
        "Smart default fields: %s of %s fields (max configured: %s)",
        len(selected_fields),
        len(fields_info),
        max_fields,
    )
    return selected_fields

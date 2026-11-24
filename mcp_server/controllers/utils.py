import logging
import re
from typing import Dict, List, Optional, Union

from werkzeug.exceptions import RequestEntityTooLarge

import odoo
from odoo import _, modules
from odoo.api import Environment
from odoo.http import request
from odoo.modules.registry import Registry

_logger = logging.getLogger(__name__)

# The three GLOBAL switches (is_mcp_enabled / is_oauth_enabled /
# get_allowed_origins) are served from an @ormcache on ``mcp.enabled.model``
# (see mcp_enabled_models.py), exactly like the per-model gates. The cache is
# per-database-registry and invalidated cross-worker by ``registry.clear_cache``
# on any config change (config write + DB signaling), so flipping the master
# kill-switch off, disabling OAuth, or tightening the Origin allowlist
# propagates to every worker on the next request.


def clear_mcp_caches() -> None:
    """Invalidate the @ormcache backing the global MCP switches.

    ``is_mcp_enabled`` / ``is_oauth_enabled`` / ``get_allowed_origins`` (and the
    per-model gates) live in the registry's ``default`` ormcache pool. Production
    config writes flush it via ``registry.clear_cache()`` on the writing env
    (res.config.settings.set_values / mcp.enabled.model._invalidate_mcp_caches),
    which also signals the other workers. This helper does the same for a direct
    ``ir.config_parameter`` write -- or a test toggling a param outside those
    paths. It clears the bound request's registry first (the one actually serving
    this request), then every loaded registry as a fallback for a request-less
    caller (e.g. a test ``setUp``). Clearing the whole ``default`` pool mirrors
    what that write-side invalidation already does.
    """
    try:
        request.env.registry.clear_cache()
    except Exception:  # noqa: BLE001 - no bound request (e.g. test setUp)
        pass
    # Odoo 17 keeps the registries in an LRU map without values(); its
    # entries live in the ``d`` OrderedDict.
    for registry in list(Registry.registries.d.values()):
        registry.clear_cache()


def read_bounded_body(httprequest, cap: int) -> bytes:
    """Read the request body and refuse it with 413 above ``cap`` bytes.

    Werkzeug 2.0 (in Odoo 17) applies ``max_content_length`` to form parsing
    only, not to ``get_data()``, and a chunked body has no ``Content-Length``
    to compare. So the body is read here, at most ``cap + 1`` bytes, and cached
    on the request, where ``get_data()`` and ``get_json()`` find it.
    """
    # Odoo 17 wraps the Werkzeug request and does not expose its stream.
    httprequest = getattr(httprequest, "_HTTPRequest__wrapped", httprequest)
    # Core retries a request after a serialization failure
    # (service.model.retrying) and dispatches it again with the stream already
    # read: reuse the body of the first read.
    cached = getattr(httprequest, "_cached_data", None)
    if cached is not None:
        if len(cached) > cap:
            raise RequestEntityTooLarge()
        return cached
    if httprequest.content_length is not None and httprequest.content_length > cap:
        raise RequestEntityTooLarge()
    # A chunked stream returns at most one chunk per read, so read in a loop.
    chunks, size = [], 0
    while size <= cap:
        chunk = httprequest.stream.read(min(65536, cap + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    if size > cap:
        raise RequestEntityTooLarge()
    data = httprequest._cached_data = b"".join(chunks)
    return data


def sanitize_model_name(model_name: str) -> str:
    """Validate and strip a model name; raise ValueError if malformed."""
    if not model_name:
        raise ValueError("Model name cannot be empty")
    # Basic validation: only allow alphanumeric, dots, and underscores
    if not re.match(r"^[a-zA-Z0-9._]+$", model_name):
        raise ValueError(f"Invalid model name format: {_one_line(model_name)[:80]}")

    return model_name.strip()


def _sanitize_or_log(
    model_name: str, log_prefix: str = "Invalid model name"
) -> Optional[str]:
    """Sanitize a model name, logging and returning ``None`` if malformed.

    Centralizes the ``sanitize_model_name`` try/except the gate functions
    repeated; each caller maps ``None`` to its own denied sentinel.
    """
    try:
        return sanitize_model_name(model_name)
    except ValueError as e:
        _logger.warning("%s: %s", log_prefix, e)
        return None


def is_mcp_enabled() -> bool:
    """Whether MCP is globally enabled (``mcp_server.enabled``, default off).

    Delegates to the @ormcache-d ``mcp.enabled.model._get_mcp_enabled()``, so the
    value is served from the registry cache and invalidated cross-worker on any
    config change (``registry.clear_cache`` + DB signaling) -- the master
    kill-switch takes effect on every worker's next request, not after a TTL. A
    read error (e.g. no bound request) degrades to disabled (fail closed).
    """
    try:
        gates = request.env["mcp.enabled.model"].sudo()  # sudo: admin config read
        return gates._get_mcp_enabled()
    except Exception as e:
        _logger.error(f"Error checking if MCP is enabled: {e}")
        return False


def get_allowed_origins() -> tuple:
    """Parsed ``mcp_server.allowed_origins`` browser-Origin allowlist.

    Comma-separated Origin values (``scheme://host[:port]``), normalized to
    lowercase with trailing slashes stripped. An empty/unset parameter returns
    an empty tuple, which disables Origin validation entirely (permissive
    default -- see ``MCPDispatcher.pre_dispatch``). Served from the @ormcache-d
    ``mcp.enabled.model._get_allowed_origins()`` (cross-worker-invalidated on
    config change). A read error degrades to "no restriction" rather than
    403-ing every browser client on a transient failure -- the endpoint is
    bearer-authenticated, so Origin validation is defense in depth, not the
    authorization boundary.
    """
    try:
        gates = request.env["mcp.enabled.model"].sudo()  # sudo: admin config read
        return gates._get_allowed_origins()
    except Exception as e:
        _logger.error(f"Error reading the MCP Origin allowlist: {e}")
        return ()


def is_oauth_enabled(env: Environment) -> bool:
    """Whether the OAuth 2.1 front door is enabled (``mcp_server.enable_oauth``).

    Enabled by default -- an unset parameter counts as enabled -- so OAuth is
    available as soon as MCP itself is enabled; set the parameter to any value
    other than "True" to accept API-key auth only. Delegates to the @ormcache-d
    ``mcp.enabled.model._get_oauth_enabled()`` (cross-worker-invalidated on
    config change). A read error degrades to disabled (fail closed).
    """
    try:
        gates = env["mcp.enabled.model"].sudo()  # sudo: admin config read
        return gates._get_oauth_enabled()
    except Exception as e:
        _logger.error(f"Error checking if OAuth is enabled: {e}")
        return False


def is_model_mcp_enabled(env: Environment, model_name: str) -> bool:
    """
    Check if a specific model is MCP-enabled via
    `mcp.enabled.model.is_model_enabled()`, which is @ormcache-d (bounded and
    invalidated cross-worker on config change).
    """
    if not is_mcp_enabled():
        return False

    model_name = _sanitize_or_log(model_name)
    if model_name is None:
        return False

    try:
        mcp_models = env[
            "mcp.enabled.model"
        ].sudo()  # sudo: admin-managed MCP config, not user data
        return mcp_models.is_model_enabled(model_name)
    except Exception as e:
        _logger.error(f"Error checking if model {model_name} is MCP-enabled: {e}")
        return False


def check_model_operation_allowed(
    env: Environment, model_name: str, operation: str
) -> bool:
    """
    Check if a specific operation is allowed for an MCP-enabled model via
    `mcp.enabled.model.check_model_operation_enabled()`, which is @ormcache-d
    (bounded and invalidated cross-worker on config change).
    """
    if not is_mcp_enabled():
        return False

    model_name = _sanitize_or_log(model_name)
    if model_name is None:
        return False

    operation = str(operation).strip().lower()

    valid_operations = ["read", "create", "write", "unlink"]
    if operation not in valid_operations:
        _logger.warning(
            f"Invalid operation '{operation}' requested for model '{model_name}'"
        )
        return False

    if not is_model_mcp_enabled(env, model_name):
        return False

    try:
        mcp_models = env[
            "mcp.enabled.model"
        ].sudo()  # sudo: admin-managed MCP config, not user data
        return mcp_models.check_model_operation_enabled(model_name, operation)
    except Exception as e:
        message = (
            f"Error checking if operation {operation} "
            f"is allowed for model {model_name}: {e}"
        )
        _logger.error(message)
        return False


def check_model_method_allowed(env: Environment, model_name: str) -> bool:
    """
    Check if arbitrary public method calls (`call_model_method`) are allowed
    for a model. Returns True only when the model is MCP-enabled AND its
    per-model `allow_method_calls` opt-in flag is set (default off). The gate is
    @ormcache-d via `mcp.enabled.model.is_method_call_enabled()`.
    """
    if not is_mcp_enabled():
        return False

    model_name = _sanitize_or_log(model_name)
    if model_name is None:
        return False

    if not is_model_mcp_enabled(env, model_name):
        return False

    try:
        mcp_models = env[
            "mcp.enabled.model"
        ].sudo()  # sudo: admin-managed MCP config, not user data
        return mcp_models.is_method_call_enabled(model_name)
    except Exception as e:
        _logger.error(
            f"Error checking if method calls are allowed for model {model_name}: {e}"
        )
        return False


def get_enabled_models(env: Environment) -> List[Dict]:
    """
    Get a list of all MCP-enabled models. Each entry is a dict with "model"
    (technical name), "name" (display name) and "operations" (a dict of allowed
    read/create/write/unlink flags built from the loaded record).
    """
    if not is_mcp_enabled():
        return []

    try:
        enabled_model_records = (
            env["mcp.enabled.model"].sudo().search([("active", "=", True)])
        )

        # Batch fetch all model names to avoid N+1 queries
        model_names = enabled_model_records.mapped("model_name")
        ir_models = env["ir.model"].sudo().search([("model", "in", model_names)])

        model_name_map = {m.model: m.name for m in ir_models}

        models_info = []
        for record in enabled_model_records:
            if record.model_name in model_name_map:
                models_info.append(
                    {
                        "model": record.model_name,
                        "name": model_name_map[record.model_name],
                        # Built from the already-loaded recordset to avoid a
                        # per-model permission search by callers.
                        "operations": {
                            "read": record.allow_read,
                            "create": record.allow_create,
                            "write": record.allow_write,
                            "unlink": record.allow_unlink,
                        },
                    }
                )
            else:
                _logger.warning(f"Model {record.model_name} not found in ir.model")

        return models_info
    except Exception as e:
        _logger.error(f"Error fetching enabled models: {e}")
        return []


def get_model_allowed_operations(env: Environment, model_name: str) -> Dict[str, bool]:
    """
    Get a dict of allowed operations for a specific MCP-enabled model, keyed by
    operation name with boolean values. Returns an empty dict if the model is
    not found or not MCP-enabled.
    """
    if not is_mcp_enabled():
        return {}

    model_name = _sanitize_or_log(model_name)
    if model_name is None:
        return {}

    mcp_model_record = (
        env["mcp.enabled.model"]
        .sudo()
        .search([("model_name", "=", model_name), ("active", "=", True)], limit=1)
    )

    if not mcp_model_record:
        return {}

    return {
        "read": mcp_model_record.allow_read,
        "create": mcp_model_record.allow_create,
        "write": mcp_model_record.allow_write,
        "unlink": mcp_model_record.allow_unlink,
    }


XMLRPC_METHOD_OPERATION_MAP = {
    # Read operations
    "read": "read",
    "search": "read",
    "search_read": "read",
    "search_count": "read",
    "name_search": "read",
    "fields_get": "read",
    "default_get": "read",
    "name_get": "read",
    "get_metadata": "read",
    "get_formview_id": "read",
    "get_formview_action": "read",
    "read_group": "read",
    "formatted_read_group": "read",
    # Create operations
    "create": "create",
    "copy": "create",
    "name_create": "create",
    # Write operations
    "write": "write",
    "toggle_active": "write",
    "action_archive": "write",
    "action_unarchive": "write",
    "message_post": "write",
    # Unlink operations
    "unlink": "unlink",
    "action_delete": "unlink",
    # NB: destructive admin primitives (e.g. button_immediate_uninstall, which
    # UNINSTALLS a module) are deliberately NOT mapped -- an unmapped method is
    # denied by default in check_mcp_access, so the legacy XML-RPC proxy can
    # never translate one into a plain 'unlink' grant. export_data is not
    # mapped either: its field paths read related models that the MCP
    # configuration does not check.
}


def map_method_to_operation(method: str) -> Optional[str]:
    """
    Map an XML-RPC method to its operation type (read/write/create/unlink),
    or None if the method is not mapped.
    """
    method = str(method).lower().strip()
    return XMLRPC_METHOD_OPERATION_MAP.get(method)


def check_mcp_access(env: Environment, model_name: str, method_name: str) -> bool:
    """
    Check if an XML-RPC method is allowed for a given model via MCP configuration.
    """
    if not model_name or not method_name:
        _logger.warning(
            f"MCP: Invalid model or method name: {model_name}, {method_name}"
        )
        return False

    model_name = _sanitize_or_log(model_name, "MCP")
    if model_name is None:
        return False

    method_name = str(method_name).strip()

    if not is_mcp_enabled():
        _logger.info("MCP: Access denied because MCP is globally disabled.")
        return False

    model_exists = bool(
        env["ir.model"].sudo().search([("model", "=", model_name)], limit=1)
    )
    if not model_exists:
        message = f"MCP: Model {model_name} does not exist in this Odoo instance."
        _logger.warning(message)
        return False

    if not env["mcp.enabled.model"].sudo().is_model_enabled(model_name):
        message = (
            f"MCP: Access denied for XML-RPC method '{method_name}' "
            f"on model '{model_name}'. Model not MCP enabled."
        )
        _logger.info(message)
        return False

    operation = map_method_to_operation(method_name)
    if not operation:
        message = (
            f"MCP: XML-RPC method '{method_name}' on model '{model_name}' "
            f"has no defined MCP operation mapping. Access denied by default."
        )
        _logger.warning(message)
        return False

    operation_allowed = (
        env["mcp.enabled.model"]
        .sudo()
        .check_model_operation_enabled(model_name, operation)
    )

    if not operation_allowed:
        message = (
            f"MCP: Access denied for XML-RPC method '{method_name}' "
            f"(operation '{operation}') on model '{model_name}'. "
            f"Operation not allowed by MCP."
        )
        _logger.info(message)
        return False

    # Additional security check: Don't allow methods that start
    # with underscore (private methods)
    if method_name.startswith("_"):
        _logger.warning(
            f"MCP: Attempted access to private method {method_name}. Access denied."
        )
        return False

    message = (
        f"MCP: Access granted for XML-RPC method '{method_name}'"
        f" (operation '{operation}') on model '{model_name}'."
    )
    _logger.debug(message)

    return True


class UnreadableWriteValues(ValueError):
    """Write values or call arguments in a shape that the MCP gate does not read.

    Core accepts some of these shapes, a list of ``[key, value]`` pairs for one,
    so the gate refuses them instead of skipping what it cannot check.
    """


def _nested_command_operations(comodel, field, command, creating=False) -> tuple:
    """MCP operations that one x2many command needs on the field's comodel.

    ``field`` is the field that core writes (see :func:`_written_field`).
    Follows core's ``write_real``. Create (0), update (1) and delete (2) act
    on the comodel row for both field types. On a one2many, unlink (3), clear
    (5) and set (6) remove lines: core deletes them when the inverse field
    cascades and writes the inverse otherwise. Link (4) and set (6) write the
    inverse of the lines they add. While the record is created (``creating``),
    clear does nothing and set only links. On a stored many2many, 3 to 6
    change only the relation table. A field that is not stored but has an
    inverse method is written by that method, which can change the linked
    records, so 3 to 6 need write on them. The method can also delete the
    lines that it removes from a one2many, so removing them needs unlink too.
    """
    code = command[0]
    if code in (0, 1, 2):
        return ({0: "create", 1: "write", 2: "unlink"}[code],)
    if code not in (3, 4, 5, 6) or (
        field.type != "one2many" and (field.store or not field.inverse)
    ):
        return ()
    if creating and code in (5, 6):
        return ("write",) if code == 6 and len(command) > 2 and command[2] else ()
    if field.type != "one2many":
        return ("write",)
    if not field.store and field.inverse:
        removes = ("write", "unlink")
    else:
        inverse = comodel._fields.get(field.inverse_name)
        cascades = getattr(inverse, "ondelete", None) == "cascade"
        removes = ("unlink",) if cascades else ("write",)
    return {
        3: removes,
        4: ("write",),
        5: removes,
        6: tuple(dict.fromkeys(("write",) + removes)),
    }[code]


def _written_field(model, field, values, creating, defaults=None):
    """The field that core writes for an x2many value, and how it writes it.

    Returns ``(field, creating, replaces)``. A related field with an inverse,
    an ``_inherits`` field too, writes its last field. On create, core gives
    an ``_inherits`` value to the create of the parent record, or to its write
    when ``values``, or else the context ``defaults``, name the parent. It starts any
    other field that is not stored but has an inverse empty: the inverse then
    sets the value as the whole content of the field (``replaces``), so the
    current rows are removed.
    """
    replaces = False
    while field.related_field is not None and field.inverse:
        if field.inherited:
            parent_field = model._inherits[field.related_field.model_name]
            names_parent = (
                values[parent_field]
                if parent_field in values
                else (defaults or {}).get(parent_field)
            )
            creating = creating and not names_parent
        else:
            replaces = replaces or creating
            creating = False
        model = model.env[field.related_field.model_name]
        field = field.related_field
    if creating and not field.store and field.inverse:
        replaces, creating = True, False
    return field, creating, replaces


def _x2many_commands(value, field: str) -> list:
    """The commands that an x2many value stands for, as core's ``write_batch`` reads it.

    A tuple or a list of plain ids is the set command, ``False`` or ``None``
    the clear command. Core reads a dict in the list as a new record when it
    puts the value in the cache, so the list must hold only commands or only
    ids.

    :raises UnreadableWriteValues: for any other value.
    """
    if value is False or value is None:
        return [(5,)]
    if isinstance(value, (list, tuple)):
        if all(isinstance(command, (list, tuple)) and command for command in value):
            return list(value)
        if all(isinstance(i, (int, str)) and not isinstance(i, bool) for i in value):
            return [(6, 0, list(value))]
    raise UnreadableWriteValues(
        f"Field '{field}' takes a list of commands, such as [0, 0, {{...}}], "
        "or a list of ids."
    )


def _iter_x2many_commands(
    model, values, path: str = "", creating: bool = False, defaults=None
):
    """Yield ``(comodel, command, operations, field)`` for each x2many command.

    The values of a create or update command are walked again with the
    comodel, so a command at any depth is found. Core sets the inverse of a
    new one2many line to the record, so the walk counts that inverse as set.
    ``defaults`` maps the fields that ``default_`` keys of the context fill
    to their values. On create, core fills a missing x2many field from them.
    Values that are not a dict raise :class:`UnreadableWriteValues`.
    """
    if not isinstance(values, dict):
        raise UnreadableWriteValues(
            f"The values written to '{model._name}' must be an object (a dict)."
        )
    if creating and defaults:
        filled = {
            name: value
            for name, value in defaults.items()
            if name not in values
            and getattr(model._fields.get(name), "type", None)
            in ("one2many", "many2many")
        }
        values = {**filled, **values}
    for name, value in values.items():
        field = model._fields.get(name)
        if field is None or field.type not in ("one2many", "many2many"):
            continue
        comodel = model.env[field.comodel_name]
        field_path = f"{path}{name}"
        commands = _x2many_commands(value, field_path)
        written, writes_create, replaces = _written_field(
            model, field, values, creating, defaults
        )
        if replaces:
            commands = [(5,)] + commands
        for command in commands:
            operations = _nested_command_operations(
                comodel, written, command, writes_create
            )
            yield comodel, command, operations, field_path
            if command[0] in (0, 1):
                nested = command[2] if len(command) > 2 else None
                if (
                    command[0] == 0
                    and isinstance(nested, dict)
                    and written.type == "one2many"
                    and written.inverse_name
                ):
                    # The inverse can be the parent link of the line model:
                    # the new line then writes that parent.
                    nested = {**nested, written.inverse_name: True}
                yield from _iter_x2many_commands(
                    comodel,
                    nested,
                    f"{field_path}.",
                    creating=command[0] == 0,
                    defaults=defaults,
                )


def iter_nested_operations(
    model, values, path: str = "", creating: bool = False, defaults=None
):
    """Yield ``(comodel, operation, field)`` for each x2many command in ``values``.

    ``model`` is a recordset of the model that ``values`` are written to, and
    ``creating`` says that they create it.
    """
    for comodel, _command, operations, field in _iter_x2many_commands(
        model, values, path, creating, defaults
    ):
        for operation in operations:
            yield comodel._name, operation, field


def _attachment_ids(value) -> list:
    """The attachment ids that a command or a proxied call names, as ints."""
    ids = value if isinstance(value, (list, tuple)) else [value]
    if not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise UnreadableWriteValues("Attachment ids must be integers.")
    return list(ids)


def _owner_model(value, field: str):
    """The owner model that a ``res_model`` value names, or ``None`` for none."""
    if not value:
        return None
    if not isinstance(value, str):
        raise UnreadableWriteValues(f"Field '{field}' takes a model name.")
    return value


def _nested_attachment_owners(env: Environment, command, field: str):
    """``(owner, field)`` of the attachments that one command creates or changes.

    The new owner comes from the values of a create or update command, the
    current owner from the rows that the command names. Clear (5) is left
    out: the lines it removes belong to the record that is written.
    """
    code = command[0]
    owners = []
    if code in (0, 1) and len(command) > 2 and isinstance(command[2], dict):
        owners.append(_owner_model(command[2].get("res_model"), f"{field}.res_model"))
    ids = []
    if code in (1, 2, 3, 4) and len(command) > 1:
        ids = _attachment_ids(command[1])
    elif code == 6 and len(command) > 2:
        ids = _attachment_ids(command[2])
    if ids:
        owners.extend(env["ir.attachment"].browse(ids).exists().mapped("res_model"))
    return {(owner, f"{field}.res_model") for owner in owners if owner}


def denied_write_operation(
    env: Environment,
    model_name: str,
    records,
    values_list,
    creating: bool = False,
    defaults=None,
):
    """The first ``(model, operation, field)`` of a write that MCP does not allow.

    ``records`` are the rows that the write changes or deletes (empty for a
    create), ``values_list`` the values that it writes, and ``creating`` says
    that the values create records. Two rules apply in addition to the gate
    on ``model_name`` itself. Each x2many command needs its operation on the
    comodel. A write to ``ir.attachment``, directly or through a command,
    needs write on the owner model, before and after the write, as
    ``upload_attachment`` does. Returns ``None`` when MCP allows everything.

    :raises UnreadableWriteValues: for values in a shape the gate does not read.
    """
    owners = set()
    for values in values_list:
        for comodel, command, operations, field in _iter_x2many_commands(
            env[model_name], values, creating=creating, defaults=defaults
        ):
            for operation in operations:
                if not check_model_operation_allowed(env, comodel._name, operation):
                    return comodel._name, operation, field
            if comodel._name == "ir.attachment" and operations:
                owners.update(_nested_attachment_owners(env, command, field))
    if model_name == "ir.attachment":
        owners.update(
            (owner, "res_model") for owner in records.mapped("res_model") if owner
        )
        for values in values_list:
            owner = _owner_model(values.get("res_model"), "res_model")
            if owner:
                owners.add((owner, "res_model"))
    for owner, field in sorted(owners):
        if not check_model_operation_allowed(env, owner, "write"):
            return _one_line(owner), "write", field
    return None


# Where a proxied method takes its record ids and its values: the position in
# ``args``, then the keyword. ``None``: the method has no such argument.
_WRITE_METHOD_ARGUMENTS = {
    "create": (None, 0, "vals_list"),
    "write": (0, 1, "vals"),
    "copy": (0, 1, "default"),
    "name_create": (None, None, "name"),
    "unlink": (0, None, None),
}

# Methods whose call can create records, and so read defaults from the context.
_CREATING_METHODS = ("create", "copy", "name_create")


def legacy_write_operands(env: Environment, model_name: str, method: str, args, kwargs):
    """Records and values of a proxied call, for :func:`denied_write_operation`.

    The records are read only for ``ir.attachment``, the one model whose rule
    needs them.

    :raises UnreadableWriteValues: for values in a shape the gate does not read.
    """
    records = env[model_name].browse()
    spec = _WRITE_METHOD_ARGUMENTS.get(method)
    if spec is None:
        return records, []
    ids_index, values_index, keyword = spec
    if not isinstance(args, (list, tuple)):
        raise UnreadableWriteValues("The arguments must be an array.")
    kwargs = kwargs if isinstance(kwargs, dict) else {}
    values_list = []
    if values_index is not None:
        values = args[values_index] if len(args) > values_index else kwargs.get(keyword)
        if method == "copy" and not values:
            # Core's copy reads any false default as no default.
            values = None
        if method == "create" and isinstance(values, list):
            values_list = values
        elif values is not None or method != "copy":
            values_list = [values]
        if not all(isinstance(vals, dict) for vals in values_list):
            raise UnreadableWriteValues(
                f"The values of '{method}' must be an object (a dict)"
                + (" or a list of objects." if method == "create" else ".")
            )
    if model_name == "ir.attachment" and ids_index is not None:
        ids = args[ids_index] if len(args) > ids_index else []
        records = records.browse(_attachment_ids(ids)).exists()
    return records, values_list


# The context keys that a method that writes can send through the proxy, besides
# the keys of the setting. Core and standard modules read many other keys to skip
# a check that MCP does not gate. A ``default_`` key needs the setting too: some
# create methods check a field only in the values, before core fills it from the
# context default.
_PROXY_CONTEXT_KEYS = (
    "lang",
    "tz",
    "uid",
    "active_test",
    "allowed_company_ids",
    "bin_size",
    "tracking_disable",
    "mail_create_nolog",
    "mail_create_nosubscribe",
    "mail_notrack",
)

# Context keys that the setting cannot allow: with this one, core's create
# writes the XML id that an "id" key names, with no access check.
_REFUSED_CONTEXT_KEYS = {"_import_current_module": "it writes XML ids"}


def legacy_write_denial(env: Environment, model_name: str, method: str, args, kwargs):
    """Why MCP refuses a proxied write-type call, or ``None`` when it allows it.

    Besides the rules of :func:`denied_write_operation`, the call may pass
    keyword arguments only as a dict, and only ``context`` and the method's
    own argument. A method that writes can send only the context keys of
    ``_PROXY_CONTEXT_KEYS`` and the keys of the setting
    ``mcp_server.proxy_context_keys``. A default that the setting allows must
    be a plain value: core turns an x2many default into commands, and
    ``default_res_model`` would choose the owner of a new attachment.

    :raises UnreadableWriteValues: for a call shape the gate does not read.
    """
    if map_method_to_operation(method) != "read":
        # Core also reads a list of [key, value] pairs as keyword arguments or
        # as a context, so a method that writes takes only dicts here.
        if kwargs and not isinstance(kwargs, dict):
            raise UnreadableWriteValues("Keyword arguments must be an object (a dict).")
        call_context = (kwargs or {}).get("context")
        if call_context and not isinstance(call_context, dict):
            raise UnreadableWriteValues("The context must be an object (a dict).")
        # sudo: admin config read
        extra_keys = env["mcp.enabled.model"].sudo()._get_proxy_context_keys()
        for key in map(str, call_context or {}):
            if key in _REFUSED_CONTEXT_KEYS:
                return (
                    f"Access denied by MCP for the context key '{key}': "
                    f"{_REFUSED_CONTEXT_KEYS[key]}."
                )
            if key not in _PROXY_CONTEXT_KEYS and key not in extra_keys:
                return (
                    f"Access denied by MCP for the context key '{_one_line(key)}'. "
                    "An MCP Administrator can allow it in Settings > MCP Server > "
                    "Extra Context Keys for XML-RPC Writes."
                )
    spec = _WRITE_METHOD_ARGUMENTS.get(method)
    if spec is None:
        return None
    if not kwargs:
        # Core's execute_kw reads any empty value, an empty array too, as no
        # keyword arguments.
        kwargs = {}
    if not isinstance(kwargs, dict):
        raise UnreadableWriteValues("Keyword arguments must be an object (a dict).")
    for name in kwargs:
        if name not in ("context", spec[2]):
            raise UnreadableWriteValues(
                f"'{method}' takes no keyword argument '{_one_line(str(name))}' via "
                "MCP. Pass its values by position."
            )
    context = kwargs.get("context") or {}
    if not isinstance(context, dict):
        raise UnreadableWriteValues("The context must be an object (a dict).")
    records, values_list = legacy_write_operands(env, model_name, method, args, kwargs)
    creating = method in _CREATING_METHODS
    defaults = {
        str(key)[len("default_") :]: value
        for key, value in context.items()
        if str(key).startswith("default_")
    }
    if creating and not values_list:
        # name_create and a copy without a default create a record too, and
        # core fills its fields from the context defaults.
        values_list = [{}]
    if method != "unlink":
        for key, value in context.items():
            if str(key).startswith("default_") and isinstance(
                value, (list, tuple, dict)
            ):
                return (
                    f"Access denied by MCP for the context key '{_one_line(str(key))}':"
                    " a default in the context must be a plain value."
                )
        creates_attachment = (model_name == "ir.attachment" and creating) or any(
            comodel == "ir.attachment" and operation == "create"
            for values in values_list
            for comodel, operation, _field in iter_nested_operations(
                env[model_name], values, creating=creating, defaults=defaults
            )
        )
        for key in ("default_res_model", "default_res_id"):
            if creates_attachment and key in context:
                return (
                    f"Access denied by MCP for the context key '{key}': the owner "
                    "of an attachment must be in the values."
                )
    denied = denied_write_operation(
        env, model_name, records, values_list, creating, defaults
    )
    if denied:
        return (
            f"Access denied by MCP for operation '{denied[1]}' on "
            f"model '{denied[0]}' (field '{denied[2]}')."
        )
    return None


def get_allowed_xmlrpc_methods() -> List[str]:
    """Get a list of all XML-RPC methods that are mapped to MCP operations."""
    return list(XMLRPC_METHOD_OPERATION_MAP.keys())


def get_mcp_server_version() -> str:
    """Return the current MCP server version from the module's manifest."""
    try:
        manifest = modules.module.get_manifest("mcp_server")
        version = manifest.get("version", "1.0.0")
        return version
    except Exception as e:
        _logger.error(f"Error retrieving MCP server version from manifest: {e}")
        return "1.0.0"  # Fallback version


def get_system_info(env: Environment) -> Dict[str, Union[str, int]]:
    """
    Get system information including Odoo version, DB name, language,
    enabled MCP models count, MCP server version, and server timezone.
    """
    db_name = env.cr.dbname
    odoo_version = odoo.release.version

    # Current user's language or system default
    lang = env.context.get("lang")
    if not lang and env.user:
        lang = env.user.lang
    if not lang:
        lang = (
            env["ir.default"].sudo()._get("res.partner", "lang")
        )  # A common place for default lang
    if not lang:
        lang = env["ir.config_parameter"].sudo().get_param("base.language", "en_US")

    enabled_mcp_models_count = 0
    if is_mcp_enabled():
        enabled_mcp_models_count = (
            env["mcp.enabled.model"].sudo().search_count([("active", "=", True)])
        )

    server_timezone = env.context.get("tz")
    if not server_timezone and env.user:
        server_timezone = env.user.tz
    if not server_timezone:
        server_timezone = "UTC"

    return {
        "db_name": db_name,
        "odoo_version": odoo_version,
        "language": lang,
        "enabled_mcp_models": enabled_mcp_models_count,
        "mcp_server_version": get_mcp_server_version(),
        "server_timezone": server_timezone,
    }


# Always-safe fallback: the UTC datetime guidance never depends on user state,
# so it is served even when the personalized block cannot be built. Wrapped in a
# per-call helper (not a module-level constant) so ``_()`` translates it in the
# request's language on each call, not once at import in the server's language.
def _utc_datetime_guidance():
    return _(
        "Datetime handling:\n"
        "- All datetimes stored and returned by Odoo are in UTC.\n"
        "- Provide datetimes to tools in UTC.\n"
        "- Convert to the user's timezone only for display."
    )


# Cross-tool workflow guidance for initialize.instructions. Per-tool detail
# belongs in the tool descriptions; this block only carries what no single
# description can: which tool to reach for, and the batch-over-loop rule.
def _usage_guidance():
    return _(
        "Usage guidance:\n"
        "- Discovery: list_models shows the accessible models; get_fields "
        "describes a model's schema (most relevant fields by default).\n"
        "- Reads: search_records is the primary tool. Filter with 'domain', "
        "request only the columns you need with 'fields', and read many known "
        'records in one call with [["id", "in", ids]] instead of one '
        "get_record call per ID. Prefer one call with a larger 'limit' over "
        "many small pages. For counts and per-group totals, use "
        "aggregate_records instead of listing rows. search_records cuts long "
        "text values. To read up to 100,000 characters of one, call get_record "
        "with that field named in 'fields'.\n"
        "- Writes: create_record / update_record / delete_record and "
        "post_message run as the authenticated user, so Odoo's access rights "
        "and validations apply. To create many records, or update many "
        "records (shared values via record_ids + values, or per-record "
        "values via updates), use create_records / update_records (one "
        "atomic call) instead of looping single calls. call_model_method "
        "triggers business actions on models where it is enabled.\n"
        "- Attachments and binary fields: get_record / search_records return "
        "binary fields as odoo:// URIs; pass a URI (or an attachment id) to "
        "read_attachment to get the content (text, image, extracted PDF text "
        "or a download link). list_record_attachments lists the files "
        "attached to a record; upload_attachment adds one."
    )


def _one_line(value: str) -> str:
    """Collapse CR/LF in an interpolated value to a single space.

    User-editable fields (display name, login, company name) are injected
    verbatim into the plain-text context block that clients feed to the LLM as
    instructions. Stripping embedded newlines stops a crafted value from forging
    extra context lines (prompt injection into the caller's own session).
    """
    return str(value).replace("\r", " ").replace("\n", " ")


def build_user_context(env: Environment) -> str:
    """Build the personalized user-context block for ``initialize.instructions``.

    A compact plain-text summary (no HTML) of the calling user's identity,
    timezone and company scope, plus the fixed UTC datetime guidance. Injected by
    spec-compliant MCP clients into the model context on connect, and also
    returned verbatim by the ``get_current_context`` tool. Only ever describes
    the caller's own user -- no new data surface.

    Mirrors the defensive style of this module: on any failure it logs and falls
    back to the always-safe UTC guidance so ``initialize`` still yields useful
    instructions.
    """
    try:
        user = env.user
        timezone_line = user.tz or "UTC (user has no timezone set)"
        company = user.company_id
        lines = [
            _("You are connected to Odoo via MCP as:"),
            _(
                "- User: %(name)s (login: %(login)s)",
                name=_one_line(user.display_name),
                login=_one_line(user.login),
            ),
            _("- Timezone: %(tz)s", tz=timezone_line),
            _(
                "- Active company: %(name)s (ID: %(id)s)",
                name=_one_line(company.display_name),
                id=company.id,
            ),
        ]
        allowed_companies = user.company_ids
        if len(allowed_companies) > 1:
            names = ", ".join(
                f"{_one_line(c.display_name)} (ID: {c.id})" for c in allowed_companies
            )
            lines.append(_("- Allowed companies: %(names)s", names=names))
        lines.append("")
        lines.append(_utc_datetime_guidance())
        lines.append("")
        lines.append(_usage_guidance())
        return "\n".join(lines)
    except Exception as e:
        _logger.error(f"Error building MCP user context: {e}")
        return _utc_datetime_guidance()

from odoo import _, api, fields, models, tools
from odoo.exceptions import ValidationError


class McpEnabledModel(models.Model):
    """Model to store which Odoo models are enabled for MCP access."""

    _name = "mcp.enabled.model"
    _description = "MCP Enabled Model"
    _rec_name = "model_id"

    model_id = fields.Many2one(
        "ir.model",
        string="Model",
        required=True,
        index=True,
        ondelete="cascade",
        help="The Odoo model to be enabled for MCP access",
    )
    model_name = fields.Char(
        related="model_id.model", string="Technical Name", store=True, readonly=True
    )
    active = fields.Boolean(
        default=True,
        help="Indicates whether this model is enabled for MCP access",
    )
    allow_read = fields.Boolean(default=True, help="Allow read operations through MCP")
    allow_create = fields.Boolean(
        default=False, help="Allow create operations through MCP"
    )
    allow_write = fields.Boolean(
        string="Allow Update", default=False, help="Allow update operations through MCP"
    )
    allow_unlink = fields.Boolean(
        string="Allow Delete", default=False, help="Allow delete operations through MCP"
    )
    allow_method_calls = fields.Boolean(
        default=False,
        help="Admin opt-in (off by default): let MCP clients invoke this "
        "model's own PUBLIC BUSINESS methods through call_model_method, e.g. "
        "action_confirm, message_subscribe, activity_schedule. Always blocked "
        "regardless of this flag: private methods (a leading underscore, or "
        "marked @api.private in Odoo), the "
        "public web_* family (web_save/web_read/web_search_read/...) and the "
        "generic Odoo ORM/CRUD/data-access helpers (create/read/write/unlink/"
        "search/browse/mapped/filtered/copy_data/... -- every method of "
        "BaseModel). A permitted business method that maps to a CRUD operation "
        "is additionally gated by the matching per-operation permission above "
        "(e.g. message_post -> write, default_get -> read, action_unlink -> "
        "unlink), so this flag never grants an operation whose per-op permission "
        "is off. Note that business methods may have side effects on this and "
        "related models (posting messages, scheduling activities, managing "
        "followers, etc.). Everything runs under the calling user's Odoo access "
        "rights (ACL and record rules) -- a method can only do what that user "
        "could already do in Odoo. Enable only for trusted models and users.",
    )
    notes = fields.Text(help="Additional notes about this model configuration")

    _sql_constraints = [
        (
            "unique_model",
            "UNIQUE(model_id)",
            "A model can only be enabled once for MCP access.",
        )
    ]

    @api.constrains("model_id")
    def _check_model_not_internal(self):
        """Keep the module's own models (``mcp.*``) unselectable for MCP access.

        Mirrors the wizard's ``("model", "not like", "mcp.%")`` exclusion at the
        model level so the guard also holds for a direct form/RPC create/write --
        exposing e.g. ``mcp.oauth.token`` would surface token hashes over MCP.
        """
        for record in self:
            technical_name = record.model_id.model
            if technical_name and technical_name.startswith("mcp."):
                raise ValidationError(
                    _(
                        "The model '%(name)s' is internal to the MCP server and "
                        "cannot be exposed for MCP access.",
                        name=technical_name,
                    )
                )

    # ------------------------------------------------------------------
    # Cache invalidation on config change
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self._invalidate_mcp_caches()
        return records

    def write(self, vals):
        result = super().write(vals)
        self._invalidate_mcp_caches()
        return result

    def unlink(self):
        result = super().unlink()
        self._invalidate_mcp_caches()
        return result

    def _invalidate_mcp_caches(self):
        """Drop the MCP permission caches so config changes take effect.

        Every MCP decision -- the per-model enablement/operation/method-call
        gates AND the global on/off switches (``_get_mcp_enabled`` /
        ``_get_oauth_enabled`` / ``_get_allowed_origins``) -- is
        ``@ormcache``-decorated, so ``registry.clear_cache`` invalidates them all
        AND signals the other HTTP workers to do the same via registry signaling.
        A disabled model (or a flipped master switch) stops being served
        everywhere on the next request, not after a TTL.
        """
        self.env.registry.clear_cache()

    # ------------------------------------------------------------------
    # Global on/off switches (@ormcache-d, cross-worker-invalidated)
    #
    # These read admin-managed ``ir.config_parameter`` values, not per-model
    # rows, but they live here alongside the per-model gates so the same
    # ``registry.clear_cache()`` invalidation (config write / DB signaling)
    # propagates the master kill-switch, the OAuth switch and the Origin
    # allowlist to every worker on the next request -- not after a TTL. Keyed
    # only on ``(model_name=self._name, method)`` (no args), i.e. one entry per
    # database registry. ``controllers/utils`` exposes the thin, fail-safe
    # wrappers callers use.
    # ------------------------------------------------------------------
    @api.model
    @tools.ormcache()
    def _get_mcp_enabled(self):
        """Global MCP enable switch (``mcp_server.enabled``, default off)."""
        return (
            self.env["ir.config_parameter"]
            .sudo()  # sudo: admin-managed global enable switch, not user data
            .get_param("mcp_server.enabled", "False")
            == "True"
        )

    @api.model
    @tools.ormcache()
    def _get_oauth_enabled(self):
        """OAuth front-door switch (``mcp_server.enable_oauth``, default on)."""
        return (
            self.env["ir.config_parameter"]
            .sudo()  # sudo: admin-managed OAuth enable switch, not user data
            .get_param("mcp_server.enable_oauth", "True")
            == "True"
        )

    @api.model
    @tools.ormcache()
    def _get_allowed_origins(self):
        """Parsed browser-Origin allowlist (``mcp_server.allowed_origins``).

        Comma-separated Origins normalized to lowercase with trailing slashes
        stripped; an empty/unset param yields an empty tuple (Origin validation
        disabled). Returns a tuple so the cached value is hashable/immutable.
        """
        raw = (
            self.env["ir.config_parameter"]
            .sudo()  # sudo: admin-managed system param, not user data
            .get_param("mcp_server.allowed_origins", "")
        )
        return tuple(
            entry.strip().rstrip("/").lower()
            for entry in (raw or "").split(",")
            if entry.strip()
        )

    @api.model
    @tools.ormcache()
    def _get_proxy_context_keys(self):
        """Context keys that the setting ``mcp_server.proxy_context_keys``
        allows on write calls through the XML-RPC proxy, as a tuple.

        Context keys are case-sensitive, so only the spaces are stripped.
        """
        raw = (
            self.env["ir.config_parameter"]
            .sudo()  # sudo: admin-managed system param, not user data
            .get_param("mcp_server.proxy_context_keys", "")
        )
        return tuple(key.strip() for key in (raw or "").split(",") if key.strip())

    @api.model
    @tools.ormcache("model_name")
    def is_model_enabled(self, model_name):
        """Check if a model is enabled for MCP access.

        ``@ormcache``-d on ``model_name`` (bounded, per-registry LRU); the cache
        is cleared cross-worker by ``_invalidate_mcp_caches`` on any config
        change.
        """
        record = self.search(
            [("model_name", "=", model_name), ("active", "=", True)], limit=1
        )
        return bool(record)

    @api.model
    @tools.ormcache("model_name", "operation")
    def check_model_operation_enabled(self, model_name, operation):
        """Check if a specific operation is enabled for a model (``@ormcache``-d)."""
        if operation not in ["read", "create", "write", "unlink"]:
            raise ValidationError(
                _("Invalid operation: %(operation)s", operation=operation)
            )

        record = self.search(
            [("model_name", "=", model_name), ("active", "=", True)], limit=1
        )

        if not record:
            return False

        return bool(record["allow_" + operation])

    @api.model
    @tools.ormcache("model_name")
    def is_method_call_enabled(self, model_name):
        """Whether ``call_model_method`` is allowed for a model (``@ormcache``-d).

        True only when the model is enabled AND its ``allow_method_calls``
        opt-in flag is set. Cleared cross-worker like the sibling gate methods.
        """
        record = self.search(
            [("model_name", "=", model_name), ("active", "=", True)], limit=1
        )
        return bool(record and record.allow_method_calls)

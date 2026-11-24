from odoo import api, fields, models

# The boolean settings, with the parameter and default the runtime readers use:
# a switch is on only when its parameter holds the string "True".
_BOOLEAN_PARAMS = {
    "mcp_enabled": ("mcp_server.enabled", "False"),
    "mcp_enable_oauth": ("mcp_server.enable_oauth", "True"),
    "mcp_enable_logging": ("mcp_server.enable_logging", "True"),
    "mcp_enable_rate_limiting": ("mcp_server.enable_rate_limiting", "False"),
}


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    mcp_enabled = fields.Boolean(
        string="Enable MCP Access",
        help="Turns the MCP server on or off. When it is off, the /mcp endpoint, the "
        "REST routes and the XML-RPC proxy refuse all requests. Only members of "
        "the MCP User group can connect.",
        config_parameter="mcp_server.enabled",
        default=False,
    )
    mcp_enable_oauth = fields.Boolean(
        config_parameter="mcp_server.enable_oauth",
        default=True,
        string="Enable OAuth 2.1 Access",
        help="Lets browser MCP clients, such as Claude.ai and Gemini, connect after "
        "the user logs in to Odoo. API keys work in both cases. When this is "
        "off, the /mcp/oauth/* routes and OAuth discovery answer HTTP 404. On "
        "by default.",
    )
    mcp_request_limit = fields.Integer(
        string="Request Limit per Minute",
        help="Requests per user per minute. Past this number, the server answers "
        "HTTP 429 until the minute ends. Pings do not count. Values from 1 to 9 "
        "count as 10. Set 0 for no limit. Default: 300.",
        config_parameter="mcp_server.request_limit",
        default=300,
    )
    mcp_enable_logging = fields.Boolean(
        string="Enable Request Logging",
        help="Writes one audit row per MCP call to the MCP log: user, IP address, "
        "tool, model, record IDs, duration and errors. The rows store argument "
        "names and result sizes, never field values. When this is off, nothing "
        "is logged, failed logins included.",
        config_parameter="mcp_server.enable_logging",
        default=True,
    )
    mcp_enable_rate_limiting = fields.Boolean(
        string="Enable Rate Limiting",
        help="Limits the requests per minute of each user on /mcp, the REST routes "
        "and the XML-RPC proxy. Each Odoo worker counts on its own, so with "
        "several workers a user can make up to (workers x limit) requests. For "
        "a strict limit, use a reverse proxy. Off by default.",
        config_parameter="mcp_server.enable_rate_limiting",
        default=False,
    )
    mcp_log_retention_days = fields.Integer(
        string="Log Retention (days)",
        help="Number of days to keep MCP log rows. A daily scheduled action deletes "
        "older rows. Set 0 to keep all rows. Default: 30.",
        config_parameter="mcp_server.log_retention_days",
        default=30,
    )
    mcp_default_limit = fields.Integer(
        string="Default Record Limit",
        help="Number of records that search_records, aggregate_records and "
        "list_record_attachments return when the client sends no limit. "
        "Default: 25.",
        config_parameter="mcp_server.default_limit",
        default=25,
    )
    mcp_max_limit = fields.Integer(
        string="Maximum Record Limit",
        help="Largest number of records that search_records, aggregate_records and "
        "list_record_attachments return in one call. A larger limit from the "
        "client is reduced to this value. Default: 100.",
        config_parameter="mcp_server.max_limit",
        default=100,
    )
    mcp_max_batch_size = fields.Integer(
        string="Maximum Batch Size",
        help="Largest number of entries in one create_records or update_records "
        "call. The server refuses a larger batch and asks the client to split "
        "it. Default: 100.",
        config_parameter="mcp_server.max_batch_size",
        default=100,
    )
    mcp_max_smart_fields = fields.Integer(
        string="Maximum Smart Fields",
        help="When the client lists no fields, the server picks the most useful "
        "fields, up to this number. This keeps the answers short for the AI "
        "model. Default: 15.",
        config_parameter="mcp_server.max_smart_fields",
        default=15,
    )
    mcp_max_related_items = fields.Integer(
        string="Maximum Related Items",
        help="get_record lists up to this many linked records by name. A larger "
        "one-to-many or many-to-many field shows a count and a search hint "
        "instead. Set 0 to always show the count. Default: 3.",
        config_parameter="mcp_server.max_related_items",
        default=3,
    )
    mcp_max_field_length = fields.Integer(
        string="Maximum Text Length",
        help="search_records, and get_record without named fields (smart defaults or "
        '["__all__"]), show up to this many characters of a text field. A longer value '
        "ends with a note that tells the AI to call get_record with the field name, "
        "which shows up to 100000 characters. Maximum: 100000. Set 0 to use the "
        "default. Default: 2000.",
        config_parameter="mcp_server.max_field_length",
        default=2000,
    )
    mcp_link_ttl_hours = fields.Integer(
        string="Download Link Validity (hours)",
        help="Hours that a read_attachment download link stays valid. Anyone with "
        "the link can download the file without an Odoo login, and a single "
        "link cannot be revoked. Keep it short. The AI client asks for a new "
        "link when one expires. Maximum: 1008 hours (42 days). Set 0 to use the "
        "default. Default: 1.",
        config_parameter="mcp_server.link_ttl_hours",
        default=1,
    )
    mcp_allowed_origins = fields.Char(
        string="Allowed Browser Origins",
        help="Comma-separated browser origins that can call /mcp, for example "
        "https://app.example.com. When the list is empty, all origins are "
        "accepted. When it is set, a request from another origin gets HTTP 403. "
        "Native clients, such as Claude Desktop and Cursor, send no Origin "
        "header and are always accepted.",
        config_parameter="mcp_server.allowed_origins",
    )
    mcp_proxy_context_keys = fields.Char(
        string="Extra Context Keys for XML-RPC Writes",
        help="Comma-separated context keys that a write call through the XML-RPC proxy "
        "(/mcp/xmlrpc/object) can send, on top of the built-in keys. A default_ key "
        "counts too, for example default_partner_id. A write call with any other "
        "key gets fault 403, and the message names the key. Some keys make Odoo "
        "skip a business check. For example, check_move_validity lets a journal "
        "entry stay unbalanced, and default_state can create a posted journal "
        "entry. Add a key only when an integration needs it. Reads and the /mcp "
        "endpoint do not change.",
        config_parameter="mcp_server.proxy_context_keys",
    )

    @api.model
    def get_values(self):
        # Odoo 17 reads a boolean config parameter with bool(), so the stored
        # string "False" would show as ticked, and a save would turn the switch
        # back on. Read the switches the way the runtime does.
        res = super().get_values()
        params = self.env["ir.config_parameter"].sudo()
        for name, (key, default) in _BOOLEAN_PARAMS.items():
            res[name] = params.get_param(key, default) == "True"
        return res

    def set_values(self):
        result = super().set_values()
        # sudo: admin-managed system params; Settings is gated by Administration.
        params = self.env["ir.config_parameter"].sudo()  # admin-managed config

        # The runtime readers (utils.is_mcp_enabled, rate limiting) expect the
        # literal "True"/"False"/"0" strings. The base mechanism stores a
        # boolean False (or integer 0) by deleting the param, which would fall
        # the readers back to their defaults -- so write every value explicitly.
        params.set_param("mcp_server.enabled", str(self.mcp_enabled))
        params.set_param("mcp_server.enable_oauth", str(self.mcp_enable_oauth))
        params.set_param("mcp_server.request_limit", str(self.mcp_request_limit))
        params.set_param("mcp_server.enable_logging", str(self.mcp_enable_logging))
        params.set_param(
            "mcp_server.enable_rate_limiting", str(self.mcp_enable_rate_limiting)
        )
        params.set_param(
            "mcp_server.log_retention_days", str(self.mcp_log_retention_days)
        )
        params.set_param("mcp_server.default_limit", str(self.mcp_default_limit))
        params.set_param("mcp_server.max_limit", str(self.mcp_max_limit))
        params.set_param("mcp_server.max_batch_size", str(self.mcp_max_batch_size))
        params.set_param("mcp_server.max_smart_fields", str(self.mcp_max_smart_fields))
        params.set_param(
            "mcp_server.max_related_items", str(self.mcp_max_related_items)
        )
        params.set_param("mcp_server.max_field_length", str(self.mcp_max_field_length))
        params.set_param("mcp_server.link_ttl_hours", str(self.mcp_link_ttl_hours))
        # A Char: write the stripped value; empty deletes the param, which the
        # runtime reader (utils.get_allowed_origins) reads as "no restriction".
        params.set_param(
            "mcp_server.allowed_origins", (self.mcp_allowed_origins or "").strip()
        )

        # Flush the @ormcache backing every MCP decision -- the per-model gates
        # AND the global switches (is_mcp_enabled / is_oauth_enabled /
        # get_allowed_origins) -- so a config change (e.g. the master kill-switch)
        # takes effect immediately. registry.clear_cache() also signals the other
        # workers via DB signaling, so the change propagates cross-worker on their
        # next request rather than after a TTL.
        self.env.registry.clear_cache()

        return result

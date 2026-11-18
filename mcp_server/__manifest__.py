{
    "name": "MCP Server",
    "version": "17.0.2.3.0",
    "summary": "Connect AI assistants to your Odoo instance via Model Context Protocol",
    "description": """
MCP Server for Odoo
===================

Enable AI assistants like Claude to securely
access your Odoo data through natural language queries.

Key Features
------------
* Native MCP endpoint at POST /mcp (Streamable HTTP, JSON-RPC 2.0) -
  connect MCP clients directly to Odoo, no separate process to install
* Search, retrieve, create, update and delete Odoo records, run aggregations
  and call business methods
* Batch writes: create_records and update_records write many records of one
  model in a single atomic call (capped by a configurable batch size)
* Attachments: read_attachment returns a PDF, image or file attached to a
  record as text, image or a time-limited download link from any client;
  list_record_attachments and upload_attachment complete the loop
* User context on connect: the handshake advertises the caller's timezone,
  active and allowed companies (plus a get_current_context tool) so the model
  writes to the right company and stops guessing timezones
* Dedicated "MCP only" API-key scope: mint a key from My Profile that
  authenticates only on /mcp (a smaller blast radius when leaked)
* OAuth read-only consent: at the login/consent screen a user can turn off the
  "Create and modify data" switch to grant a read-only (mcp:read)
  session that cannot call - or even see - write tools
* Custom tools: admins expose curated verbs (e.g. confirm_sale_order) by
  wrapping an Odoo server action, instead of enabling generic create/write
* Per-user opt-in: only members of the "MCP User" security group can use MCP
* Granular permissions control per model and operation
* Secure API key authentication with rate limiting and audit logging
* Built-in OAuth 2.1 Authorization Server: browser MCP clients (Claude.ai, Gemini)
  can connect by logging into Odoo, in addition to API keys
* Easy configuration through Odoo settings

How It Works
------------
1. Install this module and configure model access
2. Add each user to the "MCP User" security group
3. Generate an API key for authentication
4. Point any MCP client (Claude, Cursor, VS Code, MCP Inspector) at your
   Odoo URL's /mcp endpoint
5. Start querying your Odoo data naturally

Requirements: Odoo 17.0. The native /mcp endpoint needs no extra software;
the module's legacy XML-RPC endpoints remain available for programmatic/RPC access.
    """,
    "author": "much. Consulting",
    "website": "https://muchconsulting.com/",
    "support": "product@erp.muchconsulting.de",
    "category": "Productivity",
    "depends": ["base", "base_setup", "mail", "web"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "wizard/mcp_model_selection_wizard_views.xml",
        "wizard/oauth_bulk_confirm_wizard_views.xml",
        "wizard/oauth_client_secret_wizard_views.xml",
        "views/mcp_enabled_models_views.xml",
        "views/mcp_custom_tool_views.xml",
        "views/mcp_log_views.xml",
        "views/oauth_views.xml",
        "views/res_config_settings_views.xml",
        "views/res_users_apikeys_views.xml",
        "views/mcp_menu.xml",
        "views/oauth_consent_templates.xml",
        "data/oauth_cron.xml",
    ],
    "demo": [],
    "assets": {
        "web.assets_backend": [
            "mcp_server/static/src/xml/setting_help_icon.xml",
        ],
    },
    "images": [
        "static/description/banner.gif",
        "static/description/icon.png",
    ],
    "external_dependencies": {
        "python": [
            "authlib>=1.6.12,<1.7.0",
            "defusedxml",
            "packaging",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
    "license": "OPL-1",
}

MCP Server for Odoo
===================================

Overview
--------

The MCP Server module enables AI assistants to securely access and interact with your
Odoo data through the Model Context Protocol (MCP). The module speaks MCP natively: it
exposes a built-in MCP endpoint at ``/mcp`` (Streamable HTTP, JSON-RPC 2.0, protocol
revisions ``2025-11-25`` and ``2025-06-18``), so any MCP client (Claude.ai, Claude
Desktop, Claude Code,
Cursor, VS Code, MCP Inspector) connects directly to your Odoo URL with no separate
process to install. Supports full CRUD operations - create, read, update, and delete
records through natural language.

There are three ways to connect a client, from simplest to most involved:

1. **Log in with Odoo (OAuth 2.1)** — paste the Odoo MCP URL into the client and sign
   in with a normal Odoo login. No keys to create or copy. Recommended.
2. **API key (Bearer token)** — mint a key once and configure it as an
   ``Authorization: Bearer`` header.
3. **Standalone local client** (``uvx mcp-server-odoo``) — a bridge process for
   stdio-only MCP clients, using the same module APIs.

Whichever way a client connects, every call runs as a real Odoo user and shares the
same per-model permissions (``mcp.enabled.model``), audit log and rate limiting.

Installation
------------

1. Download and install the module in your Odoo instance (requires the ``authlib``
   (``>=1.6.12,<1.7.0``), ``defusedxml`` and ``packaging`` Python packages — shipped as
   ``requirements.txt`` inside the module; on Odoo.sh reference it with ``-r`` from your
   repository-root ``requirements.txt``, the only file installed automatically)
2. Navigate to Settings > MCP Server and turn on the master switch
3. Enable the models you want to expose and set their per-operation permissions
4. Connect your MCP client to the ``/mcp`` endpoint (see below)

Configuration
-------------

Model Access
~~~~~~~~~~~~

1. Go to Settings > Technical > MCP > MCP Available Models
2. Add models you want to expose (e.g., res.partner, product.product)
3. Configure permissions for each model:

   - Read access
   - Write access
   - Create access
   - Delete access
   - Allow Method Calls (opt-in for the ``call_model_method`` tool, default off)

Server Settings
~~~~~~~~~~~~~~~

Settings > MCP Server holds the master switch plus: **Allow OAuth 2.1 login** (on by
default; system parameter ``mcp_server.enable_oauth``), rate limiting and its request
limit, audit logging and its retention, the default/maximum record limits for tool
responses, and an optional **Allowed Browser Origins** allowlist (system parameter
``mcp_server.allowed_origins``; empty by default = any Origin accepted, when set a
browser request from another Origin is refused with HTTP 403 — native clients send no
Origin header and are never affected).

Security Groups
~~~~~~~~~~~~~~~

The module creates two security groups:

- **MCP Administrator**: Can configure MCP settings and manage enabled models, custom
  tools, OAuth clients/tokens and the audit log
- **MCP User**: Required to use MCP — only members can authenticate on any MCP
  endpoint (``common.authenticate`` on the XML-RPC proxy included) or complete the
  OAuth consent flow. A valid credential whose user is not a
  member is refused with an audited 403; removing the group cuts off the user's
  outstanding API keys and OAuth tokens immediately. Data access is still bounded by
  the per-model MCP permissions and the user's own Odoo access rights.

MCP Administrator implies MCP User; system administrators are MCP Administrators
automatically. Portal users cannot be members (the group implies Internal User).
On upgrade, the group is granted automatically to active internal users with
existing MCP activity (an ``mcp``-scope API key, an OAuth token, or an audit-log
entry recording a completed MCP operation). The audit-log signal only reaches users
active within ``mcp_server.log_retention_days`` (default 30) -- the daily cleanup cron
deletes older entries -- so users connecting with a global or ``rpc``-scope API key on
a longer cadence must be assigned the group manually.

Upgrading from version 1.2
~~~~~~~~~~~~~~~~~~~~~~~~~~

A database with version 1.2 of this module (the XML-RPC/REST proxy) updates in place.
The enabled models, the settings, the API keys and the audit log stay, and the legacy
``/mcp/xmlrpc/*`` and REST endpoints keep working with the same API keys. Users of the
legacy endpoints without a completed MCP operation in the audit log need an
administrator to add the MCP User group. ``mcp_server.request_timeout`` no longer
applies, and a request limit from 1 to 9 counts as 10.

Multi-database deployment
~~~~~~~~~~~~~~~~~~~~~~~~~~~

On an Odoo instance that serves **more than one database**, every ``/mcp`` route returns
``404 "No database is selected"`` until Odoo can tell which database a request targets —
the Bearer token or OAuth session cannot select one on its own. Give each database its own
hostname and let Odoo map host → database (``proxy_mode = True``, ``dbfilter = ^%d$`` in
``odoo.conf``), or pin a single database with ``db_name``. (The ``X-Odoo-Database``
header is Odoo 19+ core and does not work on Odoo 17.)
Single-database instances need none of this.

Connecting AI Assistants
------------------------

Option 1: Log in with Odoo (OAuth 2.1) — recommended
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The simplest way to connect: give the client your Odoo MCP URL and sign in with your
normal Odoo login — no API key is created, copied or stored. The module is itself a
complete OAuth 2.1 authorization server; any MCP client that supports OAuth for remote
servers works out of the box.

- **Claude.ai (web) / Claude Desktop**: Settings > Connectors > Add custom connector,
  paste ``https://your-company.odoo.com/mcp``, complete the Odoo login and consent.
- **Claude Code (CLI)**:

  .. code-block:: bash

     claude mcp add --transport http much-odoo https://your-company.odoo.com/mcp

  then run ``/mcp`` inside Claude Code and choose *Authenticate* — the browser opens
  your Odoo login.
- **ChatGPT**: enable Developer Mode, then Settings > Apps & Connectors > Advanced
  settings > Create app with the same URL — the Odoo login opens when you connect
  (web, paid plans; see Client compatibility below).
- **Perplexity / Mistral Le Chat**: add the URL as a custom connector in their
  Connectors settings — the OAuth login starts automatically.
- **Cursor / VS Code**: add the server URL without an ``Authorization`` header; recent
  versions detect the OAuth challenge and open the login flow automatically.

Under the hood, an unauthenticated ``POST /mcp`` returns ``401`` with an RFC 9728
``resource_metadata`` pointer; the client registers itself (RFC 7591 dynamic client
registration with PKCE), the user signs in and approves a per-request consent
screen, and the client exchanges the authorization code (PKCE S256) for an opaque access
token bound to that user and to the ``/mcp`` resource (RFC 8707 ``resource`` indicator) — a
token not bound to this resource is refused. Access tokens are short-lived (1 hour) and
renewed by rotating refresh tokens; replaying an already-rotated refresh token is treated
as a compromise and revokes the whole token family, so the client must sign in again.
The server always issues a refresh token, so it ignores a request for the
``offline_access`` scope.

The consent screen includes a **Create and modify data** switch.
Turning it off grants the session the read-only ``mcp:read`` scope: write tools are not
even listed and any write attempt is refused with a clear read-only error. Leaving it
on grants ``mcp:write`` (read + write). The granted scope is computed server-side
and can only ever be narrower than what the client registered for.

Administrators manage registered clients and issued tokens under Settings > Technical >
MCP (OAuth Clients / OAuth Tokens); a token can be revoked from its form or via the bulk
list action, and deactivating a client cuts off every token it issued. A daily scheduled
action garbage-collects spent credentials. To accept API keys only, turn off **Allow
OAuth 2.1 login** in Settings > MCP Server.

Confidential OAuth clients: a program that needs a client secret, for example a
Microsoft 365 Copilot plugin, gets one in two ways. A registration with
``client_secret_basic`` or ``client_secret_post`` receives a secret, and so does a
registration without ``token_endpoint_auth_method`` (the RFC 7591 default). Such a
client can also connect without the secret, so existing public clients do not change.
An MCP Administrator can also create a client with **New Confidential Client** under
Settings > Technical > MCP > OAuth Clients. Odoo shows the secret only once, and
**Regenerate Secret** replaces it. PKCE stays mandatory, a wrong secret is always
refused, and Odoo stores only a hash of the secret. Each registration request writes one
``OAuth DCR request`` line to the Odoo log.

Option 2: API key (Bearer token)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For headless setups, service accounts, CI, or MCP clients without OAuth support:

1. Go to My Profile > Account Security > New API Key
2. Set **Access** to **MCP only** (recommended) or keep *All APIs (default)*. An
   **MCP only** key authenticates only on ``/mcp`` — a leaked key cannot be used
   for general XML-RPC/JSON-RPC access
3. Enter a description and copy the key (shown only once)
4. Use the key as a ``Bearer`` token in your MCP client configuration

**Claude Code (CLI)**

.. code-block:: bash

   claude mcp add --transport http much-odoo \
     https://your-company.odoo.com/mcp \
     --header "Authorization: Bearer YOUR_API_KEY"

**Cursor** (``~/.cursor/mcp.json`` or a project ``.cursor/mcp.json``):

.. code-block:: json

   {
     "mcpServers": {
       "much-odoo": {
         "url": "https://your-company.odoo.com/mcp",
         "headers": {
           "Authorization": "Bearer YOUR_API_KEY"
         }
       }
     }
   }

**VS Code** (``.vscode/mcp.json`` — note the top-level ``servers`` key):

.. code-block:: json

   {
     "servers": {
       "much-odoo": {
         "type": "http",
         "url": "https://your-company.odoo.com/mcp",
         "headers": {
           "Authorization": "Bearer YOUR_API_KEY"
         }
       }
     }
   }

**MCP Inspector** (protocol testing)

.. code-block:: bash

   npx @modelcontextprotocol/inspector

Point it at ``https://your-company.odoo.com/mcp`` and add the header
``Authorization: Bearer YOUR_API_KEY`` (or leave it out and use its OAuth flow).

Option 3: Standalone local client (``uvx mcp-server-odoo``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The companion open-source client `mcp-server-odoo
<https://github.com/ivnvxd/mcp-server-odoo>`_ runs as a small local process on the
client machine and bridges **stdio** MCP to this module's XML-RPC/REST API. Use it when
your MCP client only speaks stdio (no remote HTTP servers) or when you want a local,
pinnable process. Requires Python 3.10+ and `uv <https://docs.astral.sh/uv/>`_.

.. code-block:: json

   {
     "mcpServers": {
       "odoo": {
         "command": "uvx",
         "args": ["mcp-server-odoo"],
         "env": {
           "ODOO_URL": "https://your-company.odoo.com",
           "ODOO_API_KEY": "your-api-key-here",
           "ODOO_DB": "your-database-name"
         }
       }
     }
   }

The bridge goes through the same per-model permissions, audit log and rate limits, but
talks to this module's legacy ``/mcp/xmlrpc/*`` and ``/mcp/*`` REST endpoints rather
than to ``/mcp``. Those delegate authentication to Odoo core (scope ``rpc``), so it
needs a **global (all-APIs) or rpc-scope** API key — an "MCP only" key works on
``/mcp`` only and is rejected here. Besides stdio it can also serve Streamable HTTP
itself (``--transport streamable-http``; note it adds no authentication of its own).
See the `mcp-server-odoo repository <https://github.com/ivnvxd/mcp-server-odoo>`_ for
full instructions (transports, username/password auth, multi-language output).

Client compatibility
~~~~~~~~~~~~~~~~~~~~

Verified against this module's endpoint (Streamable HTTP, MCP protocol ``2025-11-25``
/ ``2025-06-18``, OAuth 2.1 with dynamic client registration + PKCE, or Bearer API
keys):

- **Claude.ai (web) / Claude Desktop** — OAuth via the Connectors UI (all plans; Free:
  one connector). Connectors call your server from Anthropic's cloud (egress range
  ``160.79.104.0/21`` — allowlist it if a WAF/firewall fronts Odoo), so it must be
  reachable on public HTTPS.
- **Claude mobile (iOS/Android)** — add the connector once on claude.ai (web); it syncs
  to the mobile apps automatically.
- **Claude Code (CLI)** — OAuth or API key; runs locally, so private/localhost
  instances also work.
- **ChatGPT** — verified end-to-end against this module: OAuth login gives full
  read + write tool access in normal chat. Set up on ChatGPT web (paid plans): enable
  Developer Mode, then Settings > Apps & Connectors > Advanced settings > Create app
  with the ``/mcp`` URL (on Business/Enterprise/Edu a workspace admin enables the
  toggle). OAuth only — no header auth. Write actions prompt for confirmation by
  default. Feature limits: *company knowledge* only includes apps exposing
  ``search``/``fetch`` tools; *deep research* uses custom apps read-only; *agent mode*
  does not use custom apps.
- **Microsoft Copilot Studio** — OAuth (dynamic discovery / DCR) or API key via the MCP
  onboarding wizard (transport ``mcp-streamable-1.0``).
- **Microsoft 365 Copilot plugins** (declarative agents) — OAuth with a client secret.
  Use dynamic client registration, for example through the Microsoft 365 Agents
  Toolkit. Or create a client with **New Confidential Client** and enter it in the Teams
  Developer Portal. Use the redirect URI
  ``https://teams.microsoft.com/api/platform/v1.0/oAuthRedirect`` and turn PKCE on.
  After a provisioning through registration, sign in once, so the client receives its
  first token and is kept.
- **Perplexity (web & desktop)** — custom remote connectors (paid tiers) with OAuth or
  API key.
- **Mistral Le Chat** — Connectors > Custom MCP Connector; auth auto-detected (OAuth
  2.1 with DCR, or Bearer token).
- **Gemini CLI** — ``httpUrl`` server with automatic OAuth discovery, or an
  ``Authorization`` header. **Gemini Enterprise** (Google Cloud) — custom MCP data
  store with OAuth. The **consumer Gemini app** has no custom MCP connectors (as of
  July 2026) — use Gemini CLI or Gemini Enterprise instead.
- **Cursor / VS Code / Windsurf and other MCP IDEs** — API-key header or OAuth.
- **Stdio-only clients** (LM Studio, many desktop wrappers) — use the standalone
  ``uvx mcp-server-odoo`` bridge (Option 3).

Rule of thumb: cloud-hosted clients (Claude.ai, ChatGPT, Le Chat, Perplexity web,
Copilot, Gemini Enterprise) need your Odoo on public HTTPS and usually offer only
OAuth for custom connectors; locally-running clients (Claude Code, IDEs, Gemini CLI,
the uvx bridge) can reach private instances and use either auth.

Features
--------

Built-in Tools
~~~~~~~~~~~~~~

The ``/mcp`` endpoint exposes 17 built-in tools — nine read tools
(``search_records``, ``get_record``, ``get_fields``, ``list_models``,
``aggregate_records``, ``read_attachment``, ``list_record_attachments``,
``list_resource_templates``, ``get_current_context``) and eight
write tools (``create_record``, ``create_records``, ``update_record``,
``update_records``, ``delete_record``, ``upload_attachment``, ``post_message``,
``call_model_method`` — the latter an admin opt-in per model). ``create_records`` and ``update_records`` write many
records of one model in a single atomic call, with shared or per-record values.
``call_model_method`` is deliberately narrow: it runs only public *business* methods on
models whose *Allow Method Calls* flag is set, and refuses private methods
(``_``-prefixed or marked ``@api.private`` in Odoo) as well as data-access methods (``read``, ``search``, ``name_search``) — for those,
use the dedicated CRUD tools.
Binary data is served on demand via ``odoo://`` resources
(``odoo://record/{model}/{id}/{field}``, ``odoo://attachment/{id}``) instead of
inlined base64. ``read_attachment`` dereferences such a URI from any client: text
inline, images as an image block, PDF/Office documents as extracted text, or a
time-limited download link; ``list_record_attachments`` lists a record's files and
``upload_attachment`` adds one.

Every ``tools/call`` is checked against the tool's advertised input schema before the
tool runs (required and unknown arguments, ``type`` including unions, ``enum``,
``minItems`` and simple ``items.type``); a violation is a tool error naming the
argument, for example ``Invalid argument 'format': must be one of auto, link, blob.``
An explicit ``null`` for an optional argument counts as omitted, and the shapes the
built-in tools tolerate (a JSON-string ``fields`` / ``domain``, a bare-string
``groupby`` / ``aggregates``, digit-string ids) stay accepted. A message without an
``id`` is a notification: it gets an empty HTTP 202 and a request method sent without
one is not executed.

User Context on Connect
~~~~~~~~~~~~~~~~~~~~~~~

The ``initialize`` handshake returns a personalized ``instructions`` string — the
connected user, their timezone, active and allowed companies, and the rule that all
datetimes are UTC — which spec-compliant clients inject into the model's context
automatically, so assistants stop guessing timezones or companies. The read-only
``get_current_context`` tool returns the same information on demand.

Custom Tools
~~~~~~~~~~~~

Administrators can expose curated verbs (e.g. ``confirm_sale_order``) instead of
generic CRUD by wrapping a **Python Code** server action in a custom tool
(Settings > Technical > MCP > Custom Tools): name, LLM-facing description, JSON input
schema and a read-only flag. Arguments and results flow through a shared ``mcp`` dict
in the action's code (``mcp['args']`` in, ``mcp['result'] = ...`` out). The action runs
as the calling user (never elevated); who may call the tool is controlled by the
action's Allowed Groups (fallback: write access to the action's model). Tools a user
may not run are hidden from ``tools/list`` and refused when called; errors are rolled
back and sanitized; every call is audited. The wrapped model need not appear in
*Available Models*, and a model's per-operation *Allow Read/Create/Write/Delete* flags do
**not** gate custom tools -- the tool-level access rule (plus the caller's ACLs) is the
control, so a custom tool can perform an operation disabled there (e.g. create a contact
while ``res.partner`` *Allow Create* is off). Scope each action narrowly. The client's
arguments are checked against the tool's input schema (``required``, ``type``,
``enum``, ``minItems``, simple ``items.type``) before the action runs; other JSON Schema
keywords are not enforced.

Usage Examples
--------------

Once configured, you can query and manage your Odoo data using natural language:

**Data Retrieval:**

- "Show me all customers from Spain"
- "Find products with stock below 10 units"
- "List today's sales orders over $1000"
- "Search for unpaid invoices from last month"

**Data Management:**

- "Create a new customer contact for Acme Corporation"
- "Add a new product called 'Premium Widget' with price $99.99"
- "Update the phone number for customer John Doe"
- "Change the status of order SO/2024/001 to confirmed"
- "Delete the test contact we created earlier"

API Endpoints
-------------

Native MCP
~~~~~~~~~~

- ``/mcp`` (POST) - native MCP server; auth via ``Authorization: Bearer <token>``;
  answers HTTP 503 while MCP is disabled
- ``/mcp/rpc`` (POST) - legacy alias of ``/mcp`` (same endpoint, same auth)
  (an mcp- or rpc-scope API key, or an OAuth 2.1 access token)

OAuth 2.1
~~~~~~~~~

- ``/.well-known/oauth-protected-resource`` (GET) - RFC 9728 protected-resource metadata
- ``/.well-known/oauth-authorization-server`` (GET) - RFC 8414 authorization-server metadata
- ``/mcp/oauth/authorize`` (GET/POST) - Odoo-login + consent screen
- ``/mcp/oauth/token`` (POST) - token endpoint (PKCE S256)
- ``/mcp/oauth/register`` (POST) - dynamic client registration (IP rate-limited;
  scope tokens other than ``mcp``, ``mcp:read`` or ``mcp:write`` are dropped; a
  client left with none is stored read-only, unless it asked for no scope or only for
  ``offline_access``)

REST API
~~~~~~~~

- ``/mcp/health`` - Health check (no auth)
- ``/mcp/system/info`` - System information
- ``/mcp/auth/validate`` - API key validation
- ``/mcp/models`` - List enabled models
- ``/mcp/models/{model}/access`` - Check model permissions

XML-RPC API
~~~~~~~~~~~

Used by the standalone ``mcp-server-odoo`` client:

- ``/mcp/xmlrpc/common`` - Authentication
- ``/mcp/xmlrpc/db`` - Database operations
- ``/mcp/xmlrpc/object`` - Model operations with MCP access control

Security Considerations
-----------------------

- Prefer OAuth for interactive users: tokens are short-lived, revocable per client, and
  never need to be copied around; grant read-only consent where writes are not needed
- Prefer **MCP only** API keys so a leaked key cannot reach general RPC
- Use HTTPS in production environments
- Configure model access carefully - only enable necessary models; leave *Allow Method
  Calls* off unless needed
- Custom tools bypass a model's *Allow Read/Create/Write/Delete* flags (they are bounded
  by the wrapped action's logic, the caller's ACLs, and the read-only/OAuth-scope
  boundary) - scope each custom tool narrowly and set its action's Allowed Groups
  explicitly
- Regularly review audit logs (Settings > Technical > MCP > MCP Logs)
- Keep the module updated

Troubleshooting
---------------

Common Issues
~~~~~~~~~~~~~

**Module Not Installing**

- Check that all dependencies are satisfied (``authlib`` (``>=1.6.12,<1.7.0``),
  ``defusedxml``, ``packaging``). ``authlib`` is capped below 1.7.0 so it does not pull in a
  newer ``cryptography`` than the Odoo image ships. If the server still fails to start with
  ``module 'lib' has no attribute 'GEN_EMAIL'``, a newer ``cryptography`` was installed anyway
  (e.g. by another add-on) and the system ``pyOpenSSL`` cannot import against it — keep
  ``cryptography`` at the image's version, or upgrade ``pyOpenSSL`` to match
- Ensure Odoo 17.0 is being used

**OAuth Login Fails or Keeps Re-Prompting**

- Confirm MCP is enabled (Settings > MCP Server) and **Allow OAuth 2.1 login** is on
- Check the token was not revoked / the client not deactivated (Settings > Technical >
  MCP)
- Behind a reverse proxy, make sure the discovery documents point at the right origin
  (``web.base.url``)
- A client forced to re-authenticate for no obvious reason may have replayed a stale
  refresh token — reuse detection revokes the whole token family by design; signing in
  again issues a fresh one

**OAuth Token Accepted but Every Call Returns 401**

- The access token is not bound to this resource server. Compliant MCP clients send the
  RFC 8707 ``resource`` indicator (your ``/mcp`` URL) on the authorize and token requests;
  a token minted without it is refused on ``/mcp``. Reconnect with a client that sends
  ``resource``, or include it in manual token requests

**API Key Not Working**

- Verify the key is active in user settings
- A **403** means the credential is valid but the user is not in the **MCP User**
  (or MCP Administrator) group — membership is required on every MCP surface; the
  refusal is recorded in the audit log with the user
- Check user has appropriate MCP permissions
- An **MCP only** key works only on ``/mcp`` (rejected on REST/XML-RPC by design)

**Model Access Denied**

- Confirm model is in enabled models list
- Check operation permissions for the model
- Verify user's security group membership
- On an OAuth session, a read-only consent (``mcp:read``) hides and refuses write tools

**Connection Refused or 401 Unauthorized**

- Verify your Odoo URL is reachable from the client and ends in ``/mcp``
- Check the ``Authorization: Bearer <API_KEY>`` header is set and the key is active
- Confirm MCP is enabled (Settings > MCP Server) and the model is exposed

**Requests Return 429 (Too Many Requests)**

- The per-minute rate limit was exceeded; the response carries a ``Retry-After: 60``
  header (on ``/mcp``, the REST endpoints and the XML-RPC proxy's ``429`` fault). Wait
  for the window to reset, then retry — or raise **Request Limit per Minute** (or turn
  rate limiting off) in Settings > MCP Server

Support
-------

For support, reach out to product@erp.muchconsulting.de

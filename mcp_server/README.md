# MCP Server for Odoo

An Odoo module that enables Model Context Protocol (MCP) integration, allowing AI
assistants to securely access and interact with your Odoo data. Supports full CRUD
operations - create, read, update, and delete records through natural language.

The module **speaks MCP natively**: it exposes a built-in MCP endpoint at `/mcp`, so any
MCP client — Claude (web, desktop, mobile, Claude Code), ChatGPT, Microsoft Copilot,
Perplexity, Mistral Le Chat, Gemini CLI, Cursor, VS Code, MCP Inspector — can connect
**directly to your Odoo URL** with no separate process to install (see the
[client compatibility table](#client-compatibility)). There are three ways to connect,
from simplest to most involved:

1. **[Log in with Odoo (OAuth 2.1)](#option-1-log-in-with-odoo-oauth-21--recommended)**
   — paste your Odoo URL into the client and sign in. No keys to create or copy.
2. **[API key (Bearer token)](#option-2-api-key-bearer-token)** — mint a key once and
   configure it as an `Authorization: Bearer` header.
3. **[Standalone local client (`uvx mcp-server-odoo`)](#option-3-standalone-local-client-uvx-mcp-server-odoo)**
   — a bridge process for stdio-only MCP clients, using the same module APIs.

## Features

- 🔌 **Native MCP Endpoint**: Built-in `/mcp` endpoint (Streamable HTTP, JSON-RPC 2.0) —
  connect MCP clients straight to Odoo, no extra software
- 🔑 **Log in with Odoo (OAuth 2.1)**: browser-based clients connect with a normal Odoo
  login and a per-request consent screen — no API key handling
- 🔐 **Secure API Access**: API key **or** OAuth 2.1 authentication with rate limiting
- 📖 **Read-Only Consent**: grant an AI assistant read-only access with one switch at
  the OAuth consent screen (`mcp:read` / `mcp:write` scopes)
- 🗝️ **"MCP Only" API Keys**: mint keys that work only on the MCP endpoint — a leaked
  key cannot be used for general RPC
- 🧭 **User Context on Connect**: the handshake tells the assistant who is connected,
  their timezone and companies, so it stops guessing
- 🧩 **Custom Tools**: expose curated verbs (e.g. `confirm_sale_order`) backed by server
  actions instead of generic CRUD
- 📎 **Attachments**: read a PDF, image or file attached to a record from any client,
  list a record's attachments, upload new ones
- 🎯 **Granular Permissions**: Control read/write/create/delete access per model
- 🌐 **REST & XML-RPC APIs**: Dual protocol support for maximum compatibility
- 👥 **Role-Based Access**: MCP access is granted per user through the MCP User and
  MCP Administrator security groups
- 📊 **Audit Log**: every call, denial and auth attempt is recorded in `mcp.log`
- 🖥️ **User-Friendly UI**: Integrated configuration in Odoo settings

## What Can You Do With This Module?

This module opens up powerful AI-assisted workflows for your Odoo instance:

**Data Retrieval & Analysis:**

- **Customer Service Automation**: Let AI assistants look up customer orders, check
  inventory, and provide instant support
- **Sales Intelligence**: Query your CRM data naturally - "Show me all leads from Spain
  that haven't been contacted in 30 days"
- **Inventory Management**: Ask questions like "Which products are low in stock?" or
  "What were our top selling items last month?"
- **Financial Insights**: Get quick answers about invoices, payments, and financial
  status without complex reports
- **HR Queries**: Find employee information, leave balances, or department structures
  through natural language
- **Project Management**: Track project progress, find overdue tasks, or check team
  workloads conversationally

**Data Management & Automation:**

- **Contact Management**: Create new customers, update contact information, or manage
  supplier records
- **Product Catalog**: Add new products, update prices, or modify inventory levels
- **Order Processing**: Create sales orders, update order status, or manage deliveries
- **Task Creation**: Add new tasks to projects, assign team members, or update progress
- **Event Scheduling**: Create calendar events, schedule meetings, or manage
  appointments
- **Data Cleanup**: Remove test records, archive old data, or maintain data quality

## Requirements

**Odoo 17.0** (Community or Enterprise). This module targets Odoo 17.0.

You always need **this Odoo module** installed and configured (it provides the MCP
protocol, access control, and security layer). Whichever way a client connects — OAuth,
API key, or the standalone bridge — every call runs as a real Odoo user and shares the
same `mcp.enabled.model` permissions, audit log, and rate limiting.

The module needs the `authlib` (`>=1.6.12,<1.7.0`), `defusedxml` and `packaging` Python
packages — declared in the manifest's `external_dependencies` and mirrored in the
`requirements.txt` shipped inside the module. `authlib` is intentionally
capped below 1.7.0: 1.7.x pulls in a newer `cryptography` (via `joserfc`) that many Odoo
deploy images cannot install over their system `cryptography`/`pyOpenSSL`, which breaks
installation and the server's `import OpenSSL`. `packaging` lets Odoo parse the version pin.

Install them into the Python environment that runs Odoo:

```bash
pip install -r mcp_server/requirements.txt
# or explicitly:
pip install "authlib>=1.6.12,<1.7.0" defusedxml packaging
```

**On Odoo.sh** (and similar platforms that auto-install Python dependencies): only the
`requirements.txt` at the **root of your repository** (or of a submodule) is installed —
a `requirements.txt` inside an addon folder is ignored. Reference the module's file from
your repo-root `requirements.txt`:

```
-r path/to/mcp_server/requirements.txt
```

or copy its lines there verbatim.

**Before connecting anything**: go to **Settings > MCP Server**, turn on the master
switch, and enable the models you want to expose with their per-operation permissions.
See [Configure the Module](#step-2-configure-the-module).

## Connecting AI Assistants

### Option 1: Log in with Odoo (OAuth 2.1) — recommended

The simplest way to connect: give the client your Odoo MCP URL and **sign in with your
normal Odoo login**. No API key is created, copied, or stored in a config file. The
module is itself a complete OAuth 2.1 authorization server, so any MCP client that
supports OAuth for remote servers works out of the box:

- **Claude.ai (web)**: **Settings > Connectors > Add custom connector**, paste
  `https://your-company.odoo.com/mcp`, then complete the Odoo login and consent screen.
- **Claude Desktop**: same **Connectors** UI (Settings > Connectors > Add custom
  connector).
- **Claude Code (CLI)**:

  ```bash
  claude mcp add --transport http much-odoo https://your-company.odoo.com/mcp
  ```

  then run `/mcp` inside Claude Code and choose **Authenticate** — the browser opens
  your Odoo login.

- **ChatGPT**: enable **Developer Mode**, then **Settings > Apps & Connectors > Advanced
  settings > Create app** with the same URL — the Odoo login opens when you connect
  (web, paid plans; details in the [compatibility table](#client-compatibility)).
- **Perplexity / Mistral Le Chat**: add the URL as a custom connector in their
  Connectors settings — the OAuth login starts automatically.
- **Cursor / VS Code**: add the server URL **without** an `Authorization` header (see
  the JSON snippets [below](#option-2-api-key-bearer-token), minus the `headers` key);
  recent versions detect the OAuth challenge and open the login flow automatically.

What happens under the hood (all automatic):

1. The client `POST`s to `/mcp` with no token and gets a `401` whose
   `WWW-Authenticate: Bearer` header carries an RFC 9728 `resource_metadata` pointer to
   `/.well-known/oauth-protected-resource`.
2. From the discovery documents the client locates the authorization server and
   **registers itself** at `/mcp/oauth/register` (RFC 7591 dynamic client registration
   with PKCE). Most clients register as public clients without a secret. For clients
   that need a secret, see [Confidential OAuth clients](#confidential-oauth-clients). A
   client that asks only for scopes other than `mcp`, `mcp:read` and `mcp:write`, for
   example `openid profile`, is stored read-only (`mcp:read`). A client that asks for no
   scope, or only for `offline_access`, is stored without a scope, and the consent
   screen decides its write access.
3. The user is sent to `/mcp/oauth/authorize`, signs in with their **normal Odoo
   login**, and approves a **per-request consent screen**. The consent screen includes a
   **Create and modify data** switch — turn it off to grant the assistant **read-only**
   access (see [OAuth Read-Only Consent](#oauth-read-only-consent)).
4. The client exchanges the authorization code at `/mcp/oauth/token` (PKCE S256) for an
   opaque **access token bound to that user**. Access tokens are short-lived (1 hour);
   long-lived sessions are kept alive by **rotating refresh tokens**. The server always
   issues a refresh token, so it ignores a request for the `offline_access` scope.

Every call runs as the logged-in user, so the same `mcp.enabled.model` permissions, Odoo
access rights, audit log and rate limiting apply as with an API key.

**Managing OAuth clients & tokens (admins).** OAuth activity is managed under
**Settings > Technical > MCP** (MCP Administrator group):

- **OAuth Clients** — applications registered through the authorization flow, and
  confidential clients that an administrator created with **New Confidential
  Client**. Bulk **Deactivate** is available from the list view; deactivating a client
  blocks new logins **and** cuts off every token it already issued.
- **OAuth Tokens** — issued tokens, listing the user, client, audience, scope and
  expiry. Open a token and click **Revoke** (or use the bulk list action) to invalidate
  it immediately; the client must then re-authenticate.

A daily scheduled action (`ir.cron`) garbage-collects spent credentials — expired
authorization codes, revoked-and-expired tokens, and stale dynamically-registered
clients — so the tables stay clean. Only credentials that can no longer authenticate
anything are removed. A self-registered client with a secret stays after it received
its first token (see [Confidential OAuth clients](#confidential-oauth-clients)).

The OAuth front door is **enabled by default** whenever MCP is enabled. To accept API
keys only, turn off **Allow OAuth 2.1 login** in **Settings > MCP Server** (system
parameter `mcp_server.enable_oauth`).

#### Confidential OAuth clients

Most MCP clients are public clients. They register without a secret and protect each
token request with PKCE. Some programs need a client secret, for example Microsoft 365
Copilot plugins, which keep their credentials in the Microsoft token store. The
authorization server supports three types of client:

| Client type                   | How the client is created                                                                       | Token endpoint authentication                                |
| ----------------------------- | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| Public                        | Registration with `token_endpoint_auth_method: none`                                            | PKCE, no secret                                              |
| Confidential                  | Registration with `client_secret_basic` or `client_secret_post`, or **New Confidential Client** | PKCE and the client secret (HTTP Basic header or form field) |
| Confidential, secret optional | Registration without `token_endpoint_auth_method`                                               | PKCE, with or without the client secret                      |

- RFC 7591 defines `client_secret_basic` as the default for a registration without a
  method. The server answers with that method and a secret. A client that ignores the
  secret keeps working, so existing public clients do not change.
- A client with a secret that sends a wrong secret is refused with
  `401 invalid_client`. PKCE (S256) stays mandatory for every client.
- The secret does not expire (`client_secret_expires_at: 0`). Odoo stores only its
  SHA-256 hash.
- A self-registered client with a secret stays after its first token. A long time
  without use does not remove it. If it receives no token within 30 days after the
  registration, the daily clean-up deletes it.
- Each registration request writes one `INFO` line to the Odoo log:
  `OAuth DCR request: client_name=... auth_method=... redirect_hosts=...`. The line
  shows how a client registers and holds no secret.

To create a confidential client yourself (MCP Administrator group):

1. Open **Settings > Technical > MCP > OAuth Clients** and click **New Confidential
   Client**.
2. Enter a name, the redirect URIs of the program (one per line), and the scope: `mcp`,
   `mcp:read` for read-only access, or `mcp:write`.
3. Click **Create** and enter your password again.
4. Copy the client ID and the client secret into the program. Odoo shows the secret
   only once.

If the secret is lost, open the client and click **Regenerate Secret**, then enter the
new secret in the program. The old secret stops working at once: the program cannot
get new tokens until it has the new secret. Access tokens that the client already holds
stay valid until they expire, at most 1 hour. To stop them at once, revoke them under
**OAuth Tokens**. Regenerate Secret is available only for
clients that an administrator created. A self-registered client belongs to the program
that registered it.

### Option 2: API key (Bearer token)

For clients where you prefer (or need) to paste a static credential — headless setups,
service accounts, CI, or MCP clients without OAuth support:

1. In Odoo, go to **My Profile > Account Security > New API Key**.
2. Set **Access** to **MCP only** (recommended — see
   ["MCP Only" API Keys](#mcp-only-api-keys)) or keep _All APIs (default)_.
3. Enter a description and copy the key — it is shown only once.
4. Configure the client with the URL and an `Authorization: Bearer <API_KEY>` header
   (the `Bearer ` prefix is optional — a bare `Authorization: <API_KEY>` also works). A
   missing or invalid key returns `401` with a `WWW-Authenticate: Bearer` challenge;
   `GET /mcp` returns `405` (the endpoint is POST-only).

**Claude Code (CLI):**

```bash
claude mcp add --transport http much-odoo \
  https://your-company.odoo.com/mcp \
  --header "Authorization: Bearer YOUR_API_KEY"
```

**Cursor** (`~/.cursor/mcp.json` or a project `.cursor/mcp.json`, `mcpServers` key):

```json
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
```

**VS Code** (`.vscode/mcp.json` — note the top-level `servers` key, not `mcpServers`):

```json
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
```

**Claude Desktop**: remote (HTTP) servers are added through the **Connectors** UI
(Settings → Connectors → Add custom connector) pointed at
`https://your-company.odoo.com/mcp` — which uses
[OAuth](#option-1-log-in-with-odoo-oauth-21--recommended) and needs no key. To use an
API key instead, bridge a local stdio entry to the remote endpoint with
[`mcp-remote`](https://www.npmjs.com/package/mcp-remote).

**MCP Inspector** (protocol testing):

```bash
npx @modelcontextprotocol/inspector
```

Point it at `https://your-company.odoo.com/mcp` and add the header
`Authorization: Bearer YOUR_API_KEY` (or leave the header out and use its OAuth flow).

### Option 3: Standalone local client (`uvx mcp-server-odoo`)

The companion open-source client
[**mcp-server-odoo**](https://github.com/ivnvxd/mcp-server-odoo) runs as a small local
process on the machine where your MCP client lives and bridges **stdio** MCP to this
module's XML-RPC/REST API. Use it when your MCP client only speaks stdio (no remote HTTP
servers), or when you want a local process you can pin and configure per project.

```json
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
```

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/) on the client machine
(`ODOO_DB` is optional when the server exposes its database list). The bridge goes
through the same `mcp.enabled.model` permissions, audit log and rate limits, but it
talks to this module's legacy `/mcp/xmlrpc/*` and `/mcp/*` REST endpoints rather than to
`/mcp`. Those delegate authentication to Odoo core (scope `rpc`), so this client needs a
**global (all-APIs) or `rpc`-scope** key — an [MCP only](#mcp-only-api-keys) key works
on `/mcp` **only** and is rejected here.

Besides stdio, the bridge can also serve **Streamable HTTP** itself
(`--transport streamable-http`) if you want to host it on a trusted network — note it
adds no authentication of its own. See the
[mcp-server-odoo repository](https://github.com/ivnvxd/mcp-server-odoo) for full
instructions: transports, username/password auth, multi-language output, and a
module-less "YOLO" test mode (not recommended for production — it bypasses this module's
per-model access control).

### Client Compatibility

Verified against this module's endpoint (Streamable HTTP, MCP protocol `2025-11-25` /
`2025-06-18`, OAuth 2.1 with dynamic client registration + PKCE, or Bearer API keys):

| Client                                                                                                                                                 | Connect with                                                                               | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Claude.ai (web)** / **Claude Desktop** ([docs](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)) | OAuth (Connectors UI)                                                                      | Custom connectors on all plans (Free: one connector). Connectors call your server **from Anthropic's cloud** (egress range `160.79.104.0/21` — allowlist it if a WAF/firewall fronts Odoo), so it must be reachable on public HTTPS. Streamable HTTP + protocol `2025-06-18` explicitly supported; org-level static-header auth exists in beta, OAuth is the normal path.                                                                                                                                                                                                                                          |
| **Claude mobile (iOS/Android)**                                                                                                                        | OAuth (synced)                                                                             | Add the connector on claude.ai (web) once — it syncs to the mobile apps automatically.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| **Claude Code (CLI)**                                                                                                                                  | OAuth **or** API key                                                                       | Runs locally, so it can also reach private/localhost instances.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| **ChatGPT** ([docs](https://developers.openai.com/api/docs/guides/developer-mode))                                                                     | OAuth (custom app / connector)                                                             | **Verified end-to-end against this module**: OAuth login gives full read + write tool access in normal chat. Set up on ChatGPT web (paid plans): enable **Developer Mode**, then **Settings > Apps & Connectors > Advanced settings > Create app** with the `/mcp` URL (on Business/Enterprise/Edu a workspace admin enables the toggle). OAuth only — no header auth; DCR supported. Write actions prompt for confirmation by default. Feature limits: _company knowledge_ only includes apps exposing `search`/`fetch` tools; _deep research_ uses custom apps read-only; _agent mode_ does not use custom apps. |
| **Microsoft Copilot Studio** ([docs](https://learn.microsoft.com/en-us/microsoft-copilot-studio/mcp-add-existing-server-to-agent))                     | OAuth (dynamic discovery/DCR) **or** API key                                               | Add via the MCP onboarding wizard; transport `mcp-streamable-1.0`. Power Platform data policies apply.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| **Microsoft 365 Copilot plugins** ([docs](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/plugin-authentication-oauth))          | OAuth with a client secret (DCR **or** **New Confidential Client**)                        | Declarative agents keep a client secret in the Microsoft token store. See [Microsoft 365 Copilot plugins](#microsoft-365-copilot-plugins).                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| **Perplexity (web & desktop)** ([docs](https://www.perplexity.ai/help-center/en/articles/13915507-adding-custom-remote-connectors))                    | OAuth **or** API key                                                                       | Custom remote connectors are a paid-tier feature; Streamable HTTP supported.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| **Mistral Le Chat** ([docs](https://docs.mistral.ai/le-chat/knowledge-integrations/connectors/mcp-connectors))                                         | OAuth (auto-detected, DCR) **or** Bearer token                                             | Add under Connectors > Custom MCP Connector; the auth method is detected automatically.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| **Gemini CLI** ([docs](https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md))                                                | OAuth (auto-discovery) **or** Bearer header                                                | Use `httpUrl` in `mcpServers` (or `gemini mcp add`); OAuth discovery is automatic, or set an `Authorization` header.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| **Gemini Enterprise** ([docs](https://docs.cloud.google.com/gemini/enterprise/docs/connectors/custom-mcp-server/set-up-custom-mcp-server))             | OAuth                                                                                      | Google Cloud offering: add the endpoint as a custom MCP data store; users authorize via the OAuth flow.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| **Gemini app (web/mobile)**                                                                                                                            | —                                                                                          | **Not supported**: the consumer Gemini app has no custom MCP connectors (as of July 2026). Use Gemini CLI or Gemini Enterprise instead.                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| **Cursor / VS Code / Windsurf & other MCP IDEs**                                                                                                       | API key header **or** OAuth                                                                | Config snippets [above](#option-2-api-key-bearer-token); header-less entries trigger the OAuth flow in recent versions.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| **MCP Inspector**                                                                                                                                      | OAuth **or** API key                                                                       | Protocol testing.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| **Stdio-only clients** (LM Studio, many desktop wrappers)                                                                                              | API key via the [standalone bridge](#option-3-standalone-local-client-uvx-mcp-server-odoo) | Run `uvx mcp-server-odoo` locally as the stdio endpoint.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |

Two practical rules cover most of the table:

- **Cloud-hosted clients** (Claude.ai/mobile connectors, ChatGPT, Perplexity web, Le
  Chat, Copilot, Gemini Enterprise) connect **from the vendor's infrastructure**: your
  Odoo instance must be reachable on public HTTPS, and OAuth is usually the only
  authentication offered for custom connectors — which is exactly what
  [Option 1](#option-1-log-in-with-odoo-oauth-21--recommended) provides.
- **Locally-running clients** (Claude Code, Cursor, VS Code, Gemini CLI, Inspector, the
  uvx bridge) can reach private or localhost instances and may use either OAuth or an
  API-key header.

### Microsoft 365 Copilot plugins

Microsoft 365 Copilot plugins (declarative agents) need an OAuth client with a client
secret. Copilot Studio does not: it registers a public client, as described in
[Option 1](#option-1-log-in-with-odoo-oauth-21--recommended). You can set up a plugin in
two ways.

With dynamic client registration, for example through the Microsoft 365 Agents Toolkit:
give the toolkit the `https://your-company.odoo.com/mcp` URL. Microsoft registers a
client at `/mcp/oauth/register` and receives a client secret. After the provisioning,
sign in once through the agent, so that the client receives its first token. A
self-registered client that receives no token within 30 days is deleted.

With a static registration in the Teams Developer Portal:

1. In Odoo, create a client with **New Confidential Client** (see
   [Confidential OAuth clients](#confidential-oauth-clients)). Use the redirect URI
   `https://teams.microsoft.com/api/platform/v1.0/oAuthRedirect`.
2. In the Teams Developer Portal, open **Tools > OAuth client registration** and
   register a client with these values:

   | Field                  | Value                                               |
   | ---------------------- | --------------------------------------------------- |
   | Client ID              | The client ID from Odoo                             |
   | Client secret          | The client secret from Odoo                         |
   | Authorization endpoint | `https://your-company.odoo.com/mcp/oauth/authorize` |
   | Token endpoint         | `https://your-company.odoo.com/mcp/oauth/token`     |
   | Refresh endpoint       | `https://your-company.odoo.com/mcp/oauth/token`     |
   | Scope                  | `mcp`, or `mcp:read` for read-only access           |
   | PKCE                   | On. Odoo requires PKCE with S256.                   |

3. Set **Restrict usage by app** as the
   [Microsoft documentation](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/plugin-authentication-oauth)
   describes for plugins, then use the registration ID in the plugin manifest.

If the connection fails, look for the `OAuth DCR request` line in the Odoo log. It
shows how the client registered.

## Native MCP Endpoint (`/mcp`)

The module exposes a native MCP server at `POST /mcp` (Streamable HTTP, JSON-RPC 2.0).
The server implements MCP protocol revisions `2025-11-25` (preferred) and `2025-06-18`;
the revision is negotiated during the `initialize` handshake. `/mcp/rpc` is kept as a
legacy alias of the same endpoint, so clients configured against the old path keep
working.

A message without an `id` is a JSON-RPC notification: it is answered with an empty HTTP
`202`, and a request method such as `tools/call` sent without an `id` is not executed. A
request `id` must be a string or an integer; `null` or any other value is refused with
`-32600`. While the master switch (**Enable MCP Server**) is off, `/mcp` answers HTTP
`503` with a JSON-RPC error body, like the REST endpoints.

### Available Tools

The endpoint exposes 17 built-in tools (plus any admin-defined
[Custom Tools](#custom-tools)):

| Tool                      | Operation   | Description                                              |
| ------------------------- | ----------- | -------------------------------------------------------- |
| `search_records`          | read        | Search records with smart field selection and pagination |
| `get_record`              | read        | Fetch one record by ID, formatted for LLMs               |
| `get_fields`              | read        | Describe a model's fields (type, label, relation, etc.)  |
| `list_models`             | read        | List the MCP-enabled models                              |
| `aggregate_records`       | read        | Group/aggregate records (`read_group`-style)             |
| `read_attachment`         | read        | Read an attachment or binary field (text, image, link)   |
| `list_record_attachments` | read        | List the files attached to a record                      |
| `list_resource_templates` | read        | Advertise the available `odoo://` resource templates     |
| `get_current_context`     | read        | Return the caller's user / timezone / company context    |
| `create_record`           | create      | Create a record                                          |
| `create_records`          | create      | Create several records of one model in one call          |
| `update_record`           | write       | Update a record                                          |
| `update_records`          | write       | Update several records of one model in one call          |
| `delete_record`           | unlink      | Delete a record                                          |
| `upload_attachment`       | create      | Upload a file as an attachment, optionally to a record   |
| `call_model_method`       | method call | Call a public business method (opt-in per model)         |
| `post_message`            | write       | Post a message to a record's chatter                     |

`search_records` is also the batch read path. One call with a domain such as
`[["id", "in", [1, 2, 3]]]` fetches many records at once. A larger `limit` (up to
the configured maximum) reads a big result set in few calls. The tool descriptions
and the `initialize` instructions steer AI clients toward this pattern instead of
one `get_record` call per record.

`get_fields` returns a curated subset of the schema by default: the most relevant
fields (at most 60) with selection lists cut at 20 options. The result names how many
fields it left out. Pass `field_names` for specific fields, or `["__all__"]` for the
complete schema with full selection lists.

`create_records` and `update_records` are the batch write path. `create_records`
takes one model and a list of field->value objects. `update_records` takes one model and
either `record_ids` plus one `values` object that is written to every listed record, or
`updates`, a list of `{"id": …, "values": {…}}` entries when each record needs its own
values (each id once). Both tools run as the calling user. Both are atomic: if one entry
fails, the whole batch is rolled back and nothing is written. To create a parent with its
children in one atomic call, use nested one2many commands in the values, for example
`"order_line": [[0, 0, {...}]]`. The line model needs its own MCP entry with the
operation of the command (see [Access Control](#access-control)). A many2one value is a
record id. The write tools also accept the `[id, "name"]` pair that the read tools
return, and `{"id": id}`. An empty string or an empty list clears the field, as in Odoo.
Odoo does not always name the failing entry, so the tool descriptions tell clients to
retry an ambiguous failure in smaller batches. A batch is capped at **Maximum Batch
Size** entries (`mcp_server.max_batch_size`, default 100). A larger batch is refused
with an error that asks the client to split it. The `record_ids` list of
`call_model_method` keeps its own cap, **Maximum Record Limit**
(`mcp_server.max_limit`). One batch call counts as **one** request for rate limiting.
Deletion has no batch tool on purpose: `delete_record` stays one record per call.

#### Argument validation

Every `tools/call` is checked against the tool's advertised `inputSchema` before the
tool runs: required arguments, unknown arguments (when the schema sets
`additionalProperties: false`), the `type` of each top-level argument (unions such as
`["string", "null"]` included; `1.0` counts as an integer, `true` does not), `enum`,
`minItems` and, for lists of plain values, `items.type`. A violation returns a tool
error that names the argument, for example
`Invalid argument 'format': must be one of auto, link, blob.`, and is recorded in the
audit log. An explicit `null` for an optional argument counts as omitted. The shapes the
built-in tools tolerate stay accepted: `fields`, `domain`, `groupby` and `aggregates`
also take a string (JSON for a list, or one bare expression for `groupby` /
`aggregates`). Record ids, `limit` / `offset`, the batch lists (`records`, `record_ids`,
`updates`) and `values` are checked by the tool itself, so ids also take a digit string
and the batch lists a JSON string. `update_record` and `update_records` refuse a field
name the model does not have with `Invalid field '<name>' on model '<model>'`, and
`aggregate_records` refuses a function that does not fit the field type, such as `sum`
over a text field. A date or datetime group, such as `create_date:month`, is keyed by
the start of the period in the user's time zone, for example `2026-10-01 00:00:00`.

### Attachments and Binary Fields

`get_record` and `search_records` never inline binary data. A populated binary or image
field is returned as an `odoo://record/{model}/{id}/{field}` URI, together with a
`binary_note` that points the client at `read_attachment`. Files attached to a record
(chatter attachments, documents) are listed by `list_record_attachments`, each with an
`odoo://attachment/{id}` URI.

`read_attachment` takes such a `uri` (or a plain `attachment_id`) and a `format`:

- `auto` (default): text files come back inline, images as an MCP `image` content block,
  PDF and Office documents as extracted text, and anything else — or anything above the
  inline size caps — as a download link plus metadata. Inline PDF/Office text needs the
  standard `attachment_indexation` module; without it the tool returns the link and says
  so. Inline text is truncated at 100,000 characters (with a link to the full file),
  images and blobs are inlined up to 256 KiB.
- `link`: only a time-limited download URL, no bytes. Works for read-only OAuth sessions
  and for clients that can fetch URLs.
- `blob`: the raw file as an embedded resource (base64 for binary files, text for
  textual mimetypes), refused above 256 KiB.

A download link is a **delegated-access bearer URL**: it is an HMAC token over the
record, field and expiry, redeemed by the module's `/mcp/download/<model>/<id>/<field>`
route, which answers 404 to any other or expired token. Anyone who holds the link can
download the file until it expires, without an Odoo login, and the download bypasses MCP
auditing and rate limiting. The tool call that produced the link is audited as usual.
The link validity is **Download Link Validity** in the settings
(`mcp_server.link_ttl_hours`, default 1 hour; the module clamps it at 1008 hours = 42
days). Keep it short: an AI client simply requests a new link when one has expired. A
link cannot be revoked individually; only rotating the database secret invalidates all
of them.

`upload_attachment` creates an `ir.attachment` from base64 content. Pass `model` and
`record_id` to attach it to a record — this is gated as a **write** on that record, both
via MCP (`allow_write`) and by Odoo (write access to the record). Omit both for a
standalone attachment, which requires `ir.attachment` to be MCP-enabled for create. The
content is limited to about 7 MiB by the request body cap. Odoo stores HTML, XML and SVG
uploads from non-privileged users as `text/plain`. The returned URI can be read back
with `read_attachment` and the id linked to a chatter message with
`post_message(attachment_ids=[id])`.

**Gating.** An attachment is readable via MCP when its parent record's model is
MCP-enabled for read, **or** when `ir.attachment` itself is MCP-enabled for read. The
same rule applies to `list_record_attachments`. A record-field URI always requires the
record's own model to be read-enabled; enabling `ir.attachment` never unlocks arbitrary
binary fields. Odoo's attachment access rules apply on top in every case. To enable
`ir.attachment` itself, create the row directly under **Settings > Technical > MCP > MCP
Available Models** — the model selection wizard filters out `ir.*` models.

### Long Text Fields

The text block of a tool result shows up to 2,000 characters of each text value
(char, text and html fields). The limit is **Maximum Text Length** in the settings
(`mcp_server.max_field_length`, maximum 100,000). A longer value ends with a note that
names the `get_record` call that shows more of it. `structuredContent` always holds the
full value.

`get_record` shows a field that you name in `fields` up to 100,000 characters.
`search_records` keeps the limit, also for named `fields`, because one page holds many
records. `get_record` with smart defaults or `["__all__"]` also keeps the limit.

### Access Control

Every call runs **as the authenticated user** (the API key's owner, or the user who
logged in via OAuth), so Odoo's own access rights and record rules always apply. On top
of that, MCP access is gated by `mcp.enabled.model`:

- A model is reachable only if it is MCP-enabled, with the requested operation allowed
  (`allow_read` / `allow_create` / `allow_write` / `allow_unlink`).
- This also applies to a model that a write reaches through its values. Each x2many
  command needs its operation on the related model: create for `[0, 0, {...}]`, write
  for `[1, id, {...}]` and unlink for `[2, id]`. Removing lines of a one2many needs
  write on the line model, or unlink when the line model deletes removed lines.
  Replacing them needs write, and also unlink when the line model deletes removed lines.
  Linking a line to a one2many with `[4, id]` needs write on the line model. While a
  record is created, a clear does nothing and a replace only links, so neither needs
  unlink. Linking existing records of a many2many needs no entry, unless Odoo computes
  the many2many and writes it back through a method: then linking, unlinking and
  replacing need write on the related model. When Odoo computes a one2many and writes it
  back through a method, removing lines needs write and unlink. On create, Odoo starts
  such a computed field empty, so any value for it counts as a replace. Some fields come
  from a related record, for example the fields of the partner of a user. Such a field
  follows the rules of the field on that record. A user created for an existing partner
  writes that partner, so a clear or a replace there removes lines. This also holds for
  a user created as a new line of a partner. An x2many value must be a list of commands
  or a list of ids, and the values of a create or update command must be an object. The
  native tools and the legacy XML-RPC proxy apply the same rule.
- A create, update or delete on `ir.attachment` also needs write on the owner model
  (`res_model`), before and after the change, as `upload_attachment` does. This also
  applies to an attachment that a write reaches through an x2many command, for example
  `attachment_ids` of a message. An attachment without an owner needs only the
  `ir.attachment` entry.
- `call_model_method` additionally requires `allow_method_calls = True` on the model —
  an **admin opt-in, default off**. It exposes only the model's public _business_
  methods; private (leading-underscore or marked `@api.private` in Odoo), `web_*`, and
  generic ORM/CRUD methods are always rejected, and methods that map to a CRUD operation
  still require the matching per-operation permission. Such business methods may have
  side effects on this and related records (posting messages, scheduling activities,
  managing followers, etc.), but only ever do what the authenticated user could already
  do in Odoo. Leave it off unless you need it — and in particular do **not** enable it
  on infrastructure/meta models such as `ir.actions.server`, `ir.cron`,
  `base.automation`, `ir.model` or `ir.rule`, whose public methods (e.g. `run()`)
  execute arbitrary configured logic. Method calls on `ir.actions.*`, `ir.cron`,
  `ir.module.*` and the `base.module.*` wizards are always refused.
- `post_message` is gated as a **write** operation.
- Fields whose **name** looks like a credential (`*password`, `*secret`,
  `*_token`, `api_key` / `secret_key` / `private_key`, …) are omitted from **bulk reads** —
  the smart-default selection and the `["__all__"]` sentinel of `get_record` /
  `search_records` — as a convenience guard. This is best-effort and name-based
  only: explicitly-named fields are still returned, so protect real secrets with
  Odoo field-level `groups=`, which is the enforced boundary.
- A **bulk read** also leaves out the fields the caller cannot read. Some computed
  fields read models the caller has no access to, for example the totals that accounting
  adds to contacts and users. Instead of failing the whole call, `get_record` /
  `search_records` return the other fields, list the left-out ones in `skipped_fields`
  and add a note to the text. A field the caller names explicitly and cannot read still
  fails the call with the access error.
- Under a read-only OAuth session (scope `mcp:read`), write tools are hidden from
  `tools/list` and refused if called (see
  [OAuth Read-Only Consent](#oauth-read-only-consent)).

All tool calls are written to the audit log (`mcp.log`, browsable under **Settings >
Technical > MCP > MCP Logs**) and subject to per-user rate limiting (in-memory, enforced
per worker process). The legacy XML-RPC proxy counts a call only after the password or
API key is proven. A caller without credentials cannot use up the limit of a user.

The whole `POST /mcp` request body is capped at 10 MiB; since binary fields are written
base64-encoded, this limits a single `create_record` / `update_record` binary write to
roughly 7.5 MiB of decoded data — a larger payload is rejected with HTTP 413. The cap
applies to the whole batch of a `create_records` / `update_records` call, so a batch
that carries binary values reaches it much sooner than a single-record write.

### Resources

The endpoint serves `odoo://` resources so binary/image data is fetched on demand
instead of inlined as base64:

- `odoo://record/{model}/{id}/{field}` — a binary/image field on a record
- `odoo://attachment/{id}` — an `ir.attachment` by ID

`resources/read` is an application-driven MCP primitive that many chat clients do not
let the model call. The `read_attachment` tool accepts the same URIs and works in every
client; see [Attachments and Binary Fields](#attachments-and-binary-fields).
`resources/read` refuses a value above 10 MiB. For a larger file, call `read_attachment`
with `format="link"`.

## User Context, Scoped Keys, Read-Only Consent & Custom Tools

These four capabilities were added in version **2.0** to tighten security and give
admins more control over what MCP clients can do.

### User Context on Connect

LLMs otherwise guess the timezone and pick a wrong company on multi-company databases —
and those guesses turn into wrong writes. To prevent that, the `initialize` handshake
returns a personalized **`instructions`** string that spec-compliant clients inject into
the model's context automatically (zero extra round-trips). It describes the connected
**user**, their **timezone** (or a note that none is set), the **active company**, and
any other **allowed companies**, plus the rule that all datetimes are stored and
returned in **UTC**.

For clients that ignore `instructions`, the same information is available on demand
through the read-only **`get_current_context`** tool. The context only ever exposes the
caller's own user/company information — nothing else.

### "MCP Only" API Keys

By default an Odoo API key is a global credential that works on every RPC surface. You
can instead mint a key that is confined to the MCP endpoint:

1. Go to **My Profile > Account Security > New API Key**.
2. In the key wizard, set **Access** to **MCP only** (the default is _All APIs_).
3. Enter a description and copy the key — it is shown only once.

On Odoo 17 an API key has no expiry date. Delete a key in **Account Security** when you
no longer need it.

An **MCP only** key authenticates **only on `/mcp`**, so a leaked key has a much smaller
blast radius — it cannot be used for general XML-RPC/JSON-RPC access to your database
(including this module's own legacy `/mcp/xmlrpc/*` and REST endpoints). Existing keys
are unaffected: global (all-APIs) keys and `rpc`-scope keys keep working on `/mcp`
exactly as before.

### OAuth Read-Only Consent

When a browser client connects via
[OAuth](#option-1-log-in-with-odoo-oauth-21--recommended), the per-request consent
screen shows a **Create and modify data** switch (on by default):

- **Leave it on** → the session is granted the `mcp:write` scope: the client can read
  **and** write, exactly as before.
- **Turn it off** → the session is granted the read-only `mcp:read` scope. Write tools
  (`create_record`, `create_records`, `update_record`, `update_records`,
  `delete_record`, `call_model_method`, `post_message`, and any non-read-only
  [custom tool](#custom-tools)) are **not even
  listed** for that session, and any attempt to call one is refused with a clear
  read-only error.

If the client explicitly requests only `mcp:read`, the switch is not shown and the token
is read-only. The granted scope is computed server-side and can only ever be
**narrower** than what the client registered for — never wider. Refresh-token rotation
preserves the scope.

> The read/write gate applies to **OAuth tokens only**. API keys stay full-access — the
> per-model registry and Odoo's own access rights remain their control (and an
> [MCP only](#mcp-only-api-keys) key already narrows where a key can be used).

### Custom Tools

Instead of enabling generic `create`/`write` on a model, an administrator can expose a
**curated verb** — e.g. `confirm_sale_order` with a narrow input schema — by wrapping an
Odoo **server action** in a custom tool. Manage them under **Settings > Technical >
MCP > Custom Tools** (MCP Administrator group):

| Field             | Meaning                                                                                   |
| ----------------- | ----------------------------------------------------------------------------------------- |
| **Name**          | The tool name the LLM calls (`^[A-Za-z0-9_-]{1,64}$`, unique, no builtin-name collisions) |
| **Description**   | The tool's contract with the LLM — say what it does and what each argument is             |
| **Input Schema**  | JSON Schema (a JSON object) advertised to clients in `tools/list`                         |
| **Read-only**     | Advertised as the tool's `readOnlyHint`; read-only OAuth sessions may call these only     |
| **Server Action** | The `ir.actions.server` executed when the tool is called                                  |

The tool's **arguments** and **result** flow through a shared `mcp` dict exposed to the
server action's Python code: read `mcp['args']` (the client-supplied arguments) and
assign `mcp['result']` (returned to the client, serialized as JSON text). A minimal
copy-paste code action:

```python
# Server action -> Python Code
mcp['result'] = {'echo': mcp['args'].get('x')}
```

Calling this tool with `{"x": "hello"}` returns `{"echo": "hello"}`.

> **Custom tools must wrap a Python Code action.** A custom tool must wrap a **Python
> Code** server action that reads `mcp['args']` and assigns `mcp['result']` (returned to
> the client as JSON text). Other server-action types (_Update a Record_, _Create a new
> Record_, _Duplicate a Record_, _Send Webhook Notification_, _Multi Actions_, and
> Discuss actions such as _Send Email_) are **not supported** and are rejected when you
> save the tool: they run once per selected record, and a tool call passes no record, so
> the action would silently do nothing while still reporting success. A Python Code
> action that assigns nothing to `mcp['result']` still runs — the client just receives a
> generic success message instead of a structured result.

**Access model — read this before exposing a tool:**

- The action runs **as the calling user**, so their Odoo access rights (ACLs) apply to
  everything the action does — a custom tool never grants elevated rights.
- **Who may call the tool** is the action's own **Allowed Groups**: a user must be a
  member of one of them. If the action has **no** Allowed Groups, access falls back to
  requiring **write** access on the action's model (Odoo's core rule) — so even a
  read-only tool then needs model write access. **Set Allowed Groups explicitly** to
  control access cleanly.
- The action's model does **not** need to be in the
  [Enabled Models](#step-2-configure-the-module) list, and — for a model that _is_
  enabled — its per-operation **Allow Read/Create/Write/Delete** flags **do not apply** to
  a custom tool. The tool-level access gate (plus the caller's Odoo ACLs) is the control,
  so a custom tool can perform an operation you disabled there (e.g. create a contact
  while `res.partner` **Allow Create** is off). Scope the wrapped action narrowly.
- A tool a user may not run is **hidden** from their `tools/list` and refused (with a
  sanitized access-denied error) if called directly.
- Under a read-only OAuth session (`mcp:read`), only tools marked **Read-only** are
  listed and callable.

The client's arguments are checked against the **Input Schema** before the action runs,
with the same rules as the built-in tools (see
[Argument validation](#argument-validation)): `required`, `type`, `enum`, `minItems` and
simple `items.type` on the top-level properties. Other JSON Schema keywords are not
enforced, so validate anything finer in the action itself. A schema part the server
cannot read is skipped rather than failing the call. An unknown type name also writes a
warning to the server log.

If an action raises, the whole call is rolled back and the client receives a sanitized
error (no traceback or SQL is leaked). Every custom-tool call is written to the audit
log (`mcp.log`).

> **Metadata is visible to authorized callers.** A custom tool's **name**,
> **description** and **input schema** are shown (via an internal privileged read in the
> MCP controller — there is no direct ACL grant on the model) to exactly the users
> allowed to run it: members of the action's Allowed Groups (including portal users), or
> — when no groups are set — users with write access to the action's model. The wrapped
> action's **code** is never exposed by this read (it stays restricted to the
> Settings/Technical group on `ir.actions.server`). Still, do **not** put secrets in a
> tool's name or description.

## Installation

### Step 1: Install from Odoo App Store

1. Download the module
2. Copy to Odoo addons
   ```bash
   cp -r mcp_server /path/to/odoo/addons/
   ```
3. Update the module list:
   - Navigate to Apps in Odoo
   - Click "Update Apps List"
   - Search for "MCP Server"
4. Click Install on the MCP Server module

### Step 2: Configure the Module

1. **Navigate to Settings**:

   - Go to Settings > MCP Server
   - Turn on **Enable MCP Server** (the master switch). Optional switches live here too:
     **Allow OAuth 2.1 login**, rate limiting and its request limit, logging and its
     retention, the default/maximum record limits for tool responses (plus the smart
     field, related-item and **Maximum Text Length** caps), the **Maximum Batch Size**
     for `create_records` / `update_records`, the **Download Link Validity** of
     `read_attachment` links, an optional **Allowed Browser Origins** allowlist (empty
     by default = any Origin accepted; when set, browser requests from other Origins get
     HTTP 403 — native clients send no Origin header and are never affected), and the
     **Extra Context Keys for XML-RPC Writes** that the XML-RPC proxy accepts on write
     calls (empty by default, see [XML-RPC API](#xml-rpc-api)).

2. **Enable Models**:

   - Click "Configure Models" or go to **Settings > Technical > MCP > MCP Available
     Models**
   - Add models you want to expose (e.g., res.partner, product.product)
   - Set permissions for each model:
     - ✅ Can Read
     - ✅ Can Write
     - ✅ Can Create
     - ✅ Can Delete
     - (optional) Allow Method Calls — opt-in for `call_model_method`

3. **Connect a client** — see [Connecting AI Assistants](#connecting-ai-assistants):
   OAuth needs no further setup; for API keys, mint one under **My Profile > Account
   Security** (choose **MCP only** for the smallest blast radius).

### Security Groups

The module creates two security groups:

- **MCP Administrator**: Can configure MCP settings, manage enabled models, custom
  tools, OAuth clients/tokens, and read the audit log
- **MCP User**: **Required to use MCP.** Only members can authenticate on any MCP
  surface — the native `/mcp` endpoint (API key or OAuth), the REST endpoints and the
  XML-RPC proxy, including `common.authenticate` / `login` — and only members can
  complete the OAuth consent flow. A valid credential whose user is not a member is
  refused with an audited **403** (the OAuth consent screen shows a clear error
  instead), and removing the group cuts off the user's outstanding API keys and OAuth
  tokens immediately. What a member can read or write is still bounded by the per-model
  MCP permissions **and** the user's own Odoo access rights.

Assign users to appropriate groups in Settings > Users & Companies > Users.
MCP Administrator implies MCP User, and system administrators (Settings access) are
MCP Administrators automatically. Portal users cannot be members (the group implies
Internal User), so portal accounts can never connect to MCP.

The MCP menus sit under **Settings > Technical > MCP**. To see them, an MCP
Administrator also needs the _Access Rights_ administration level and debug mode.

> **Upgrading:** users with existing MCP activity (an **MCP only** API key, an OAuth
> token, or an audit-log entry recording a completed MCP operation) are added to the
> group automatically; assign it manually for anyone else. The audit-log signal only
> reaches users active within `mcp_server.log_retention_days` (default 30) — the daily
> cleanup cron deletes older entries — so users connecting with a global or `rpc`-scope
> API key on a longer cadence will need the group assigned manually.

### Upgrading from version 1.2

A database with version 1.2 of this module (the XML-RPC/REST proxy) updates in place.
Update the module, and the native `/mcp` endpoint, the OAuth server and the custom tools
become available. The enabled models, the settings, the API keys and the audit log stay,
and the legacy `/mcp/xmlrpc/*` and REST endpoints keep working with the same API keys.

- Version 2 requires the **MCP User** group on every MCP endpoint (see
  [Security Groups](#security-groups)). The update grants the group to users whose audit
  log shows a completed MCP operation. Operations on the version 1.2 XML-RPC proxy do
  not count, because version 1.2 could log them for a user that was not signed in. Every
  other user of the legacy endpoints needs an administrator to add the group, or gets
  HTTP 403 (fault 403 on XML-RPC).
- `mcp_server.request_timeout`, the request timeout of version 1.2, no longer applies.
- A request limit from 1 to 9 counts as 10.
- Check the members of the **MCP User** group after the update: the update grants the
  group from the audit log of version 1.2.
- Version 1.2 applied the request limit on a database that never saved the MCP settings.
  From version 2 the limit is off until you turn it on in Settings.
- OAuth 2.1 login is on after the update (`mcp_server.enable_oauth`). Turn it off in
  Settings if no client uses it.

## Multi-Database Deployment

Before it can authenticate a request, Odoo must resolve **which database** the request
targets — and the Bearer token / OAuth session can't select one, because the API key and
OAuth token live *inside* a database. So on an instance serving **more than one
database**, every `/mcp*` route (including the public `/mcp/health` and the OAuth
discovery documents) returns **404 "No database is selected"** until the target database
is resolved from the request itself. Resolve it at the transport layer:

1. **Hostname per database (recommended)** — the standard Odoo multi-tenant setup: give
   each database its own host and let Odoo map host → database.

   ```ini
   # odoo.conf
   proxy_mode = True
   list_db    = False        # hide the database manager
   dbfilter   = ^%d$         # subdomain → db  (use ^%h$ to match the full host)
   ```

   Point each client at its own host (e.g. `https://acme.example.com/mcp`). This is the
   **only option that works for browser OAuth clients** (Claude.ai web, Gemini): they
   can't send a custom header, but the whole flow stays on one host, so `dbfilter`
   resolves it. Discovery documents and consent redirects are built from the request
   host, so each tenant automatically advertises its own correct URLs.

2. **`X-Odoo-Database` header — not available on Odoo 17.** Odoo 19 core resolves this
   header; Odoo 17 does not, so header-based selection cannot work on this version. Use
   hostname routing (option 1) or pin a single database (option 3).

3. **Single database** — pin the instance with `db_name = <db>` (or `dbfilter = ^<db>$`).

A single-database instance needs none of this — Odoo auto-selects the only database.

## API Endpoints

### Native MCP Endpoint

| Endpoint   | Method | Description                                                                                                                                                                                                            |
| ---------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/mcp`     | POST   | Native MCP server (Streamable HTTP, JSON-RPC 2.0). Auth via `Authorization: Bearer <token>` — an mcp- or rpc-scope API key or an OAuth 2.1 access token. See [Native MCP Endpoint (`/mcp`)](#native-mcp-endpoint-mcp). |
| `/mcp/rpc` | POST   | Legacy alias of `/mcp` (the pre-rename path) — same endpoint, same auth.                                                                                                                                               |

### OAuth 2.1

The module is an OAuth 2.1 authorization server, so browser clients can log into Odoo
(see [Option 1](#option-1-log-in-with-odoo-oauth-21--recommended)). These endpoints are
public (no API key required).

| Endpoint                                  | Method   | Description                                                                  |
| ----------------------------------------- | -------- | ---------------------------------------------------------------------------- |
| `/.well-known/oauth-protected-resource`   | GET      | RFC 9728 protected-resource metadata for `/mcp`                              |
| `/.well-known/oauth-authorization-server` | GET      | RFC 8414 authorization-server metadata                                       |
| `/mcp/oauth/authorize`                    | GET/POST | Odoo-login + per-request consent screen; issues an authorization code        |
| `/mcp/oauth/token`                        | POST     | Exchange an authorization code / refresh token for opaque tokens (PKCE S256) |
| `/mcp/oauth/register`                     | POST     | RFC 7591 dynamic client registration (PKCE, IP rate-limited)                 |

### REST API

All REST endpoints except `/mcp/health` require API key authentication via `X-API-Key` header.

| Endpoint                     | Method | Description                          |
| ---------------------------- | ------ | ------------------------------------ |
| `/mcp/health`                | GET    | Health check (no auth required)      |
| `/mcp/system/info`           | GET    | Get database and server information  |
| `/mcp/auth/validate`         | GET    | Validate API key                     |
| `/mcp/models`                | GET    | List all MCP-enabled models          |
| `/mcp/models/{model}/access` | GET    | Check access permissions for a model |

### XML-RPC API

MCP-specific XML-RPC endpoints with enhanced access control (used by the
[standalone client](#option-3-standalone-local-client-uvx-mcp-server-odoo)):

| Endpoint             | Description                              |
| -------------------- | ---------------------------------------- |
| `/mcp/xmlrpc/common` | Authentication services                  |
| `/mcp/xmlrpc/db`     | Database operations                      |
| `/mcp/xmlrpc/object` | Model operations with MCP access control |

Odoo business errors keep the fault codes of core's `/xmlrpc/2`: `2` for a user or
validation error, `3` for a refused login and `4` for an access error. An invalid value,
such as an unknown field name in a search domain, gets `1` with the error text, as in
core. Refusals by the MCP layer itself use `400` (invalid call, or a database other than
the one the request is served from), `403` (MCP disabled, model not enabled, user
outside the MCP User group) and `429` (rate limit).

The proxy applies the rules of [Access Control](#access-control) to the values of
`create`, `write`, `copy` and `unlink`. Pass these values by position or under the
method's own keyword (`vals_list`, `vals`, `default`). The `context` keyword is also
accepted. Other keyword arguments, keyword arguments that are neither an object nor
empty, and positional arguments that are not an array, get `400`. A default in the
request context must be a plain value: a context key such as `default_category_id` with
a list or an object gets `403`. So do `default_res_model` and `default_res_id` on a call
that creates an attachment. A `default_` key that names the parent of a new record, such
as `default_partner_id` for a user, counts as naming it. On a call that creates records,
a `default_` key of an x2many field counts as a value of that field: `False` clears it.
On a method that writes, the request context can hold only `lang`, `tz`, `uid`,
`active_test`, `allowed_company_ids`, `bin_size`, `tracking_disable`,
`mail_create_nolog`, `mail_create_nosubscribe` and `mail_notrack`. It can also hold the
keys of the setting **Extra Context Keys for XML-RPC Writes** (Settings > MCP Server,
empty by default). A `default_` key needs the setting too, for example
`default_partner_id`. Any other key gets `403`, and the message names the key. Many keys
make core or a standard module skip one of its checks. For example, with `default_state`
core can create a posted journal entry, because some create methods check a field only
in the values. Add a key only for an integration that needs it. Even if the setting
names `_import_current_module`, that key gets `403`, because with it core writes XML ids
with no access check. Reads accept any context key. On a method that writes, a context
or keyword arguments that are not an object get `400`. The keyword and context rules
also apply to `name_create`, whose own keyword is `name`. The proxy does not offer
`export_data`: read the records with `search_read`.

## Usage Example

### Testing the Installation

1. **Check health endpoint**:

   ```bash
   curl https://your-odoo.com/mcp/health
   ```

2. **Validate API key**:

   ```bash
   curl https://your-odoo.com/mcp/auth/validate \
     -H "X-API-Key: your-api-key-here"
   ```

3. **List enabled models**:

   ```bash
   curl https://your-odoo.com/mcp/models \
     -H "X-API-Key: your-api-key-here"
   ```

4. **Exercise the MCP handshake** (or use
   [MCP Inspector](#option-2-api-key-bearer-token) for an interactive session):

   ```bash
   curl -X POST https://your-odoo.com/mcp \
     -H "Authorization: Bearer your-api-key-here" \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
   ```

## Security Considerations

- **Prefer OAuth for interactive users**: tokens are short-lived, bound to one resource,
  revocable per client, and never need to be copied around
- **API Key Security**: keep API keys secure and rotate them regularly; prefer
  [MCP only](#mcp-only-api-keys) keys so a leak cannot reach general RPC
- **Least privilege**: only enable models that are necessary; grant read-only OAuth
  consent where write access is not needed
- **Model Access**: `call_model_method` is off by default — leave it off unless needed
- **HTTPS**: always use HTTPS in production environments
- **Rate Limiting**: the module includes per-user rate limiting for API endpoints
- **Audit Trail**: all MCP operations, denials and auth attempts are logged
  (**Settings > Technical > MCP > MCP Logs**)

## Development

### Running Tests

```bash
# Run all MCP module tests
/path/to/odoo-bin \
  -d your_database \
  -u mcp_server \
  --test-enable \
  --test-tags /mcp_server \
  --stop-after-init
```

## Troubleshooting

<details>
<summary>Module Not Found</summary>

- Ensure the module is in the correct addons path
- Update the apps list in Odoo (Apps > Update Apps List)
- Check module dependencies are satisfied
- Verify the module manifest file is valid

</details>

<details>
<summary>OAuth Login Fails or Keeps Re-Prompting</summary>

- Confirm MCP is enabled (Settings > MCP Server) **and** the **Allow OAuth 2.1 login**
  switch is on (system parameter `mcp_server.enable_oauth`)
- The authorize/consent screen requires a normal Odoo login for the connecting user —
  check the user is active and can log into the web client
- Check whether the token was revoked or its client deactivated under **Settings >
  Technical > MCP** — the client must re-authenticate after a revocation
- Access tokens expire after 1 hour; clients refresh automatically via the refresh
  token. If the refresh token was already used (rotation reuse), the whole grant is
  revoked as a safety measure — log in again
- Behind a reverse proxy, make sure Odoo sees the correct external HTTPS URL
  (`web.base.url`) so the discovery documents and consent redirects point at the right
  origin

</details>

<details>
<summary>API Key Not Working</summary>

- Verify the key is active in the user's API Keys tab
- A **403** means the key is valid but the user is not in the **MCP User** (or MCP
  Administrator) group — membership is required on every MCP surface; the refusal is
  recorded in the audit log with the user
- Ensure the header is properly formatted (`Authorization: Bearer <key>` on `/mcp`;
  `X-API-Key` on the REST endpoints)
- An **MCP only** key works **only** on `/mcp` — it is rejected on the REST/XML-RPC
  endpoints by design
- Try regenerating the API key
- Check that the user account is active and not archived

</details>

<details>
<summary>Model Access Denied</summary>

- Confirm the model is in the MCP enabled models list (**Settings > Technical > MCP >
  MCP Available Models**)
- Check the specific permissions (read/write/create/delete) for that model
- Verify the user's security group membership
- Ensure the user has Odoo permissions for that model too
- Check if record rules are blocking access
- On an OAuth session: a read-only consent (`mcp:read`) hides and refuses all write
  tools — reconnect and leave the **Create and modify data** switch on if writes are
  needed

</details>

<details>
<summary>Connection Issues</summary>

- Verify your Odoo URL is accessible from the client
- Check if HTTPS is properly configured
- Ensure firewall rules allow the connection
- Test with the health endpoint first: `curl https://your-odoo.com/mcp/health`
- Check Odoo logs for any error messages

</details>

<details>
<summary>Endpoint 404s / "No database is selected"</summary>

- The Odoo instance serves **more than one database**, so Odoo can't tell which one the
  request targets — every `/mcp*` route 404s and the body reads *"No database is
  selected"*
- Resolve the target database at the transport layer — see
  [Multi-Database Deployment](#multi-database-deployment): hostname-based `dbfilter`,
  or pin a single database with `db_name` (the `X-Odoo-Database` header is Odoo 19+
  core and does not work on Odoo 17)
- Single-database instances are unaffected — Odoo auto-selects the only database

</details>

<details>
<summary>Requests Return 429 (Too Many Requests)</summary>

- The per-minute rate limit was exceeded. The `429` responses of `/mcp` and the REST
  endpoints carry a `Retry-After: 60` header; the XML-RPC proxy answers with fault code
  `429` and the same header
- Wait for the window to reset, then retry — or raise **Request Limit per Minute** (or
  turn rate limiting off) in Settings > MCP Server

</details>

<details>
<summary>Performance Issues</summary>

- Enable only necessary models to reduce overhead
- Use field filtering in API calls to limit data transfer
- Consider implementing caching in your client
- Check if rate limiting is affecting your requests
- Monitor Odoo server resources (CPU, memory, database)

</details>

## License

This module is licensed under OPL-1.
See [LICENSE](LICENSE) for the full text.

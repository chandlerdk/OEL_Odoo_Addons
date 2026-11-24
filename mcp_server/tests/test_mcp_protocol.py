"""Tests for the native MCP protocol endpoint (``POST /mcp``).

Exercises the JSON-RPC handshake (``initialize``/``ping``/``tools/list``/
``notifications/initialized``), bearer authentication (``auth='mcp'``), the
global enable gate, and JSON-RPC error rendering. Mirrors the HttpCase +
``_generate("rpc", ...)`` key-minting pattern of ``test_main_controller.py``.
"""

import json
import time
from unittest.mock import patch

from odoo.tests import common, tagged
from odoo.tools import mute_logger

from ..controllers import error_sanitizer, mcp, rate_limiting, utils
from ..controllers.mcp import MCPController
from ..controllers.mcp_route import MCP_MAX_CONTENT_LENGTH
from ..models import ir_http
from .test_helpers import create_test_user, grant_mcp_access, post_chunked

# Must match mcp_server/controllers/mcp.py.
PREFERRED_PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "much-mcp-server"


@tagged("much_unit", "post_install", "-at_install")
class TestMcpProtocol(common.HttpCase):
    """Native MCP protocol handshake + auth on ``/mcp``."""

    def setUp(self):
        super().setUp()
        utils.clear_mcp_caches()
        # Reset the shared in-memory rate-limit caches for test independence
        # (both the per-user limiter and the per-IP bearer-failure audit throttle
        # are module-level globals that otherwise carry state across tests).
        rate_limiting._api_limiter.clear()
        ir_http._bearer_failure_limiter.clear()
        mcp._audit_write_limiter.clear()

        # Unique login to avoid collisions across runs.
        unique_id = str(int(time.time() * 1000))[-6:]
        login = f"mcp_proto_user_{unique_id}"
        self.mcp_user = create_test_user(
            self.env,
            "MCP Protocol User",
            login,
            email=f"mcp_proto_{unique_id}@example.com",
        )
        grant_mcp_access(self.mcp_user)

        # Mint an rpc-scope API key for the user.
        env_as_user = self.env(user=self.mcp_user)
        self.api_key = env_as_user["res.users.apikeys"]._generate(
            "rpc", "Test MCP Protocol Key"
        )

        # Enable MCP globally and drop any stale cached toggle value.
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.enabled", "True")
        utils.clear_mcp_caches()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    _DEFAULT_KEY = object()

    def _post_rpc(self, body, api_key=_DEFAULT_KEY):
        """POST ``body`` to ``/mcp``.

        ``body`` may be a dict (JSON-encoded) or a raw string (sent verbatim,
        for malformed-envelope tests). ``api_key`` defaults to the minted key;
        pass ``None`` to omit the ``Authorization`` header entirely.
        """
        headers = {"Content-Type": "application/json"}
        if api_key is self._DEFAULT_KEY:
            api_key = self.api_key
        if api_key is not None:
            headers["Authorization"] = f"Bearer {api_key}"
        if not isinstance(body, str):
            body = json.dumps(body)
        return self.url_open("/mcp", data=body, headers=headers)

    # ------------------------------------------------------------------
    # initialize / negotiation
    # ------------------------------------------------------------------
    def test_initialize_returns_capabilities_and_server_info(self):
        """initialize returns protocolVersion, capabilities and serverInfo."""
        response = self._post_rpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": PREFERRED_PROTOCOL_VERSION},
            }
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertEqual(result["protocolVersion"], PREFERRED_PROTOCOL_VERSION)
        # Tools capability is advertised (empty object).
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], SERVER_NAME)
        self.assertTrue(result["serverInfo"]["version"])
        # serverInfo.description (2025-11-25 addition).
        self.assertTrue(result["serverInfo"]["description"])

    def test_initialize_supported_older_revision_is_echoed(self):
        """A client asking for supported-but-older 2025-06-18 gets it echoed.

        Version negotiation must honour every advertised revision, not force
        the preferred one onto clients that have not adopted it yet.
        """
        response = self._post_rpc(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["protocolVersion"], "2025-06-18")

    def test_initialize_pre_batch_removal_version_negotiated_down(self):
        """A batch-era revision (2025-03-26) is negotiated down to the preferred.

        2025-03-26 / 2024-11-05 mandate JSON-RPC batching, which this server does
        not implement, so they are not advertised; a client requesting one
        is answered with the preferred (batch-free) revision.
        """
        response = self._post_rpc(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["result"]["protocolVersion"],
            PREFERRED_PROTOCOL_VERSION,
        )

    def test_initialize_unsupported_version_falls_back_to_preferred(self):
        """An unsupported requested version falls back to the preferred one."""
        response = self._post_rpc(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "initialize",
                "params": {"protocolVersion": "1999-01-01"},
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["result"]["protocolVersion"],
            PREFERRED_PROTOCOL_VERSION,
        )

    # ------------------------------------------------------------------
    # tools/list, ping
    # ------------------------------------------------------------------
    def test_tools_list_advertises_registered_tools(self):
        """tools/list advertises the registered MCP tools with their schemas.

        Read-tool coverage lives in ``test_mcp_read_tools``; this asserts only
        the handshake contract (name + camelCase ``inputSchema``).
        """
        response = self._post_rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
        self.assertEqual(response.status_code, 200)
        tools = response.json()["result"]["tools"]
        names = {tool["name"] for tool in tools}
        self.assertIn("search_records", names)
        self.assertIn("get_record", names)
        for tool in tools:
            self.assertIn("name", tool)
            self.assertIn("inputSchema", tool)

    def test_tools_list_advertises_batch_write_tools(self):
        """create_records / update_records ship valid schemas and annotations."""
        response = self._post_rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
        self.assertEqual(response.status_code, 200)
        tools = {tool["name"]: tool for tool in response.json()["result"]["tools"]}
        expected = {
            "create_records": (["model", "records"], False),
            "update_records": (["model"], True),
        }
        for name, (required, idempotent) in expected.items():
            with self.subTest(name):
                self.assertIn(name, tools)
                schema = tools[name]["inputSchema"]
                self.assertEqual(schema["required"], required)
                self.assertFalse(schema["additionalProperties"])
                annotations = tools[name]["annotations"]
                self.assertFalse(annotations["readOnlyHint"])
                self.assertFalse(annotations["destructiveHint"])
                self.assertEqual(annotations["idempotentHint"], idempotent)
        self.assertEqual(
            tools["create_records"]["inputSchema"]["properties"]["records"]["items"],
            {"type": "object", "minProperties": 1},
        )
        updates = tools["update_records"]["inputSchema"]["properties"]["updates"]
        self.assertEqual(updates["items"]["required"], ["id", "values"])

    def test_ping_returns_empty_result(self):
        """ping acknowledges liveness with an empty result object."""
        response = self._post_rpc({"jsonrpc": "2.0", "id": 5, "method": "ping"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], {})

    def test_legacy_rpc_alias_path_still_served(self):
        """POST /mcp/rpc (the legacy alias) reaches the same endpoint."""
        response = self.url_open(
            "/mcp/rpc",
            data=json.dumps({"jsonrpc": "2.0", "id": 6, "method": "ping"}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], {})

    # ------------------------------------------------------------------
    # Origin validation (2025-11-25: permissive default + admin allowlist)
    # ------------------------------------------------------------------
    def _set_allowed_origins(self, value):
        """Set the ``mcp_server.allowed_origins`` param and flush the caches."""
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.allowed_origins", value
        )
        utils.clear_mcp_caches()

    def _ping_with_origin(self, origin):
        """POST an authenticated ping carrying an ``Origin`` header."""
        return self.url_open(
            "/mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "Origin": origin,
            },
        )

    def test_origin_unrestricted_by_default(self):
        """With no allowlist configured, any Origin passes (permissive default)."""
        response = self._ping_with_origin("https://anywhere.example")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), "*")

    def test_origin_not_on_allowlist_is_403(self):
        """With an allowlist configured, a foreign Origin is refused with 403."""
        self._set_allowed_origins("https://app.example.com")
        response = self._ping_with_origin("https://evil.example")
        self.assertEqual(response.status_code, 403)
        self.assertIn("Origin not allowed", response.text)

    def test_origin_on_allowlist_passes_and_is_echoed(self):
        """An allowlisted Origin passes; CORS echoes it instead of the wildcard.

        The allowlist entry carries mixed case and a trailing slash to prove
        the normalization (lowercase, trailing-/ stripped) on the config side.
        """
        self._set_allowed_origins("HTTPS://App.Example.COM/")
        response = self._ping_with_origin("https://app.example.com")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Origin"),
            "https://app.example.com",
        )
        self.assertIn("Origin", response.headers.get("Vary", ""))

    def test_origin_absent_always_passes(self):
        """Native (non-browser) clients send no Origin and are never refused."""
        self._set_allowed_origins("https://app.example.com")
        response = self._post_rpc({"jsonrpc": "2.0", "id": 8, "method": "ping"})
        self.assertEqual(response.status_code, 200)

    def test_origin_preflight_from_disallowed_origin_is_403(self):
        """A CORS preflight (OPTIONS) from a disallowed Origin is refused too."""
        self._set_allowed_origins("https://app.example.com")
        response = self.opener.options(
            self.base_url() + "/mcp",
            headers={"Origin": "https://evil.example"},
            timeout=12,
        )
        self.assertEqual(response.status_code, 403)

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    def test_valid_key_connects(self):
        """A valid bearer key authenticates the request (HTTP 200)."""
        response = self._post_rpc({"jsonrpc": "2.0", "id": 6, "method": "ping"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("error", response.json())

    def test_missing_key_returns_401(self):
        """A request without an Authorization header is rejected with 401."""
        response = self._post_rpc(
            {"jsonrpc": "2.0", "id": 7, "method": "ping"}, api_key=None
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("Bearer", response.headers.get("WWW-Authenticate", ""))

    def test_invalid_key_returns_401(self):
        """A request with an invalid bearer key is rejected with 401."""
        response = self._post_rpc(
            {"jsonrpc": "2.0", "id": 8, "method": "ping"},
            api_key="not-a-real-key",
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("Bearer", response.headers.get("WWW-Authenticate", ""))

    def _post_rpc_raw_auth(self, body, authorization):
        """POST ``body`` to ``/mcp`` with a verbatim ``Authorization`` value."""
        return self.url_open(
            "/mcp",
            data=json.dumps(body),
            headers={
                "Content-Type": "application/json",
                "Authorization": authorization,
            },
        )

    def test_bare_token_without_bearer_prefix_authenticates(self):
        """``Authorization: <key>`` (no ``Bearer `` scheme) authenticates.

        Tolerance for hand-written client configs: a bare single-word token
        goes through the same two front doors as the canonical form.
        """
        response = self._post_rpc_raw_auth(
            {"jsonrpc": "2.0", "id": 20, "method": "ping"}, self.api_key
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], {})

    def test_bare_invalid_token_returns_401(self):
        """A bare (scheme-less) INVALID token is rejected like any other.

        It must reach the credential doors (and fail there), not slip through
        the parsing tolerance: the 401 carries the invalid-credentials
        description, not the malformed-header one.
        """
        response = self._post_rpc_raw_auth(
            {"jsonrpc": "2.0", "id": 21, "method": "ping"}, "not-a-real-key"
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("Bearer", response.headers.get("WWW-Authenticate", ""))
        self.assertIn("Invalid or expired credentials", response.text)

    def test_other_auth_scheme_returns_401_as_malformed(self):
        """``Authorization: Basic ...`` is malformed here, not a bare token.

        Credentials of a different auth scheme must not be forwarded to the
        token doors: the 401 carries the missing-credentials description
        (rejected at parsing), not the invalid-credentials one.
        """
        response = self._post_rpc_raw_auth(
            {"jsonrpc": "2.0", "id": 22, "method": "ping"}, "Basic dXNlcjpwYXNz"
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("Missing credentials", response.text)
        self.assertNotIn("Invalid or expired credentials", response.text)

    def test_lone_scheme_word_returns_401_as_malformed(self):
        """A lone ``Authorization: Bearer`` (no credential) is malformed.

        The scheme word itself must not be looked up as the literal token
        "bearer" through the bare-token tolerance.
        """
        response = self._post_rpc_raw_auth(
            {"jsonrpc": "2.0", "id": 23, "method": "ping"}, "Bearer"
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("Missing credentials", response.text)

    def test_archived_user_key_returns_401(self):
        """An rpc-scope key whose owner is archived is rejected with 401."""
        unique_id = str(int(time.time() * 1000))[-6:]
        archived_user = create_test_user(
            self.env,
            "MCP Archived User",
            f"mcp_archived_user_{unique_id}",
            email=f"mcp_archived_{unique_id}@example.com",
        )
        key = self.env(user=archived_user)["res.users.apikeys"]._generate(
            "rpc", "Archived User Key"
        )
        # Deactivate the owner AFTER minting the key: an outstanding rpc key must
        # stop authenticating the moment its user is archived.
        archived_user.active = False

        response = self._post_rpc(
            {"jsonrpc": "2.0", "id": 12, "method": "ping"}, api_key=key
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("Bearer", response.headers.get("WWW-Authenticate", ""))

    def test_non_rpc_scope_key_returns_401(self):
        """A key minted for a non-rpc scope is not accepted at /mcp (401)."""
        # Front door 1 accepts a credential scoped to 'mcp' or 'rpc'; a key
        # scoped to neither must not resolve a user (no OAuth fallback either).
        key = self.env(user=self.mcp_user)["res.users.apikeys"]._generate(
            "mcp_not_rpc", "Non-RPC Scope Key"
        )
        response = self._post_rpc(
            {"jsonrpc": "2.0", "id": 13, "method": "ping"}, api_key=key
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("Bearer", response.headers.get("WWW-Authenticate", ""))

    def test_bearer_failure_audit_is_ip_throttled(self):
        """Invalid bearers stay 401, but the failure-audit write is capped per IP.

        The audit write checks out a fresh cursor + fsync per attempt and runs
        before the per-user request limiter, so without a per-IP cap an invalid
        bearer flood amplifies into DB load. Past the cap the audit is skipped
        (the 401 is unaffected); the limiter records at most the cap.
        """
        ir_http._bearer_failure_limiter.clear()
        attempts = ir_http._BEARER_FAILURE_MAX + 5
        statuses = [
            self._post_rpc(
                {"jsonrpc": "2.0", "id": 1, "method": "ping"}, api_key="bad-key"
            ).status_code
            for _ in range(attempts)
        ]
        # Every attempt is still rejected...
        self.assertEqual(statuses, [401] * attempts, msg=statuses)
        # ...but the throttle recorded no more than the cap (over-cap attempts
        # skipped the audit write). Without the throttle it would be untouched (0).
        recorded = sum(
            len(stamps) for stamps in ir_http._bearer_failure_limiter._buckets.values()
        )
        self.assertEqual(recorded, ir_http._BEARER_FAILURE_MAX)

    def test_error_audit_write_is_ip_throttled(self):
        """Rejected tool calls stay errors, but the error-audit write is capped.

        Each unknown-tool call would otherwise force its own independent-cursor
        commit (fsync); with the per-user request limiter off by default, one
        credential can amplify a flood of cheap malformed calls into a DB commit
        per request. Past the per-IP cap the audit write is skipped (the JSON-RPC
        error response is unaffected); the limiter records at most the cap.
        """
        # Disable the per-user request limiter so every call reaches tool
        # dispatch (its 429 would otherwise pre-empt the audit path we exercise);
        # the audit throttle is the failure mode under test.
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.enable_rate_limiting", "False"
        )
        mcp._audit_write_limiter.clear()
        attempts = mcp._AUDIT_WRITE_MAX + 5
        for _ in range(attempts):
            response = self._post_rpc(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "does_not_exist_tool", "arguments": {}},
                }
            )
            # Every rejected call still returns a JSON-RPC error (200 envelope).
            self.assertEqual(response.status_code, 200)
            self.assertIn("error", response.json())
        # ...but the throttle recorded no more than the cap: the over-cap calls
        # skipped the committed audit write. Without the throttle it would be
        # untouched (0).
        recorded = sum(
            len(stamps) for stamps in mcp._audit_write_limiter._buckets.values()
        )
        self.assertEqual(recorded, mcp._AUDIT_WRITE_MAX)

    @mute_logger("odoo.addons.mcp_server.controllers.mcp")
    def test_tool_call_survives_audit_write_failure(self):
        """A model_access audit-write error must not break a successful tool call.

        Auditing must never break the response: the success audit row is written
        on the request cursor in the tools/call ``finally``; if that write raises,
        the tool result is still returned rather than surfaced as an error.
        """
        with patch.object(
            type(self.env["mcp.log"]),
            "log_model_access",
            side_effect=Exception("audit infra down"),
        ):
            response = self._post_rpc(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "list_models", "arguments": {}},
                }
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotIn("error", body)
        self.assertFalse(body["result"]["isError"], msg=body)

    def test_no_per_request_auth_success_row(self):
        """No auth_success row is written per request (only failures are logged)."""
        log_model = self.env["mcp.log"].sudo()
        before = log_model.search([], order="id desc", limit=1).id or 0
        # A protocol request and a tool call: neither should log auth_success.
        self._post_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self._post_rpc(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "list_models", "arguments": {}},
            }
        )
        self.env.invalidate_all()
        new_auth_success = log_model.search(
            [("id", ">", before), ("event_type", "=", "auth_success")]
        )
        self.assertFalse(
            new_auth_success,
            "auth_success rows should no longer be written per request",
        )

    # ------------------------------------------------------------------
    # notifications
    # ------------------------------------------------------------------
    def test_notifications_initialized_returns_202_no_body(self):
        """notifications/initialized is acknowledged with an empty HTTP 202."""
        response = self._post_rpc(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.content, b"")

    def test_message_without_id_gets_no_response(self):
        """A message without an ``id`` is a notification: HTTP 202, no body.

        JSON-RPC 2.0 answers no notification, and MCP requests MUST carry an
        id, so ``ping`` / an unknown method sent without one get an empty 202
        instead of a result or error object with ``id: null``.
        """
        for method in ("ping", "does/not/exist"):
            with self.subTest(method):
                response = self._post_rpc({"jsonrpc": "2.0", "method": method})
                self.assertEqual(response.status_code, 202)
                self.assertEqual(response.content, b"")

    def test_request_without_id_is_not_executed(self):
        """A request method sent without an ``id`` never runs its side effect.

        Nobody could receive the outcome, so the call is acknowledged with an
        empty 202 and the tool is not invoked (no tool audit row either).
        """
        mixin_cls = type(self.env["mcp.mixin"])
        with patch.object(mixin_cls, "list_models") as mocked:
            response = self._post_rpc(
                {
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": "list_models", "arguments": {}},
                }
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.content, b"")
        mocked.assert_not_called()

    def test_unusable_id_is_invalid_request(self):
        """An ``id`` that is null or not a string/integer is refused (-32600).

        MCP: a request id is a string or integer and MUST NOT be null. The
        request is not run, and the unusable id is not echoed back.
        """
        for bad_id in (None, True, 1.5, [1], {"a": 1}):
            with self.subTest(id=bad_id):
                response = self._post_rpc(
                    {"jsonrpc": "2.0", "id": bad_id, "method": "ping"}
                )
                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual(body["error"]["code"], -32600)
                self.assertIsNone(body["id"])
        for good_id in ("req-1", 0):
            with self.subTest(id=good_id):
                response = self._post_rpc(
                    {"jsonrpc": "2.0", "id": good_id, "method": "ping"}
                )
                self.assertEqual(
                    response.json(), {"jsonrpc": "2.0", "id": good_id, "result": {}}
                )

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------
    def test_unknown_method_returns_method_not_found(self):
        """An unknown method yields JSON-RPC error -32601."""
        response = self._post_rpc(
            {"jsonrpc": "2.0", "id": 9, "method": "does/not/exist"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["error"]["code"], -32601)

    def test_envelope_context_member_does_not_crash_messages(self):
        """A ``context`` member in the envelope does not break translated errors.

        Odoo's ``_()`` reads a local named ``kwargs`` as a dict holding a
        ``context``; the handler bound the client envelope to that name, so
        ``"context": "x"`` turned -32601 into a generic -32603.
        """
        response = self._post_rpc(
            {"jsonrpc": "2.0", "id": 9, "method": "does/not/exist", "context": "x"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["error"]["code"], -32601)

    def test_invalid_envelope_returns_invalid_request(self):
        """A bad JSON-RPC envelope (wrong version) yields error -32600."""
        response = self._post_rpc({"jsonrpc": "1.0", "id": 10, "method": "ping"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["error"]["code"], -32600)

    def test_malformed_json_body_returns_parse_error(self):
        """A truly malformed JSON body yields JSON-RPC parse error -32700.

        The raw-body decode happens in the MCP dispatcher, which catches the
        JSON decode failure and renders -32700 (spec-correct) with a null id.
        """
        response = self._post_rpc("not-json{")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["error"]["code"], -32700)
        self.assertIsNone(body["id"])

    @mute_logger("odoo.http")
    def test_unexpected_exception_returns_sanitized_internal_error(self):
        """An unexpected error escaping dispatch renders a sanitized -32603.

        A method handler that raises a non-JSONRPCError (here ``ping``, patched
        to raise a ``RuntimeError`` carrying traceback-shaped internals) bypasses
        the narrow ``except JSONRPCError`` in ``handle_rpc`` and the tool-level
        catch, so it reaches ``ir.http._handle_error``. The client must receive
        only the generic internal-error object -- request id echoed, no ``data``
        field, and none of the raised message's internals leaked.
        """

        def boom(self, params):
            raise RuntimeError(
                'leaky File "/opt/odoo/secret.py", line 7 '
                "psycopg2.OperationalError at 0x7f00"
            )

        with patch.dict(MCPController._METHOD_HANDLERS, {"ping": boom}):
            response = self._post_rpc({"jsonrpc": "2.0", "id": 99, "method": "ping"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        # The dispatcher stashes the request id before dispatch, so it survives
        # even when the handler blows up.
        self.assertEqual(body["id"], 99)
        error = body["error"]
        self.assertEqual(error["code"], -32603)
        self.assertEqual(error["message"], error_sanitizer.GENERIC_ERROR_MESSAGE)
        # No internal detail leaks: no data field, and none of the raised
        # message's traceback/path/handle fragments appear anywhere in the body.
        self.assertNotIn("data", error)
        for leaked in ("Traceback", "secret.py", "psycopg2", "0x7f00", "RuntimeError"):
            self.assertNotIn(leaked, response.text)

    # ------------------------------------------------------------------
    # Global enable gate
    # ------------------------------------------------------------------
    def test_globally_disabled_is_refused(self):
        """With MCP disabled globally, requests are refused with HTTP 503 + -32000.

        A well-formed request the server refuses because of its own state is a
        server-defined error (-32000), not an invalid-request envelope (-32600);
        this mirrors the rate-limit path, which also uses -32000. The HTTP
        status is 503, like the REST door, so the refusal is visible below
        JSON-RPC too.
        """
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("mcp_server.enabled", "False")
        utils.clear_mcp_caches()
        try:
            response = self._post_rpc(
                {"jsonrpc": "2.0", "id": 11, "method": "initialize", "params": {}}
            )
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["id"], 11)
            error = response.json()["error"]
            self.assertEqual(error["code"], -32000)
            self.assertIn("disabled globally", error["message"])
        finally:
            params.set_param("mcp_server.enabled", "True")
            utils.clear_mcp_caches()

    # ------------------------------------------------------------------
    # Transport hardening (dispatcher)
    # ------------------------------------------------------------------
    def test_batch_array_returns_single_invalid_request(self):
        """A JSON-RPC batch array is refused with a single -32600 (id null).

        This server does not implement request batching; the dispatcher cannot
        merge a list into ``request.params`` so the handler sees an empty
        envelope and rejects it as an invalid request rather than fanning out.
        """
        response = self._post_rpc(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            ]
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotIsInstance(body, list)
        self.assertEqual(body["error"]["code"], -32600)
        self.assertIsNone(body["id"])

    @mute_logger("odoo.http")
    def test_deeply_nested_payload_returns_parse_error(self):
        """A deeply-nested body overflows json parsing -> -32700 (not -32603).

        json's recursive descent raises ``RecursionError`` (not ``ValueError``)
        on pathological nesting; the dispatcher must classify it as a parse
        error instead of letting it escape to a generic internal error.
        """
        depth = 100000
        body = "[" * depth + "]" * depth
        response = self._post_rpc(body)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], -32700)
        self.assertIsNone(payload["id"])

    @mute_logger("odoo.http", "werkzeug")
    def test_oversized_body_is_refused_with_413(self):
        """A body over the per-route cap is refused before it is parsed (413)."""
        oversized = "0" * (MCP_MAX_CONTENT_LENGTH + 1)
        response = self._post_rpc(oversized)
        self.assertEqual(response.status_code, 413)

    def _post_chunked(self, chunks):
        """POST ``chunks`` to ``/mcp`` as a chunked body (no Content-Length)."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        return post_chunked(self, "/mcp", chunks, headers)

    def test_oversized_chunked_body_is_refused_with_413(self):
        """A chunked body has no Content-Length; over the cap it is refused too."""
        block = b"0" * 65536
        status, _body = self._post_chunked(
            [block] * (MCP_MAX_CONTENT_LENGTH // len(block) + 1)
        )
        self.assertEqual(status, 413)

    def test_chunked_body_under_the_cap_is_served(self):
        """A chunked body under the cap is read in full and answered."""
        body = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}).encode()
        status, answer = self._post_chunked([body[:10], body[10:]])
        self.assertEqual(status, 200, answer[:200])
        self.assertEqual(json.loads(answer)["id"], 7)

    def test_unsupported_protocol_version_header_returns_400(self):
        """An unsupported MCP-Protocol-Version header is rejected with 400."""
        response = self.url_open(
            "/mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "MCP-Protocol-Version": "1999-01-01",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Unsupported MCP-Protocol-Version", response.text)

    def test_supported_protocol_version_header_passes(self):
        """A negotiated (supported) MCP-Protocol-Version header is accepted."""
        response = self.url_open(
            "/mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "MCP-Protocol-Version": PREFERRED_PROTOCOL_VERSION,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], {})

    def test_cors_preflight_success_echoes_origin_and_headers(self):
        """An allowlisted OPTIONS preflight returns 204 with the CORS headers.

        The success path must echo the validated Origin, mark the response
        Vary: Origin, and advertise MCP-Protocol-Version as an allowed request
        header so browser clients can send it on the real POST.
        """
        self._set_allowed_origins("https://app.example.com")
        response = self.opener.options(
            self.base_url() + "/mcp",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization, MCP-Protocol-Version",
            },
            timeout=12,
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Origin"),
            "https://app.example.com",
        )
        self.assertIn("Origin", response.headers.get("Vary", ""))
        self.assertIn(
            "MCP-Protocol-Version",
            response.headers.get("Access-Control-Allow-Headers", ""),
        )

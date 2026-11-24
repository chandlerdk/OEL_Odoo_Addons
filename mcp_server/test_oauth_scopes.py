"""Tests for OAuth scope enforcement (``mcp:read`` / ``mcp:write``) at /mcp.

The read/write gate is keyed on the granted OAuth scope. The full
browser-client flow is driven over real HTTP
(authorize -> consent -> token) exactly as ``test_oauth.py`` does; the consent
POST is where the ``grant_write`` checkbox decides ``mcp:read`` vs ``mcp:write``.
Over the resulting bearer we drive ``tools/list`` + ``tools/call`` and assert:

* consent ``grant_write`` UNCHECKED -> token scope ``mcp:read``: write tools are
  hidden from ``tools/list`` and a write ``tools/call`` is refused with a
  read-only ``isError`` result, while a read tool still runs;
* consent ``grant_write`` CHECKED -> token scope ``mcp:write``: a write tool
  executes;
* a client that requests exactly ``scope=mcp:read`` gets a consent page with no
  write checkbox and a read-only token;
* a legacy token (empty / ``mcp`` scope, crafted directly) keeps full write
  access (back-compat);
* refresh-token rotation preserves the granted scope;
* API-key auth is ungated: write tools stay listed and callable.

Reuses ``test_oauth.py``'s PKCE + hashing helpers and mirrors its HttpCase +
``url_open`` harness so the test-cursor cookie travels with every request.
"""

import re
import secrets
import time
from datetime import timedelta
from urllib.parse import parse_qsl, urlsplit

from odoo import fields
from odoo.tests import common, tagged

from ..controllers import mcp, oauth_server, rate_limiting, utils
from .test_helpers import (
    UrlOpenCompatMixin,
    create_test_user,
    grant_mcp_access,
    users_groups_field,
)
from .test_oauth import _code_challenge, _sha256_hex


@tagged("much_unit", "post_install", "-at_install")
class TestOAuthScopes(UrlOpenCompatMixin, common.HttpCase):
    """OAuth ``mcp:read`` / ``mcp:write`` scope grant + request-time enforcement."""

    def setUp(self):
        super().setUp()
        utils.clear_mcp_caches()
        # Module-level limiters survive the per-test rollback; reset them so a
        # stale count from a sibling test cannot trip the cap here.
        rate_limiting._api_limiter.clear()
        oauth_server._dcr_limiter.clear()
        mcp._audit_write_limiter.clear()

        unique_id = str(int(time.time() * 1000))[-6:]
        self.login = f"mcp_scope_user_{unique_id}"
        self.password = "scope_pw"  # nosec B105 - test fixture credential

        # Internal user WITH Contact Creation so create_record on res.partner can
        # actually succeed once a write scope is granted (the bare internal group
        # is read-only on res.partner) -- the scope gate, not an ACL, must be what
        # blocks writes in the read-only cases.
        groups_field = users_groups_field(self.env)
        self.user = create_test_user(
            self.env,
            "MCP Scope User",
            self.login,
            password=self.password,
            email=f"mcp_scope_{unique_id}@example.com",
            **{
                groups_field: [
                    (
                        6,
                        0,
                        [
                            self.env.ref("base.group_user").id,
                            self.env.ref("base.group_partner_manager").id,
                        ],
                    )
                ]
            },
        )
        grant_mcp_access(self.user)

        # Audience the AS derives from the request host; a token's RFC 8707
        # audience must equal this to be accepted at /mcp.
        self.resource = self.base_url() + "/mcp"
        self.redirect_uri = "http://127.0.0.1:8765/callback"

        params = self.env["ir.config_parameter"].sudo()
        params.set_param("mcp_server.enabled", "True")
        params.set_param("mcp_server.enable_oauth", "True")
        # res.partner fully CRUD-enabled via MCP so the create tool reaches the
        # ORM once the scope gate allows it (and stays gated when it doesn't).
        self._enable_model(
            "base.model_res_partner",
            allow_read=True,
            allow_create=True,
            allow_write=True,
            allow_unlink=True,
        )
        utils.clear_mcp_caches()

        self.client_id = self._register_client()["client_id"]

        # A resource-owner session for the authorize/consent step.
        self.authenticate(self.login, self.password)

    # ------------------------------------------------------------------
    # Fixture helpers
    # ------------------------------------------------------------------
    def _enable_model(self, model_xmlid, **perms):
        """Find-or-create an ``mcp.enabled.model`` row for ``model_xmlid``."""
        model_id = self.env.ref(model_xmlid).id
        record = (
            self.env["mcp.enabled.model"]
            .sudo()
            .with_context(active_test=False)
            .search([("model_id", "=", model_id)], limit=1)
        )
        vals = {"active": True, **perms}
        if record:
            record.write(vals)
        else:
            record = (
                self.env["mcp.enabled.model"]
                .sudo()
                .create({"model_id": model_id, **vals})
            )
        return record

    def _register_client(self, redirect_uris=None, scope="mcp mcp:read mcp:write"):
        """Register a public PKCE client via RFC 7591 DCR; return the JSON body.

        The client defaults to being registered for all three advertised scopes
        (``mcp``/``mcp:read``/``mcp:write``). This matters: at token generation
        the granted scope is re-narrowed against the client's registered scope
        (``client.get_allowed_scope``). Base Authlib does opaque string-subset
        matching -- under which a client registered for only ``mcp`` would have a
        granted ``mcp:read``/``mcp:write`` stripped to empty -- but this module
        overrides ``get_allowed_scope`` to be hierarchy-aware (``mcp`` expands to
        cover ``mcp:read``/``mcp:write``). Registering all three keeps most tests
        independent of that override; ``scope`` is a parameter so a test can
        register a coarser client (e.g. exactly ``"mcp"``, as Claude.ai does) and
        pin that the issued token scope is still the consent-granted one, not
        ``""``.
        """
        body = {
            "redirect_uris": redirect_uris or [self.redirect_uri],
            "client_name": "Scope Test Client",
            "scope": scope,
        }
        response = self.url_open("/mcp/oauth/register", json=body)
        self.assertIn(response.status_code, (200, 201), response.text[:500])
        return response.json()

    # ------------------------------------------------------------------
    # Flow helpers (mirrors test_oauth.py, plus the grant_write consent choice)
    # ------------------------------------------------------------------
    def _authorize_params(self, *, challenge, scope="mcp", state="state-xyz"):
        """Assemble the authorization-request parameters."""
        return {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": scope,
            "state": state,
            "resource": self.resource,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }

    def _get_authorization_code(self, params, *, grant_write=False):
        """Render consent, approve it (optionally checking ``grant_write``), and
        return the issued code."""
        get_resp = self.url_open("/mcp/oauth/authorize", params=params)
        self.assertEqual(get_resp.status_code, 200, get_resp.text[:500])
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', get_resp.text)
        self.assertIsNotNone(match, "consent page must embed a CSRF token")

        post_data = dict(params)
        post_data["csrf_token"] = match.group(1)
        post_data["decision"] = "allow"
        if grant_write:
            # Simulate the (default-checked) consent checkbox being submitted.
            post_data["grant_write"] = "on"
        post_resp = self.url_open(
            "/mcp/oauth/authorize", data=post_data, allow_redirects=False
        )
        self.assertEqual(post_resp.status_code, 302, post_resp.text[:500])
        query = dict(parse_qsl(urlsplit(post_resp.headers["Location"]).query))
        self.assertIn("code", query, post_resp.headers.get("Location"))
        return query["code"]

    def _exchange_code(self, code, verifier):
        """POST the token endpoint to swap a code for tokens; return the response."""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "code_verifier": verifier,
            "resource": self.resource,
        }
        return self.url_open("/mcp/oauth/token", data=data, allow_redirects=False)

    def _refresh(self, refresh_token):
        """POST the token endpoint to rotate a refresh token; return the response."""
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
        }
        return self.url_open("/mcp/oauth/token", data=data, allow_redirects=False)

    def _run_flow(self, *, scope="mcp", grant_write=False):
        """Run the full authorize->consent->token flow; return the token response
        JSON (access + refresh)."""
        verifier = secrets.token_urlsafe(48)
        code = self._get_authorization_code(
            self._authorize_params(challenge=_code_challenge(verifier), scope=scope),
            grant_write=grant_write,
        )
        response = self._exchange_code(code, verifier)
        self.assertEqual(response.status_code, 200, response.text[:500])
        return response.json()

    def _token_row(self, access_token):
        """Return the persisted ``mcp.oauth.token`` row for ``access_token``."""
        return (
            self.env["mcp.oauth.token"]
            .sudo()
            .search([("access_token_hash", "=", _sha256_hex(access_token))], limit=1)
        )

    # ------------------------------------------------------------------
    # RPC helpers
    # ------------------------------------------------------------------
    def _rpc(self, token, method, params=None):
        """POST a JSON-RPC ``method`` to /mcp with a bearer ``token``."""
        body = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            body["params"] = params
        headers = {"Authorization": f"Bearer {token}"}
        return self.url_open("/mcp", json=body, headers=headers)

    def _list_tool_names(self, token):
        """Return the tool names advertised by ``tools/list`` for ``token``."""
        response = self._rpc(token, "tools/list")
        self.assertEqual(response.status_code, 200, response.text[:500])
        payload = response.json()
        self.assertNotIn("error", payload, msg=payload.get("error"))
        return [tool["name"] for tool in payload["result"]["tools"]]

    def _call_tool(self, token, name, arguments=None):
        """Invoke ``tools/call`` over ``token`` and return the tool-result dict."""
        response = self._rpc(
            token, "tools/call", {"name": name, "arguments": arguments or {}}
        )
        self.assertEqual(response.status_code, 200, response.text[:500])
        payload = response.json()
        self.assertNotIn("error", payload, msg=payload.get("error"))
        return payload["result"]

    # ------------------------------------------------------------------
    # grant_write unchecked -> mcp:read (read-only session)
    # ------------------------------------------------------------------
    def test_readonly_scope_hides_and_blocks_write_but_allows_read(self):
        """Consent with write unchecked yields mcp:read: no write tool, reads OK."""
        access_token = self._run_flow(grant_write=False)["access_token"]
        self.assertEqual(self._token_row(access_token).scope, "mcp:read")

        # tools/list omits EVERY write builtin for a read-only session; a
        # regression on any one tool's readOnlyHint annotation (which the
        # gate keys on) would otherwise re-expose it and go unnoticed.
        names = self._list_tool_names(access_token)
        for write_tool in (
            "create_record",
            "update_record",
            "delete_record",
            "call_model_method",
            "post_message",
            "create_records",
            "update_records",
            "upload_attachment",
        ):
            self.assertNotIn(write_tool, names, msg=names)
        self.assertIn("search_records", names)

        # A write tools/call is refused with a read-only isError result -- the
        # batch write tools included (the expanded write surface).
        write_calls = {
            "create_record": {
                "model": "res.partner",
                "values": {"name": "Read-only create"},
            },
            "create_records": {
                "model": "res.partner",
                "records": [{"name": "Read-only batch"}],
            },
            "update_records": {
                "model": "res.partner",
                "record_ids": [1],
                "values": {"name": "Read-only batch"},
            },
        }
        for tool_name, arguments in write_calls.items():
            with self.subTest(tool_name):
                denied = self._call_tool(access_token, tool_name, arguments)
                self.assertTrue(denied["isError"], msg=denied)
                self.assertIn("read-only", denied["content"][0]["text"].lower())
                self.assertNotIn("Traceback", denied["content"][0]["text"])

        # A read tool still runs under the same read-only token.
        read_ok = self._call_tool(
            access_token, "search_records", {"model": "res.partner", "limit": 1}
        )
        self.assertFalse(read_ok["isError"], msg=read_ok)

    # ------------------------------------------------------------------
    # Resources are read-only by nature and NOT scope-gated (retained under read)
    # ------------------------------------------------------------------
    def test_resources_not_gated_by_read_scope(self):
        """A read-only (mcp:read) session keeps full resource access.

        Resources only materialise odoo:// URIs (read-only by nature), so the
        read/write scope gate does NOT apply to them -- a comment-only invariant
        in controllers/mcp.py. Over an mcp:read token, resources/templates/list
        must return its templates with no read-only denial and no JSON-RPC error,
        proving read-only sessions retain resource access.
        """
        access_token = self._run_flow(grant_write=False)["access_token"]
        self.assertEqual(self._token_row(access_token).scope, "mcp:read")

        response = self._rpc(access_token, "resources/templates/list")
        self.assertEqual(response.status_code, 200, response.text[:500])
        payload = response.json()
        self.assertNotIn("error", payload, msg=payload.get("error"))
        self.assertIn("resourceTemplates", payload["result"])

    # ------------------------------------------------------------------
    # A read-only-scope write denial is audited as permission_denied
    # ------------------------------------------------------------------
    def test_readonly_scope_write_denial_writes_permission_denied_row(self):
        """A denied write tools/call under mcp:read audits a permission_denied row.

        The read-only-scope gate returns an isError result (it does not raise),
        so without a distinct audit the shared ``finally`` would log it as a generic
        E500 -- indistinguishable from an internal failure. It must instead write an
        ``mcp.log`` row with ``event_type='permission_denied'`` for the denied tool.
        """
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.enable_logging", "True"
        )
        access_token = self._run_flow(grant_write=False)["access_token"]

        denied = self._call_tool(
            access_token,
            "create_record",
            {"model": "res.partner", "values": {"name": "Read-only create audit"}},
        )
        self.assertTrue(denied["isError"], msg=denied)

        self.env.invalidate_all()
        rows = (
            self.env["mcp.log"]
            .sudo()
            .search(
                [
                    ("event_type", "=", "permission_denied"),
                    ("operation", "=", "create"),
                    ("model_name", "=", "res.partner"),
                    ("user_id", "=", self.user.id),
                    ("endpoint", "=", "/mcp"),
                ]
            )
        )
        self.assertTrue(
            rows, "a read-only-scope write denial must audit a permission_denied row"
        )
        # The same denial must NOT also surface as a generic E500 error row.
        e500 = (
            self.env["mcp.log"]
            .sudo()
            .search(
                [
                    ("event_type", "=", "error"),
                    ("error_code", "=", "E500"),
                    ("operation", "=", "create"),
                    ("user_id", "=", self.user.id),
                ]
            )
        )
        self.assertFalse(
            e500, "the denial must not be logged as a generic E500 error row"
        )

    # ------------------------------------------------------------------
    # grant_write checked -> mcp:write (write allowed)
    # ------------------------------------------------------------------
    def test_write_scope_allows_write_tool(self):
        """Consent with write checked yields mcp:write: a write tool executes."""
        access_token = self._run_flow(grant_write=True)["access_token"]
        self.assertEqual(self._token_row(access_token).scope, "mcp:write")

        names = self._list_tool_names(access_token)
        self.assertIn("create_record", names)

        created = self._call_tool(
            access_token,
            "create_record",
            {"model": "res.partner", "values": {"name": "Write scope create"}},
        )
        self.assertFalse(created["isError"], msg=created)
        new_id = created["structuredContent"]["record"]["id"]
        self.assertTrue(new_id)
        self.env.invalidate_all()
        self.assertTrue(self.env["res.partner"].browse(new_id).exists())

    # ------------------------------------------------------------------
    # Client requesting scope=mcp:read -> no write checkbox, read-only token
    # ------------------------------------------------------------------
    def test_client_requesting_read_scope_has_no_write_checkbox(self):
        """A client that asks for exactly mcp:read gets a read-only consent + token."""
        verifier = secrets.token_urlsafe(48)
        params = self._authorize_params(
            challenge=_code_challenge(verifier), scope="mcp:read"
        )

        consent = self.url_open("/mcp/oauth/authorize", params=params)
        self.assertEqual(consent.status_code, 200, consent.text[:500])
        # The write checkbox is not rendered when the client requested mcp:read.
        self.assertNotIn('name="grant_write"', consent.text)
        self.assertIn("read-only", consent.text.lower())

        # Completing the flow yields an mcp:read token regardless of any POSTed
        # grant_write (the client-requested read scope pins it).
        code = self._get_authorization_code(params, grant_write=True)
        response = self._exchange_code(code, verifier)
        self.assertEqual(response.status_code, 200, response.text[:500])
        access_token = response.json()["access_token"]
        self.assertEqual(self._token_row(access_token).scope, "mcp:read")

    # ------------------------------------------------------------------
    # Legacy tokens (empty / mcp scope) keep full write access
    # ------------------------------------------------------------------
    def test_legacy_scope_token_allows_write(self):
        """A token with empty or 'mcp' scope (crafted directly) keeps write access."""
        client = (
            self.env["mcp.oauth.client"]
            .sudo()
            .create(
                {
                    "client_id": f"legacy-client-{int(time.time() * 1000)}",
                    "redirect_uris": self.redirect_uri,
                }
            )
        )

        def _mint(scope):
            raw_token = secrets.token_urlsafe(48)
            self.env["mcp.oauth.token"].sudo().create(
                {
                    "access_token_hash": _sha256_hex(raw_token),
                    "client": client.id,
                    "user_id": self.user.id,
                    "scope": scope,
                    "audience": self.resource,
                    "access_expires_at": fields.Datetime.now() + timedelta(hours=1),
                }
            )
            return raw_token

        # Empty scope == pre-scopes legacy token -> full access.
        empty_scope_token = _mint("")
        self.assertIn("create_record", self._list_tool_names(empty_scope_token))
        created = self._call_tool(
            empty_scope_token,
            "create_record",
            {"model": "res.partner", "values": {"name": "Legacy empty-scope"}},
        )
        self.assertFalse(created["isError"], msg=created)

        # The full-access legacy 'mcp' scope is likewise ungated for writes.
        mcp_scope_token = _mint("mcp")
        created2 = self._call_tool(
            mcp_scope_token,
            "create_record",
            {"model": "res.partner", "values": {"name": "Legacy mcp-scope"}},
        )
        self.assertFalse(created2["isError"], msg=created2)

    # ------------------------------------------------------------------
    # Refresh rotation preserves the granted scope
    # ------------------------------------------------------------------
    def test_refresh_rotation_preserves_scope(self):
        """Rotating a refresh token keeps the granted mcp:read scope on the new
        access token."""
        tokens = self._run_flow(grant_write=False)
        access1, refresh1 = tokens["access_token"], tokens["refresh_token"]
        self.assertEqual(self._token_row(access1).scope, "mcp:read")

        rotated = self._refresh(refresh1)
        self.assertEqual(rotated.status_code, 200, rotated.text[:500])
        access2 = rotated.json()["access_token"]
        self.assertNotEqual(access2, access1)
        self.assertEqual(self._token_row(access2).scope, "mcp:read")

    # ------------------------------------------------------------------
    # Client registered for only "mcp": a read-only consent must NOT fail open
    # ------------------------------------------------------------------
    def test_mcp_only_client_readonly_consent_yields_read_scope_not_empty(self):
        """A client registered scope='mcp' still gets an mcp:read token (not '').

        Regression for the fail-open scope hole. Base Authlib's
        ``client.get_allowed_scope`` does opaque string-subset matching, which
        would narrow the consent-granted ``mcp:read`` to ``""`` against a client
        registered only for ``mcp`` (``mcp:read`` is not a subset of ``{mcp}``);
        a persisted ``""`` scope reads as legacy full access in ``_write_allowed``,
        so unchecking write at consent would silently hand out a WRITE-capable
        token. Two defences now close this: ``get_allowed_scope`` is overridden to
        expand ``mcp`` to cover ``mcp:read``/``mcp:write``, and ``_save_token``
        sources the scope authoritatively from the authorization code -- so the
        persisted scope is exactly ``mcp:read``.
        """
        # Register a client whose stored scope is EXACTLY "mcp" (as Claude.ai
        # registers), replacing the default all-three-scopes client for this test.
        self.client_id = self._register_client(scope="mcp")["client_id"]

        tokens = self._run_flow(grant_write=False)
        access_token, refresh_token = tokens["access_token"], tokens["refresh_token"]

        # The hole: without the fix this is "" (full access), not "mcp:read".
        self.assertEqual(self._token_row(access_token).scope, "mcp:read")

        # tools/list must omit write tools, and a write call must be blocked --
        # i.e. the read-only gate actually engages for this token.
        self.assertNotIn("create_record", self._list_tool_names(access_token))
        denied = self._call_tool(
            access_token,
            "create_record",
            {"model": "res.partner", "values": {"name": "mcp-only readonly create"}},
        )
        self.assertTrue(denied["isError"], msg=denied)
        self.assertIn("read-only", denied["content"][0]["text"].lower())

        # Refresh-rotation must preserve mcp:read (pins the refresh path of the
        # fix, which sources scope from the rotated-from token) and keep blocking
        # write -- the refresh grant also runs through get_allowed_scope narrowing.
        rotated = self._refresh(refresh_token)
        self.assertEqual(rotated.status_code, 200, rotated.text[:500])
        access2 = rotated.json()["access_token"]
        self.assertEqual(self._token_row(access2).scope, "mcp:read")
        denied2 = self._call_tool(
            access2,
            "create_record",
            {"model": "res.partner", "values": {"name": "mcp-only rotated create"}},
        )
        self.assertTrue(denied2["isError"], msg=denied2)

    # ------------------------------------------------------------------
    # A read-only-registered client cannot self-upscope to write
    # ------------------------------------------------------------------
    def test_readonly_registered_client_cannot_self_upscope_to_write(self):
        """A client registered scope='mcp:read' gets mcp:read even when it
        requests and consents write -- registration bounds the grant.

        The client requests scope='mcp' and POSTs grant_write; the granted (and
        thus persisted code + token) scope must never exceed the client's
        read-only registration.
        """
        self.client_id = self._register_client(scope="mcp:read")["client_id"]

        verifier = secrets.token_urlsafe(48)
        params = self._authorize_params(
            challenge=_code_challenge(verifier), scope="mcp"
        )

        # The consent page must not offer write for a read-only-registered client.
        consent = self.url_open("/mcp/oauth/authorize", params=params)
        self.assertEqual(consent.status_code, 200, consent.text[:500])
        self.assertNotIn('name="grant_write"', consent.text)

        # Even POSTing grant_write=on yields an mcp:read token (bounded by
        # the client's registration, not by what the user checked).
        code = self._get_authorization_code(params, grant_write=True)
        response = self._exchange_code(code, verifier)
        self.assertEqual(response.status_code, 200, response.text[:500])
        access_token = response.json()["access_token"]
        self.assertEqual(self._token_row(access_token).scope, "mcp:read")

        # The read-only gate actually engages: write tool hidden and blocked.
        self.assertNotIn("create_record", self._list_tool_names(access_token))
        denied = self._call_tool(
            access_token,
            "create_record",
            {"model": "res.partner", "values": {"name": "read-only registered"}},
        )
        self.assertTrue(denied["isError"], msg=denied)
        self.assertIn("read-only", denied["content"][0]["text"].lower())

    # ------------------------------------------------------------------
    # A client registered exactly "mcp" is unaffected (write consent -> write)
    # ------------------------------------------------------------------
    def test_mcp_only_client_write_consent_yields_write_scope(self):
        """A client registered scope='mcp' (the common case) still gets a write
        token when the user consents to write -- the fix must not restrict it."""
        self.client_id = self._register_client(scope="mcp")["client_id"]

        access_token = self._run_flow(grant_write=True)["access_token"]
        self.assertEqual(self._token_row(access_token).scope, "mcp:write")

        self.assertIn("create_record", self._list_tool_names(access_token))
        created = self._call_tool(
            access_token,
            "create_record",
            {"model": "res.partner", "values": {"name": "mcp-only write create"}},
        )
        self.assertFalse(created["isError"], msg=created)

    # ------------------------------------------------------------------
    # Unknown scope rejected at /authorize (invalid_scope)
    # ------------------------------------------------------------------
    def test_unknown_scope_rejected_at_authorize(self):
        """An /authorize request with an unknown scope is rejected (invalid_scope).

        The authorization server pins scopes_supported = SUPPORTED_SCOPES, so
        Authlib's validate_requested_scope refuses a scope outside the advertised
        set at consent time -- the resource owner never sees a consent page for it.
        A supported scope still renders consent, proving the flow is not broken.
        """
        verifier = secrets.token_urlsafe(48)

        # An out-of-set scope: rejected with the standalone authorize-error page
        # (HTTP 400, never redirected back to the client), not the consent screen.
        bogus = self._authorize_params(
            challenge=_code_challenge(verifier), scope="bogus"
        )
        resp = self.url_open("/mcp/oauth/authorize", params=bogus)
        self.assertEqual(resp.status_code, 400, resp.text[:500])
        self.assertIn("Authorization request rejected", resp.text)
        self.assertNotIn('name="csrf_token"', resp.text)

        # A supported scope still renders the consent form (flow intact).
        ok = self._authorize_params(
            challenge=_code_challenge(verifier), scope="mcp:read"
        )
        consent = self.url_open("/mcp/oauth/authorize", params=ok)
        self.assertEqual(consent.status_code, 200, consent.text[:500])
        self.assertIn('name="csrf_token"', consent.text)

    # ------------------------------------------------------------------
    # API-key auth is ungated
    # ------------------------------------------------------------------
    def test_api_key_auth_is_ungated(self):
        """An API key stashes no scope: write tools stay listed and callable."""
        api_key = self.env(user=self.user)["res.users.apikeys"]._generate(
            "rpc", "Scope Test Key"
        )
        self.assertIn("create_record", self._list_tool_names(api_key))
        created = self._call_tool(
            api_key,
            "create_record",
            {"model": "res.partner", "values": {"name": "API key create"}},
        )
        self.assertFalse(created["isError"], msg=created)

"""Tests for the OAuth 2.1 authorization server fronting ``POST /mcp``.

Drives the full browser-client flow over real HTTP against the test server:
dynamic client registration -> Odoo-session authorize/consent -> single-use
authorization code -> PKCE-protected token exchange -> opaque bearer token used
on the native MCP endpoint. Alongside the happy path it pins the security
profile demanded by the spec: S256-only PKCE (downgrade-proof), single-use codes
with reuse-triggered family revocation, exact redirect-URI matching, RFC 8707
audience binding, and admin revocation.

Mirrors the HttpCase + ``_generate("rpc", ...)`` patterns of
``test_mcp_protocol.py`` / ``test_authentication.py``; every request rides
``self.url_open`` so the HttpCase test-cursor cookie travels with it.
"""

import base64
import hashlib
import re
import secrets
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qsl, urlsplit

from psycopg2 import IntegrityError

from odoo import fields
from odoo.tests import common, tagged
from odoo.tools import mute_logger

from ..controllers import oauth_server, rate_limiting, utils
from .test_helpers import (
    UrlOpenCompatMixin,
    create_test_user,
    grant_mcp_access,
    post_chunked,
)


def _code_challenge(verifier):
    """Return the base64url-no-pad SHA-256 challenge for a PKCE ``verifier``."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _sha256_hex(value):
    """Hex SHA-256 digest, matching how the oauth.* models hash credentials."""
    return hashlib.sha256(value.encode()).hexdigest()


@tagged("much_unit", "post_install", "-at_install")
class TestOAuth(UrlOpenCompatMixin, common.HttpCase):
    """OAuth 2.1 auth-code + PKCE flow and security assertions."""

    def setUp(self):
        super().setUp()
        utils.clear_mcp_caches()
        # Reset the shared in-memory limiters; they're module-level and survive
        # the per-test rollback, so stale registrations would otherwise leak and
        # trip the cap.
        rate_limiting._api_limiter.clear()
        oauth_server._dcr_limiter.clear()

        unique_id = str(int(time.time() * 1000))[-6:]
        self.login = f"mcp_oauth_user_{unique_id}"
        self.password = "oauth_pw"  # nosec B105 - test fixture credential
        self.user = create_test_user(
            self.env,
            "MCP OAuth User",
            self.login,
            password=self.password,
            email=f"mcp_oauth_{unique_id}@example.com",
        )
        grant_mcp_access(self.user)

        # Resource identifier the AS derives from the request host; the token's
        # RFC 8707 audience must equal this to be accepted at /mcp.
        self.resource = self.base_url() + "/mcp"
        self.redirect_uri = "http://127.0.0.1:8765/callback"

        # Enable MCP globally and expose res.partner (read) for the tool call.
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("mcp_server.enabled", "True")
        # OAuth is on by default; set it explicitly here so the suite is
        # independent of the global default, then clear the caches that gate the
        # endpoints.
        params.set_param("mcp_server.enable_oauth", "True")
        self._enable_model("base.model_res_partner", allow_read=True)
        utils.clear_mcp_caches()

        # A registered public client shared by the flow tests.
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

    def _register_client(self, redirect_uris=None):
        """Register a public PKCE client via RFC 7591 DCR; return the JSON body."""
        body = {
            "redirect_uris": redirect_uris or [self.redirect_uri],
            "client_name": "Test MCP Client",
            "scope": "mcp",
        }
        response = self.url_open("/mcp/oauth/register", json=body)
        self.assertIn(response.status_code, (200, 201), response.text[:500])
        return response.json()

    # ------------------------------------------------------------------
    # Flow helpers
    # ------------------------------------------------------------------
    def _authorize_params(
        self,
        *,
        challenge,
        method="S256",
        redirect_uri=None,
        resource=None,
        scope="mcp",
        state="state-xyz",
        client_id=None,
    ):
        """Assemble the authorization-request parameters (omitting any Nones)."""
        params = {
            "response_type": "code",
            "client_id": client_id or self.client_id,
            "redirect_uri": redirect_uri or self.redirect_uri,
            "scope": scope,
            "state": state,
            "resource": self.resource if resource is None else resource,
        }
        if challenge is not None:
            params["code_challenge"] = challenge
        if method is not None:
            params["code_challenge_method"] = method
        return params

    def _get_authorization_code(self, params):
        """Render consent, approve it with CSRF, and return the issued code."""
        get_resp = self.url_open("/mcp/oauth/authorize", params=params)
        self.assertEqual(get_resp.status_code, 200, get_resp.text[:500])
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', get_resp.text)
        self.assertIsNotNone(match, "consent page must embed a CSRF token")

        post_data = dict(params)
        post_data["csrf_token"] = match.group(1)
        post_data["decision"] = "allow"
        post_resp = self.url_open(
            "/mcp/oauth/authorize", data=post_data, allow_redirects=False
        )
        self.assertEqual(post_resp.status_code, 302, post_resp.text[:500])
        query = dict(parse_qsl(urlsplit(post_resp.headers["Location"]).query))
        self.assertIn("code", query, post_resp.headers.get("Location"))
        self.assertEqual(query.get("state"), params["state"])
        # RFC 9207: the issuer identifier rides every authorization redirect.
        self.assertEqual(
            (query.get("iss") or "").rstrip("/"), self.base_url().rstrip("/")
        )
        return query["code"]

    def _exchange_code(
        self, code, verifier, *, redirect_uri=None, resource=None, omit_resource=False
    ):
        """POST the token endpoint to swap a code for tokens; return the response."""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri or self.redirect_uri,
            "client_id": self.client_id,
            "code_verifier": verifier,
        }
        if not omit_resource:
            data["resource"] = resource or self.resource
        return self.url_open("/mcp/oauth/token", data=data, allow_redirects=False)

    def _refresh(self, refresh_token):
        """POST the token endpoint to rotate a refresh token; return the response."""
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
        }
        return self.url_open("/mcp/oauth/token", data=data, allow_redirects=False)

    def _issue_access_token(self):
        """Run the full flow once and return the opaque access token."""
        verifier = secrets.token_urlsafe(48)
        code = self._get_authorization_code(
            self._authorize_params(challenge=_code_challenge(verifier))
        )
        response = self._exchange_code(code, verifier)
        self.assertEqual(response.status_code, 200, response.text[:500])
        return response.json()["access_token"]

    def _rpc(self, access_token, method, params=None):
        """POST a JSON-RPC ``method`` to /mcp with an OAuth bearer token."""
        body = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            body["params"] = params
        headers = {"Authorization": f"Bearer {access_token}"}
        return self.url_open("/mcp", json=body, headers=headers)

    # ------------------------------------------------------------------
    # CORS for browser-based clients
    # ------------------------------------------------------------------
    def test_discovery_and_registration_allow_cross_origin_calls(self):
        """Discovery, register and token answer cross-origin, preflight included."""
        origin = {"Origin": "https://client.example"}
        for path in (
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
            "/.well-known/oauth-authorization-server",
        ):
            with self.subTest(path=path):
                response = self.url_open(path, headers=origin)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.headers.get("Access-Control-Allow-Origin"), "*"
                )
        for path in ("/mcp/oauth/register", "/mcp/oauth/token"):
            with self.subTest(preflight=path):
                response = self.opener.options(
                    self.base_url() + path,
                    headers={
                        **origin,
                        "Access-Control-Request-Method": "POST",
                        "Access-Control-Request-Headers": "Content-Type, Authorization",
                    },
                    timeout=12,
                )
                self.assertEqual(response.status_code, 204)
                self.assertEqual(
                    response.headers.get("Access-Control-Allow-Origin"), "*"
                )
                self.assertIn(
                    "POST", response.headers.get("Access-Control-Allow-Methods")
                )
        # The header rides on an error answer too, so the client can read it.
        response = self.url_open(
            "/mcp/oauth/register",
            data="{}",
            headers={**origin, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), "*")

    def test_authorize_stays_same_origin(self):
        """The authorize page is a browser navigation and gets no CORS header."""
        response = self.url_open(
            "/mcp/oauth/authorize",
            headers={"Origin": "https://client.example"},
            allow_redirects=False,
        )
        self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))

    # ------------------------------------------------------------------
    # Per-route body cap (unauthenticated ingress)
    # ------------------------------------------------------------------
    def test_oversized_oauth_body_is_refused_with_413(self):
        """token + register refuse an over-cap body before parsing it (413).

        Both are unauthenticated public endpoints; without a per-route body cap
        they would buffer/parse an arbitrarily large body before the per-IP rate
        limits run. The cap is sized for the tiny OAuth/DCR payloads, well below
        the /mcp binary-write cap.
        """
        oversized = "0" * (oauth_server.OAUTH_MAX_CONTENT_LENGTH + 1)
        token_resp = self.url_open(
            "/mcp/oauth/token",
            data=oversized,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            allow_redirects=False,
        )
        self.assertEqual(token_resp.status_code, 413, token_resp.text[:200])
        register_resp = self.url_open(
            "/mcp/oauth/register",
            data=oversized,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(register_resp.status_code, 413, register_resp.text[:200])

    def test_oversized_chunked_registration_is_refused_with_413(self):
        """A chunked DCR body has no Content-Length; over the cap it gets 413."""
        block = b"0" * 4096
        status, body = post_chunked(
            self,
            "/mcp/oauth/register",
            [block] * (oauth_server.OAUTH_MAX_CONTENT_LENGTH // len(block) + 1),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 413, body[:200])

    def test_within_cap_oauth_body_is_not_refused(self):
        """A normal-sized DCR body passes the cap (the cap only rejects floods)."""
        response = self.url_open(
            "/mcp/oauth/register",
            json={
                "redirect_uris": [self.redirect_uri],
                "client_name": "Sized Client",
                "scope": "mcp",
            },
        )
        self.assertIn(response.status_code, (200, 201), response.text[:200])

    # ------------------------------------------------------------------
    # Dynamic client registration
    # ------------------------------------------------------------------
    def test_dynamic_registration_returns_public_client(self):
        """DCR with method ``none`` issues a client_id and no client_secret."""
        response = self.url_open(
            "/mcp/oauth/register",
            json={
                "redirect_uris": [self.redirect_uri],
                "client_name": "Test MCP Client",
                "scope": "mcp",
                "token_endpoint_auth_method": "none",
            },
        )
        self.assertEqual(response.status_code, 201, response.text[:500])
        body = response.json()
        self.assertTrue(body.get("client_id"))
        self.assertNotIn("client_secret", body)
        self.assertEqual(body.get("token_endpoint_auth_method"), "none")

    def test_registration_stores_client_name_used_as_label(self):
        """DCR persists client_name; it drives display_name and the consent label."""
        body = self._register_client()
        client = (
            self.env["mcp.oauth.client"]
            .sudo()
            .search([("client_id", "=", body["client_id"])], limit=1)
        )
        self.assertEqual(client.client_name, "Test MCP Client")
        # The friendly name is the record's display name (list, token relation).
        self.assertEqual(client.display_name, "Test MCP Client")
        # ... and the consent screen shows it instead of the opaque client_id.
        consent = self.url_open(
            "/mcp/oauth/authorize",
            params=self._authorize_params(
                challenge=_code_challenge(secrets.token_urlsafe(48)),
                client_id=body["client_id"],
            ),
        )
        self.assertEqual(consent.status_code, 200, consent.text[:500])
        self.assertIn("Test MCP Client", consent.text)

    def test_unnamed_client_display_name_falls_back_to_id(self):
        """A client that registers without a name displays its client_id."""
        client = (
            self.env["mcp.oauth.client"].sudo().create({"client_id": "no-name-client"})
        )
        self.assertEqual(client.display_name, "no-name-client")

    @mute_logger("odoo.sql_db")
    def test_duplicate_client_id_violates_unique_constraint(self):
        """Two clients cannot share a client_id (UNIQUE(client_id) regression).

        The lookup (``query_client``) resolves a client by ``client_id`` via
        search(limit=1); the DB uniqueness constraint guarantees a duplicate can
        never be persisted to mask another registration.
        """
        dup_id = f"dup-client-{int(time.time() * 1000)}"
        self.env["mcp.oauth.client"].sudo().create(
            {"client_id": dup_id, "redirect_uris": self.redirect_uri}
        )
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            self.env["mcp.oauth.client"].sudo().create(
                {"client_id": dup_id, "redirect_uris": self.redirect_uri}
            )

    def test_registration_malformed_body_returns_400(self):
        """A malformed / non-JSON DCR body is a client error (400), not a 500.

        get_json() raises a Werkzeug 400/415 that must be reported as a DCR
        client error, not mapped to server_error by the generic handler.
        """
        response = self.url_open(
            "/mcp/oauth/register",
            data="not-json{",
            headers={"Content-Type": "application/json"},
            allow_redirects=False,
        )
        self.assertEqual(response.status_code, 400, response.text[:500])
        self.assertEqual(response.json().get("error"), "invalid_client_metadata")
        self.assertNotIn("client_id", response.json())

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------
    def test_authorization_code_pkce_flow_issues_usable_token(self):
        """Full auth-code + PKCE(S256) flow yields a token that drives a tool call."""
        access_token = self._issue_access_token()

        # The bearer authenticates the native MCP endpoint.
        listed = self._rpc(access_token, "tools/list")
        self.assertEqual(listed.status_code, 200)
        payload = listed.json()
        self.assertNotIn("error", payload, msg=payload.get("error"))
        self.assertTrue(payload["result"]["tools"])

        # And a real tool call succeeds under the token's bound user.
        called = self._rpc(
            access_token,
            "tools/call",
            {
                "name": "search_records",
                "arguments": {"model": "res.partner", "limit": 1},
            },
        )
        self.assertEqual(called.status_code, 200)
        result = called.json()
        self.assertNotIn("error", result, msg=result.get("error"))
        self.assertFalse(result["result"].get("isError"), msg=result["result"])

    def test_oauth_operation_row_records_client(self):
        """A tool call over OAuth attributes its model_access row to the client.

        The operation row itself carries auth_method='oauth' and the acting OAuth
        client, so the audit trail answers "which app did this" without a separate
        auth log -- and no auth_success row is written on the OAuth door.
        """
        log_model = self.env["mcp.log"].sudo()
        before = log_model.search([], order="id desc", limit=1).id or 0
        access_token = self._issue_access_token()
        called = self._rpc(
            access_token,
            "tools/call",
            {
                "name": "search_records",
                "arguments": {"model": "res.partner", "limit": 1},
            },
        )
        self.assertEqual(called.status_code, 200)
        self.assertFalse(called.json()["result"].get("isError"))

        self.env.invalidate_all()
        client = (
            self.env["mcp.oauth.client"]
            .sudo()
            .search([("client_id", "=", self.client_id)], limit=1)
        )
        row = log_model.search(
            [
                ("id", ">", before),
                ("event_type", "=", "model_access"),
                ("model_name", "=", "res.partner"),
            ],
            order="id desc",
            limit=1,
        )
        self.assertTrue(row, "expected a model_access row for the OAuth tool call")
        self.assertEqual(row.auth_method, "oauth")
        self.assertEqual(row.oauth_client_id, client)
        self.assertEqual(row.tool_name, "search_records")
        auth_success = log_model.search(
            [("id", ">", before), ("event_type", "=", "auth_success")]
        )
        self.assertFalse(
            auth_success, "the OAuth door must not write a per-request auth_success row"
        )

    def test_token_endpoint_ignores_query_string_credentials(self):
        """The token endpoint reads grant params from the POST body only.

        A ``code`` supplied in the URL query string must NOT be honored
        (RFC 6749 3.2 -- POST body params only), or secret-class values would
        leak into access/proxy logs and browser history. The same code still
        redeems normally from the body, so the happy path is intact.
        """
        verifier = secrets.token_urlsafe(48)
        code = self._get_authorization_code(
            self._authorize_params(challenge=_code_challenge(verifier))
        )
        # ``code`` lives ONLY in the query string; the body omits it.
        data = {
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "code_verifier": verifier,
            "resource": self.resource,
        }
        rejected = self.url_open(
            "/mcp/oauth/token",
            params={"code": code},
            data=data,
            allow_redirects=False,
        )
        # The query-string code is ignored -> Authlib sees no code -> 400, and
        # the code is NOT consumed (no DB lookup happens for a missing code).
        self.assertEqual(rejected.status_code, 400, rejected.text[:500])
        self.assertNotIn("access_token", rejected.text)
        # The same code still redeems when submitted in the body.
        ok = self._exchange_code(code, verifier)
        self.assertEqual(ok.status_code, 200, ok.text[:500])
        self.assertIn("access_token", ok.json())

    # ------------------------------------------------------------------
    # PKCE enforcement
    # ------------------------------------------------------------------
    def test_pkce_missing_code_challenge_is_rejected(self):
        """Authorize without a code_challenge is refused (no downgrade to no-PKCE)."""
        params = self._authorize_params(challenge=None, method=None)
        response = self.url_open("/mcp/oauth/authorize", params=params)
        self.assertEqual(response.status_code, 400)
        self.assertIn("PKCE", response.text)
        # Positive control: the same request WITH a valid S256 challenge passes.
        ok = self._authorize_params(
            challenge=_code_challenge(secrets.token_urlsafe(48))
        )
        self.assertEqual(
            self.url_open("/mcp/oauth/authorize", params=ok).status_code, 200
        )

    def test_oauth_consent_layout_avoids_website_poisoned_frontend_layout(self):
        """Consent/error pages must render via web.layout, not web.login_layout.

        web.login_layout / web.frontend_layout are extended by the website module
        (website.layout is a non-primary extension of the frontend_layout chain),
        so they require a website request context our /mcp/oauth/authorize route
        never provides: rendering them dies with QWeb "KeyError: 'website'" and a
        HTTP 500 on any instance with website installed. This is a structural
        guard against a refactor back to those layouts -- the render itself is
        exercised by the authorize tests above (200) and was verified on a
        website-installed database. Runs without website installed.
        """
        layout = self.env.ref("mcp_server.oauth_layout")
        self.assertIn('t-call="web.layout"', layout.arch)
        for banned in ("web.login_layout", "web.frontend_layout"):
            self.assertNotIn(banned, layout.arch)
        for tmpl_id in (
            "mcp_server.oauth_consent",
            "mcp_server.oauth_authorize_error",
        ):
            arch = self.env.ref(tmpl_id).arch
            self.assertIn('t-call="mcp_server.oauth_layout"', arch)
            self.assertNotIn("web.login_layout", arch)

    def test_pkce_plain_method_is_rejected(self):
        """Authorize with the weaker 'plain' method is refused (S256-only)."""
        params = self._authorize_params(challenge="a" * 43, method="plain")
        response = self.url_open("/mcp/oauth/authorize", params=params)
        self.assertEqual(response.status_code, 400)
        self.assertIn("code_challenge_method", response.text)
        # Positive control: switching the method to S256 (challenge unchanged) passes.
        ok = self._authorize_params(challenge="a" * 43, method="S256")
        self.assertEqual(
            self.url_open("/mcp/oauth/authorize", params=ok).status_code, 200
        )

    def test_pkce_wrong_code_verifier_is_rejected(self):
        """A code_verifier not matching the challenge fails the token exchange."""
        verifier = secrets.token_urlsafe(48)
        code = self._get_authorization_code(
            self._authorize_params(challenge=_code_challenge(verifier))
        )
        response = self._exchange_code(code, secrets.token_urlsafe(48))
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertNotIn("access_token", body)
        self.assertEqual(body.get("error"), "invalid_grant")

    # ------------------------------------------------------------------
    # Single-use code + reuse detection
    # ------------------------------------------------------------------
    def test_authorization_code_is_single_use_and_reuse_revokes_token(self):
        """A second exchange is rejected and revokes the token the code produced."""
        verifier = secrets.token_urlsafe(48)
        code = self._get_authorization_code(
            self._authorize_params(challenge=_code_challenge(verifier))
        )

        first = self._exchange_code(code, verifier)
        self.assertEqual(first.status_code, 200, first.text[:500])
        access_token = first.json()["access_token"]
        # The freshly minted token works before the replay.
        self.assertEqual(self._rpc(access_token, "ping").status_code, 200)

        # Replaying the spent code is rejected as invalid_grant ...
        replay = self._exchange_code(code, verifier)
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(replay.json().get("error"), "invalid_grant")

        # ... and the reuse detection has revoked the originally issued token.
        self.assertEqual(self._rpc(access_token, "ping").status_code, 401)

    # ------------------------------------------------------------------
    # Exact redirect-URI matching
    # ------------------------------------------------------------------
    def test_redirect_uri_must_match_registration(self):
        """A redirect_uri not registered for the client is refused at authorize."""
        verifier = secrets.token_urlsafe(48)
        params = self._authorize_params(
            challenge=_code_challenge(verifier),
            redirect_uri="http://127.0.0.1:9999/elsewhere",
        )
        response = self.url_open("/mcp/oauth/authorize", params=params)
        self.assertEqual(response.status_code, 400)
        self.assertIn("redirect", response.text.lower())
        # Positive control: the registered redirect_uri (only change) is accepted.
        ok = self._authorize_params(challenge=_code_challenge(verifier))
        self.assertEqual(
            self.url_open("/mcp/oauth/authorize", params=ok).status_code, 200
        )

    # ------------------------------------------------------------------
    # RFC 8707 audience binding
    # ------------------------------------------------------------------
    def test_token_audience_mismatch_is_rejected_at_rpc(self):
        """A token minted for a different audience is refused at /mcp (401)."""
        client = (
            self.env["mcp.oauth.client"]
            .sudo()
            .create(
                {
                    "client_id": f"aud-client-{int(time.time() * 1000)}",
                    "redirect_uris": self.redirect_uri,
                }
            )
        )
        raw_token = secrets.token_urlsafe(48)
        self.env["mcp.oauth.token"].sudo().create(
            {
                "access_token_hash": _sha256_hex(raw_token),
                "client": client.id,
                "user_id": self.user.id,
                "scope": "mcp",
                "audience": "https://attacker.example/mcp",
                "access_expires_at": fields.Datetime.now() + timedelta(hours=1),
            }
        )

        response = self._rpc(raw_token, "ping")
        self.assertEqual(response.status_code, 401)
        # The 401 must point browser clients at the protected-resource metadata
        # (RFC 9728 5.1), not merely advertise "Bearer".
        www = response.headers.get("WWW-Authenticate", "")
        self.assertIn("Bearer", www)
        self.assertIn("resource_metadata=", www)
        self.assertIn("/.well-known/oauth-protected-resource", www)

    def test_token_with_legacy_alias_audience_is_accepted(self):
        """A token whose audience is the legacy /mcp/rpc alias still works.

        /mcp is the canonical resource identifier, but a client configured
        against the old /mcp/rpc URL sends that as the RFC 8707 ``resource``
        parameter, so a token minted with the alias audience must keep
        authenticating on the same endpoint.
        """
        client = (
            self.env["mcp.oauth.client"]
            .sudo()
            .create(
                {
                    "client_id": f"legacy-aud-client-{int(time.time() * 1000)}",
                    "redirect_uris": self.redirect_uri,
                }
            )
        )
        raw_token = secrets.token_urlsafe(48)
        self.env["mcp.oauth.token"].sudo().create(
            {
                "access_token_hash": _sha256_hex(raw_token),
                "client": client.id,
                "user_id": self.user.id,
                "scope": "mcp",
                "audience": self.base_url().rstrip("/") + "/mcp/rpc",
                "access_expires_at": fields.Datetime.now() + timedelta(hours=1),
            }
        )

        self.assertEqual(self._rpc(raw_token, "ping").status_code, 200)

    # ------------------------------------------------------------------
    # Admin revocation
    # ------------------------------------------------------------------
    def test_admin_revoke_invalidates_token(self):
        """Revoking an issued token via the admin action rejects it at /mcp."""
        access_token = self._issue_access_token()
        self.assertEqual(self._rpc(access_token, "ping").status_code, 200)

        token = (
            self.env["mcp.oauth.token"]
            .sudo()
            .search([("access_token_hash", "=", _sha256_hex(access_token))], limit=1)
        )
        self.assertTrue(token, "issued token must be persisted")
        token.action_revoke()

        self.assertEqual(self._rpc(access_token, "ping").status_code, 401)

    def test_admin_actions_operate_on_a_multi_record_set(self):
        """Revoke / (de)activate act on the whole recordset, not one record.

        The bulk "Actions" menu entries (``action_mcp_oauth_token_revoke`` /
        ``action_mcp_oauth_client_{de,}activate``) call these object methods on the
        entire selection, so they must never become single-record only.
        """
        oauth_token = self.env["mcp.oauth.token"].sudo()
        token_a = oauth_token.search(
            [("access_token_hash", "=", _sha256_hex(self._issue_access_token()))],
            limit=1,
        )
        token_b = oauth_token.search(
            [("access_token_hash", "=", _sha256_hex(self._issue_access_token()))],
            limit=1,
        )
        tokens = token_a | token_b
        self.assertEqual(len(tokens), 2)
        self.assertFalse(any(tokens.mapped("revoked")))
        tokens.action_revoke()
        self.assertTrue(all(tokens.mapped("revoked")))

        oauth_client = self.env["mcp.oauth.client"].sudo()
        client_a = oauth_client.search(
            [("client_id", "=", self._register_client()["client_id"])], limit=1
        )
        client_b = oauth_client.search(
            [("client_id", "=", self._register_client()["client_id"])], limit=1
        )
        clients = client_a | client_b
        self.assertEqual(len(clients), 2)
        self.assertTrue(all(clients.mapped("active")))
        clients.action_deactivate()
        self.assertFalse(any(clients.mapped("active")))
        clients.action_activate()
        self.assertTrue(all(clients.mapped("active")))

    def test_bulk_confirm_wizard_applies_the_operation(self):
        """The confirmation wizard revokes / deactivates the passed selection.

        The destructive bulk actions open this wizard instead of acting
        immediately; confirming it must apply the operation to every record in
        ``active_ids``.
        """
        wizard_model = self.env["mcp.oauth.bulk.confirm.wizard"].sudo()

        token = (
            self.env["mcp.oauth.token"]
            .sudo()
            .search(
                [("access_token_hash", "=", _sha256_hex(self._issue_access_token()))],
                limit=1,
            )
        )
        self.assertFalse(token.revoked)
        wizard_model.with_context(active_ids=token.ids).create(
            {"operation": "revoke"}
        ).action_confirm()
        self.assertTrue(token.revoked)

        client = (
            self.env["mcp.oauth.client"]
            .sudo()
            .search([("client_id", "=", self.client_id)], limit=1)
        )
        self.assertTrue(client.active)
        wizard_model.with_context(active_ids=client.ids).create(
            {"operation": "deactivate"}
        ).action_confirm()
        self.assertFalse(client.active)

    # ------------------------------------------------------------------
    # Deactivated client is cut off at /mcp
    # ------------------------------------------------------------------
    def test_deactivated_client_token_is_rejected_at_rpc(self):
        """Deactivating a client invalidates its already-issued tokens at once."""
        access_token = self._issue_access_token()
        self.assertEqual(self._rpc(access_token, "ping").status_code, 200)

        # Deactivate the client the token was issued to; the still-unexpired
        # bearer must stop working immediately, not only once it expires.
        client = (
            self.env["mcp.oauth.client"]
            .sudo()
            .search([("client_id", "=", self.client_id)], limit=1)
        )
        self.assertTrue(client, "issued token's client must be persisted")
        client.active = False

        response = self._rpc(access_token, "ping")
        self.assertEqual(response.status_code, 401)
        # The 401 still points browser clients at where to obtain a fresh token.
        www = response.headers.get("WWW-Authenticate", "")
        self.assertIn("resource_metadata=", www)

    # ------------------------------------------------------------------
    # Archived (deactivated) user is cut off everywhere
    # ------------------------------------------------------------------
    def test_archived_user_token_is_rejected_at_rpc(self):
        """Archiving a user invalidates their outstanding access token at once."""
        access_token = self._issue_access_token()
        self.assertEqual(self._rpc(access_token, "ping").status_code, 200)

        # Deactivate the token's user; the still-unexpired bearer must stop working.
        self.user.sudo().active = False
        response = self._rpc(access_token, "ping")
        self.assertEqual(response.status_code, 401)
        # The 401 still points browser clients at where to obtain a fresh token.
        www = response.headers.get("WWW-Authenticate", "")
        self.assertIn("resource_metadata=", www)

    # ------------------------------------------------------------------
    # Expiry + malformed-credential doors at /mcp
    # ------------------------------------------------------------------
    def test_expired_access_token_is_rejected_at_rpc(self):
        """A time-expired access token is refused at /mcp (401).

        The sibling disqualifiers (revoked / wrong audience / inactive client or
        user) are covered above with still-unexpired tokens; this pins the expiry
        boundary. ``_get_valid_access_token`` filters ``access_expires_at > now``,
        and with a matching hash and the default ``revoked=False`` expiry is the
        sole reason the token is not found -> 401.
        """
        client = (
            self.env["mcp.oauth.client"]
            .sudo()
            .search([("client_id", "=", self.client_id)], limit=1)
        )
        raw_token = secrets.token_urlsafe(48)
        self.env["mcp.oauth.token"].sudo().create(
            {
                "access_token_hash": _sha256_hex(raw_token),
                "client": client.id,
                "user_id": self.user.id,
                "scope": "mcp",
                "access_expires_at": fields.Datetime.now() - timedelta(seconds=1),
            }
        )

        response = self._rpc(raw_token, "ping")
        self.assertEqual(response.status_code, 401)
        www = response.headers.get("WWW-Authenticate", "")
        self.assertIn("Bearer", www)
        self.assertIn("resource_metadata=", www)

    def test_malformed_authorization_header_is_rejected_at_rpc(self):
        """A non-Bearer Authorization header is refused at /mcp (401).

        Exercises ir.http's wrong-scheme branch: a presented-but-unusable
        credential (here Basic auth) is rejected with the RFC 9728 challenge and
        audited as a failed attempt. The audit row is written on an independent
        cursor that, for a *raising* route under HttpCase, rolls back with the
        request transaction, so only the 401 + challenge is asserted here.
        """
        body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        response = self.url_open(
            "/mcp", json=body, headers={"Authorization": "Basic dXNlcjpwYXNz"}
        )
        self.assertEqual(response.status_code, 401)
        www = response.headers.get("WWW-Authenticate", "")
        self.assertIn("Bearer", www)
        self.assertIn("resource_metadata=", www)
        self.assertIn("/.well-known/oauth-protected-resource", www)

    def test_archived_user_cannot_redeem_authorization_code(self):
        """An outstanding auth code cannot be redeemed once the user is archived."""
        verifier = secrets.token_urlsafe(48)
        code = self._get_authorization_code(
            self._authorize_params(challenge=_code_challenge(verifier))
        )
        self.user.sudo().active = False
        response = self._exchange_code(code, verifier)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access_token", response.json())

    def test_archived_user_cannot_rotate_refresh_token(self):
        """A valid refresh token cannot rotate once its user is archived."""
        verifier = secrets.token_urlsafe(48)
        code = self._get_authorization_code(
            self._authorize_params(challenge=_code_challenge(verifier))
        )
        first = self._exchange_code(code, verifier)
        self.assertEqual(first.status_code, 200, first.text[:500])
        refresh1 = first.json()["refresh_token"]

        self.user.sudo().active = False
        response = self._refresh(refresh1)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access_token", response.json())

    # ------------------------------------------------------------------
    # Refresh-token rotation + reuse detection
    # ------------------------------------------------------------------
    def test_refresh_rotation_and_reuse_revokes_family(self):
        """Rotation issues a new pair; replaying the old refresh kills the family."""
        verifier = secrets.token_urlsafe(48)
        code = self._get_authorization_code(
            self._authorize_params(challenge=_code_challenge(verifier))
        )
        first = self._exchange_code(code, verifier)
        self.assertEqual(first.status_code, 200, first.text[:500])
        tokens1 = first.json()
        access1, refresh1 = tokens1["access_token"], tokens1["refresh_token"]
        self.assertEqual(self._rpc(access1, "ping").status_code, 200)

        # Rotation: the refresh token yields a brand-new access + refresh pair.
        rotated = self._refresh(refresh1)
        self.assertEqual(rotated.status_code, 200, rotated.text[:500])
        tokens2 = rotated.json()
        access2, refresh2 = tokens2["access_token"], tokens2["refresh_token"]
        self.assertNotEqual(access2, access1)
        self.assertNotEqual(refresh2, refresh1)
        # The rotated access token authenticates /mcp (audience inherited).
        self.assertEqual(self._rpc(access2, "ping").status_code, 200)

        # Replaying the already-rotated refresh token is rejected ...
        replay = self._refresh(refresh1)
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(replay.json().get("error"), "invalid_grant")
        # ... and reuse detection revoked the whole family: the live descendant
        # access token and the latest refresh token are both dead now.
        self.assertEqual(self._rpc(access2, "ping").status_code, 401)
        self.assertEqual(self._refresh(refresh2).status_code, 400)

    # ------------------------------------------------------------------
    # Authorization-code reuse: precise, no over-revocation
    # ------------------------------------------------------------------
    def test_code_reuse_revokes_only_its_own_family(self):
        """A code replay revokes the family it produced, not unrelated tokens."""
        # An unrelated token (same client+user) that must survive the replay.
        survivor = self._issue_access_token()
        self.assertEqual(self._rpc(survivor, "ping").status_code, 200)

        verifier = secrets.token_urlsafe(48)
        code = self._get_authorization_code(
            self._authorize_params(challenge=_code_challenge(verifier))
        )
        issued = self._exchange_code(code, verifier)
        self.assertEqual(issued.status_code, 200, issued.text[:500])
        victim = issued.json()["access_token"]
        self.assertEqual(self._rpc(victim, "ping").status_code, 200)

        replay = self._exchange_code(code, verifier)
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(replay.json().get("error"), "invalid_grant")

        # The replayed code's own family is revoked ...
        self.assertEqual(self._rpc(victim, "ping").status_code, 401)
        # ... while the unrelated family is untouched (no over-revocation).
        self.assertEqual(self._rpc(survivor, "ping").status_code, 200)

    def test_expired_unused_code_fails_without_revocation(self):
        """An expired-but-never-redeemed code fails and revokes nothing."""
        survivor = self._issue_access_token()
        self.assertEqual(self._rpc(survivor, "ping").status_code, 200)

        verifier = secrets.token_urlsafe(48)
        code = self._get_authorization_code(
            self._authorize_params(challenge=_code_challenge(verifier))
        )
        # Force-expire the (still unused) code in place.
        code_rec = (
            self.env["mcp.oauth.authorization.code"]
            .sudo()
            .search([("code_hash", "=", _sha256_hex(code)), ("used", "=", False)])
        )
        self.assertTrue(code_rec, "unused code must be persisted before expiry")
        code_rec.expires_at = fields.Datetime.now() - timedelta(seconds=1)

        response = self._exchange_code(code, verifier)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get("error"), "invalid_grant")
        # No reuse was detected (code was never consumed): tokens survive.
        self.assertEqual(self._rpc(survivor, "ping").status_code, 200)

    def test_unverified_code_redemption_does_not_revoke(self):
        """A code presented without a verifier is rejected and revokes nothing."""
        survivor = self._issue_access_token()
        self.assertEqual(self._rpc(survivor, "ping").status_code, 200)

        verifier = secrets.token_urlsafe(48)
        code = self._get_authorization_code(
            self._authorize_params(challenge=_code_challenge(verifier))
        )
        # Redeem the (unredeemed) code with NO code_verifier.
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "resource": self.resource,
        }
        response = self.url_open("/mcp/oauth/token", data=data, allow_redirects=False)
        self.assertEqual(response.status_code, 400)
        # The verifier-free attempt must not trigger any token revocation.
        self.assertEqual(self._rpc(survivor, "ping").status_code, 200)

    def test_plain_method_code_refused_at_token_endpoint(self):
        """A stored 'plain'-method code is refused at redemption (downgrade-proof)."""
        verifier = "a" * 64  # valid plain verifier == challenge
        client = (
            self.env["mcp.oauth.client"]
            .sudo()
            .search([("client_id", "=", self.client_id)], limit=1)
        )
        raw_code = secrets.token_urlsafe(32)
        # Seed a code whose PKCE method is the weaker 'plain' -- which the
        # authorize endpoint would never issue -- and try to redeem it.
        self.env["mcp.oauth.authorization.code"].sudo().create(
            {
                "code_hash": _sha256_hex(raw_code),
                "client": client.id,
                "user_id": self.user.id,
                "redirect_uri": self.redirect_uri,
                "scope": "mcp",
                "code_challenge": verifier,
                "code_challenge_method": "plain",
                "resource": self.resource,
                "expires_at": fields.Datetime.now() + timedelta(minutes=1),
            }
        )
        response = self._exchange_code(raw_code, verifier)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access_token", response.json())

    # ------------------------------------------------------------------
    # RFC 8707 audience: bound from the code, server-derived
    # ------------------------------------------------------------------
    def test_issued_token_audience_matches_resource(self):
        """The happy-path token is audienced to the server-derived resource."""
        access_token = self._issue_access_token()
        token_rec = (
            self.env["mcp.oauth.token"]
            .sudo()
            .search([("access_token_hash", "=", _sha256_hex(access_token))], limit=1)
        )
        self.assertTrue(token_rec)
        self.assertEqual(token_rec.audience.rstrip("/"), self.resource.rstrip("/"))

    def test_audience_defaults_to_canonical_when_no_resource_bound(self):
        """No resource bound anywhere -> audience defaults to canonical (not False).

        A client that omits RFC 8707 ``resource`` at BOTH authorize and token
        time must not complete the flow into a token with a False audience, which
        fails closed at /mcp (``_mcp_audience_matches``) on every call. Since this
        AS serves a single resource, the audience defaults to its canonical URL so
        the token stays usable.
        """
        oauth2_request = SimpleNamespace(
            payload=SimpleNamespace(data={}),  # no 'resource' at the token step
            refresh_token=None,
            authorization_code=SimpleNamespace(resource=False),  # code bound none
        )
        with patch(
            "odoo.addons.mcp_server.models.oauth_token.resource_url",
            return_value="https://example.test/mcp",
        ):
            audience, _family = (
                self.env["mcp.oauth.token"]
                .sudo()
                ._resolve_audience_and_family(oauth2_request)
            )
        self.assertEqual(audience, "https://example.test/mcp")

    def test_audience_defaults_to_canonical_on_bare_token_request(self):
        """The ``else`` branch (no auth code, no refresh) also defaults audience.

        Companion to the auth-code-branch helper test above: with neither an
        authorization code nor a refreshed token in play and no ``resource`` in
        the request, ``_resolve_audience_and_family`` must still default to the
        canonical resource URL rather than ``False`` (which would fail closed at
        /mcp).
        """
        oauth2_request = SimpleNamespace(
            payload=SimpleNamespace(data={}),  # no 'resource' at the token step
            refresh_token=None,
            authorization_code=None,  # neither an auth-code nor a refresh grant
        )
        with patch(
            "odoo.addons.mcp_server.models.oauth_token.resource_url",
            return_value="https://example.test/mcp",
        ):
            audience, _family = (
                self.env["mcp.oauth.token"]
                .sudo()
                ._resolve_audience_and_family(oauth2_request)
            )
        self.assertEqual(audience, "https://example.test/mcp")

    def test_audience_defaults_to_canonical_end_to_end(self):
        """Omit resource at BOTH authorize and token time -> token still usable.

        End-to-end companion to the helper-level default tests: a client that
        binds no RFC 8707 ``resource`` anywhere must not complete the flow into a
        token that 401s on every /mcp call. The audience defaults to this
        server's canonical resource URL, so the bearer authenticates (200).
        """
        verifier = secrets.token_urlsafe(48)
        params = self._authorize_params(challenge=_code_challenge(verifier))
        del params["resource"]  # omit resource at authorize time
        code = self._get_authorization_code(params)
        # ... and omit it again at token time.
        response = self._exchange_code(code, verifier, omit_resource=True)
        self.assertEqual(response.status_code, 200, response.text[:500])
        access_token = response.json()["access_token"]

        # The token defaulted to the canonical audience -> it authenticates /mcp.
        self.assertEqual(self._rpc(access_token, "ping").status_code, 200)
        token_rec = (
            self.env["mcp.oauth.token"]
            .sudo()
            .search([("access_token_hash", "=", _sha256_hex(access_token))], limit=1)
        )
        self.assertTrue(token_rec)
        self.assertEqual(token_rec.audience.rstrip("/"), self.resource.rstrip("/"))

    def test_audience_bound_from_code_when_token_omits_resource(self):
        """Omitting resource at the token step still binds the code's audience."""
        verifier = secrets.token_urlsafe(48)
        # The authorize request carries the resource (the spec's MUST is here).
        code = self._get_authorization_code(
            self._authorize_params(challenge=_code_challenge(verifier))
        )
        # The token step omits resource entirely.
        response = self._exchange_code(code, verifier, omit_resource=True)
        self.assertEqual(response.status_code, 200, response.text[:500])
        access_token = response.json()["access_token"]
        # The token is still usable and correctly audienced.
        self.assertEqual(self._rpc(access_token, "ping").status_code, 200)
        token_rec = (
            self.env["mcp.oauth.token"]
            .sudo()
            .search([("access_token_hash", "=", _sha256_hex(access_token))], limit=1)
        )
        self.assertEqual(token_rec.audience.rstrip("/"), self.resource.rstrip("/"))

    def test_audience_trailing_slash_is_tolerated(self):
        """A resource with a trailing slash still yields a usable token."""
        verifier = secrets.token_urlsafe(48)
        resource_slash = self.resource + "/"
        code = self._get_authorization_code(
            self._authorize_params(
                challenge=_code_challenge(verifier), resource=resource_slash
            )
        )
        response = self._exchange_code(code, verifier, resource=resource_slash)
        self.assertEqual(response.status_code, 200, response.text[:500])
        access_token = response.json()["access_token"]
        self.assertEqual(self._rpc(access_token, "ping").status_code, 200)

    def test_token_resource_contradicting_code_is_rejected(self):
        """A token-step resource that contradicts the code's binding is refused."""
        verifier = secrets.token_urlsafe(48)
        code = self._get_authorization_code(
            self._authorize_params(challenge=_code_challenge(verifier))
        )
        response = self._exchange_code(
            code, verifier, resource="https://other.example/mcp"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get("error"), "invalid_request")

    def test_authorize_unknown_resource_is_rejected(self):
        """RFC 8707: an unaccepted resource indicator is refused at authorize.

        A code is never issued for a resource this AS does not serve, so the
        flow cannot complete into a token whose audience only 401s at /mcp.
        """
        verifier = secrets.token_urlsafe(48)
        params = self._authorize_params(
            challenge=_code_challenge(verifier),
            resource="https://not-this-server.example/mcp",
        )
        response = self.url_open("/mcp/oauth/authorize", params=params)
        self.assertEqual(response.status_code, 400, response.text[:300])
        # The canonical resource still renders the consent screen (200).
        ok = self._authorize_params(challenge=_code_challenge(verifier))
        self.assertEqual(
            self.url_open("/mcp/oauth/authorize", params=ok).status_code, 200
        )

    # ------------------------------------------------------------------
    # Garbage collection keeps live credentials
    # ------------------------------------------------------------------
    def test_gc_oauth_keeps_live_credentials(self):
        """GC removes only dead codes/tokens/stale clients; live rows survive."""
        now = fields.Datetime.now()
        Token = self.env["mcp.oauth.token"].sudo()
        Code = self.env["mcp.oauth.authorization.code"].sudo()
        Client = self.env["mcp.oauth.client"].sudo()

        def _client(tag):
            return Client.create(
                {
                    "client_id": f"gc-{tag}-{int(time.time() * 1000)}",
                    "redirect_uris": self.redirect_uri,
                    "created_via": "dcr",
                }
            )

        dead_client = _client("dead")
        live_client = _client("live")

        dead_token = Token.create(
            {
                "access_token_hash": _sha256_hex(f"dead-{secrets.token_hex(4)}"),
                "client": dead_client.id,
                "user_id": self.user.id,
                "audience": self.resource,
                "revoked": True,
                "access_expires_at": now - timedelta(hours=2),
                "refresh_expires_at": now - timedelta(hours=1),
            }
        )
        live_token = Token.create(
            {
                "access_token_hash": _sha256_hex(f"live-{secrets.token_hex(4)}"),
                "client": live_client.id,
                "user_id": self.user.id,
                "audience": self.resource,
                "revoked": False,
                "access_expires_at": now + timedelta(hours=1),
            }
        )
        expired_code = Code.create(
            {
                "code_hash": _sha256_hex(f"ec-{secrets.token_hex(4)}"),
                "client": dead_client.id,
                "user_id": self.user.id,
                "expires_at": now - timedelta(minutes=1),
            }
        )
        fresh_code = Code.create(
            {
                "code_hash": _sha256_hex(f"fc-{secrets.token_hex(4)}"),
                "client": live_client.id,
                "user_id": self.user.id,
                "expires_at": now + timedelta(minutes=1),
            }
        )

        # Age both DCR clients past the retention threshold (create_date is a log
        # field; set it directly with parameterized SQL).
        self.env.cr.execute(
            "UPDATE mcp_oauth_client SET create_date = %s WHERE id IN %s",
            (now - timedelta(days=40), tuple((dead_client.id, live_client.id))),
        )
        (dead_client + live_client).invalidate_recordset(["create_date"])

        Token._gc_oauth()

        # Dead rows collected.
        self.assertFalse(dead_token.exists())
        self.assertFalse(expired_code.exists())
        self.assertFalse(dead_client.exists())  # stale DCR client, no live token
        # Live rows preserved.
        self.assertTrue(live_token.exists())
        self.assertTrue(fresh_code.exists())
        self.assertTrue(live_client.exists())  # stale DCR but has a live token

    def test_gc_collects_abandoned_expired_token(self):
        """A merely-expired token (never explicitly revoked) is collected."""
        now = fields.Datetime.now()
        Token = self.env["mcp.oauth.token"].sudo()
        Code = self.env["mcp.oauth.authorization.code"].sudo()
        Client = self.env["mcp.oauth.client"].sudo()

        client = Client.create(
            {
                "client_id": f"gc-aband-{int(time.time() * 1000)}",
                "redirect_uris": self.redirect_uri,
                "created_via": "dcr",
            }
        )
        abandoned_token = Token.create(
            {
                "access_token_hash": _sha256_hex(f"aband-{secrets.token_hex(4)}"),
                "client": client.id,
                "user_id": self.user.id,
                "audience": self.resource,
                "revoked": False,
                "access_expires_at": now - timedelta(hours=2),
                "refresh_expires_at": now - timedelta(hours=1),
                "refresh_family_id": "fam-abandoned",
            }
        )
        guarding_code = Code.create(
            {
                "code_hash": _sha256_hex(f"ac-{secrets.token_hex(4)}"),
                "client": client.id,
                "user_id": self.user.id,
                "expires_at": now - timedelta(minutes=1),
                "used": True,
                "refresh_family_id": "fam-abandoned",
            }
        )

        Token._gc_oauth()

        self.assertFalse(
            abandoned_token.exists(),
            "an abandoned expired token must be garbage-collected",
        )
        self.assertFalse(
            guarding_code.exists(),
            "the code guarding a now-dead family must be collected too",
        )

    def test_gc_oauth_preserves_reuse_detection_signal(self):
        """GC keeps a redeemed code while its token family is still live.

        The used=True code is the row a later code replay matches to revoke its
        family (_detect_reuse); deleting it early would silently break replay
        containment. Codes guarding a dead/absent family are collected as spent.
        """
        now = fields.Datetime.now()
        Token = self.env["mcp.oauth.token"].sudo()
        Code = self.env["mcp.oauth.authorization.code"].sudo()
        # Fresh create_date keeps this client out of the stale-DCR sweep, so the
        # code assertions isolate the code-GC rule (no cascade interference).
        client = (
            self.env["mcp.oauth.client"]
            .sudo()
            .create(
                {
                    "client_id": f"gc-reuse-{int(time.time() * 1000)}",
                    "redirect_uris": self.redirect_uri,
                }
            )
        )

        def _code(tag, *, used, family):
            return Code.create(
                {
                    "code_hash": _sha256_hex(f"{tag}-{secrets.token_hex(4)}"),
                    "client": client.id,
                    "user_id": self.user.id,
                    "expires_at": now - timedelta(minutes=1),
                    "used": used,
                    "refresh_family_id": family,
                }
            )

        def _token(tag, *, revoked, family):
            return Token.create(
                {
                    "access_token_hash": _sha256_hex(f"{tag}-{secrets.token_hex(4)}"),
                    "client": client.id,
                    "user_id": self.user.id,
                    "revoked": revoked,
                    "access_expires_at": now + timedelta(hours=-1 if revoked else 1),
                    "refresh_family_id": family,
                }
            )

        # Family with a live (unrevoked) token -> its redeemed code must survive.
        _token("live", revoked=False, family="fam-live")
        guarded_code = _code("guarded", used=True, family="fam-live")
        # Family fully revoked -> its redeemed code guards nothing; collect it.
        _token("dead", revoked=True, family="fam-dead")
        dead_family_code = _code("deadfam", used=True, family="fam-dead")
        # Redeemed code with no family, and an expired never-redeemed code:
        # both minted/guard nothing and are collected.
        familyless_code = _code("nofam", used=True, family=False)
        unused_code = _code("unused", used=False, family=False)

        Token._gc_oauth()

        self.assertTrue(
            guarded_code.exists(),
            "redeemed code guarding a live family must survive GC",
        )
        self.assertFalse(
            dead_family_code.exists(),
            "redeemed code whose family is fully revoked is collected",
        )
        self.assertFalse(
            familyless_code.exists(), "redeemed familyless code is collected"
        )
        self.assertFalse(unused_code.exists(), "expired unused code is collected")

    # ------------------------------------------------------------------
    # Discovery metadata
    # ------------------------------------------------------------------
    def test_discovery_metadata_documents(self):
        """Both .well-known documents are reachable unauthenticated and correct."""
        base = self.base_url().rstrip("/")

        prm = self.url_open("/.well-known/oauth-protected-resource")
        self.assertEqual(prm.status_code, 200)
        prm_body = prm.json()
        self.assertEqual(prm_body["resource"].rstrip("/"), self.resource.rstrip("/"))
        self.assertEqual(prm_body["authorization_servers"], [base])
        self.assertEqual(prm_body["bearer_methods_supported"], ["header"])
        self.assertEqual(
            prm_body["scopes_supported"], ["mcp", "mcp:read", "mcp:write"]
        )

        asm = self.url_open("/.well-known/oauth-authorization-server")
        self.assertEqual(asm.status_code, 200)
        body = asm.json()
        self.assertEqual(body["code_challenge_methods_supported"], ["S256"])
        self.assertEqual(
            body["token_endpoint_auth_methods_supported"],
            ["none", "client_secret_basic", "client_secret_post"],
        )
        self.assertEqual(body["response_types_supported"], ["code"])
        self.assertEqual(
            set(body["grant_types_supported"]),
            {"authorization_code", "refresh_token"},
        )
        self.assertEqual(body["issuer"].rstrip("/"), base)
        self.assertEqual(body["authorization_endpoint"], base + "/mcp/oauth/authorize")
        self.assertEqual(body["token_endpoint"], base + "/mcp/oauth/token")
        self.assertEqual(body["registration_endpoint"], base + "/mcp/oauth/register")

    def test_protected_resource_metadata_path_based_well_known(self):
        """The RFC 9728 path-inserted well-known URI serves the same document.

        A client deriving the metadata URL from the resource identifier
        ``<base>/mcp`` (path-insertion form) instead of following the
        ``WWW-Authenticate`` hint must land on the same document.
        """
        prm = self.url_open("/.well-known/oauth-protected-resource/mcp")
        self.assertEqual(prm.status_code, 200)
        self.assertEqual(prm.json()["resource"].rstrip("/"), self.resource.rstrip("/"))

    # ------------------------------------------------------------------
    # Consent denial
    # ------------------------------------------------------------------
    def test_consent_deny_redirects_with_access_denied(self):
        """Denying consent redirects to the client with error=access_denied."""
        verifier = secrets.token_urlsafe(48)
        params = self._authorize_params(challenge=_code_challenge(verifier))
        get_resp = self.url_open("/mcp/oauth/authorize", params=params)
        self.assertEqual(get_resp.status_code, 200)
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', get_resp.text)
        self.assertIsNotNone(match)

        post_data = dict(params)
        post_data["csrf_token"] = match.group(1)
        post_data["decision"] = "deny"
        resp = self.url_open(
            "/mcp/oauth/authorize", data=post_data, allow_redirects=False
        )
        self.assertEqual(resp.status_code, 302)
        location = resp.headers["Location"]
        self.assertTrue(location.startswith(self.redirect_uri), location)
        query = dict(parse_qsl(urlsplit(location).query))
        self.assertEqual(query.get("error"), "access_denied")
        self.assertNotIn("code", query)
        self.assertEqual(query.get("state"), params["state"])
        self.assertEqual(
            (query.get("iss") or "").rstrip("/"), self.base_url().rstrip("/")
        )

    # ------------------------------------------------------------------
    # Unknown / inactive client at authorize
    # ------------------------------------------------------------------
    def test_inactive_client_is_rejected_at_authorize(self):
        """An archived client cannot start an authorization request."""
        self.env["mcp.oauth.client"].sudo().search(
            [("client_id", "=", self.client_id)]
        ).active = False
        params = self._authorize_params(
            challenge=_code_challenge(secrets.token_urlsafe(48))
        )
        resp = self.url_open("/mcp/oauth/authorize", params=params)
        self.assertEqual(resp.status_code, 400)

    def test_unknown_client_is_rejected_at_authorize(self):
        """An unregistered client_id cannot start an authorization request."""
        params = self._authorize_params(
            challenge=_code_challenge(secrets.token_urlsafe(48)),
            client_id="no-such-client",
        )
        resp = self.url_open("/mcp/oauth/authorize", params=params)
        self.assertEqual(resp.status_code, 400)

    # ------------------------------------------------------------------
    # DCR hardening
    # ------------------------------------------------------------------
    def test_dcr_rejects_non_loopback_http_redirect(self):
        """DCR refuses an http redirect on a non-loopback host."""
        resp = self.url_open(
            "/mcp/oauth/register",
            json={
                "redirect_uris": ["http://attacker.example/cb"],
                "client_name": "Bad",
                "scope": "mcp",
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_dcr_rejects_empty_redirect_uris(self):
        """DCR refuses a registration with no redirect_uris."""
        resp = self.url_open(
            "/mcp/oauth/register",
            json={"redirect_uris": [], "client_name": "Bad", "scope": "mcp"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_dcr_rejects_userinfo_redirect(self):
        """DCR refuses a redirect_uri carrying a userinfo (consent-spoofing) part."""
        resp = self.url_open(
            "/mcp/oauth/register",
            json={
                "redirect_uris": ["https://trusted.example.com@evil.com/cb"],
                "client_name": "Spoof",
                "scope": "mcp",
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_dcr_rejects_too_many_redirect_uris(self):
        """DCR caps how many redirect_uris an unauthenticated client persists."""
        resp = self.url_open(
            "/mcp/oauth/register",
            json={
                "redirect_uris": [
                    "https://client.example.com/cb%d" % i for i in range(6)
                ],
                "client_name": "Greedy",
                "scope": "mcp",
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_dcr_rejects_overlong_client_name(self):
        """DCR caps client_name length (unauthenticated uncapped-write guard)."""
        resp = self.url_open(
            "/mcp/oauth/register",
            json={
                "redirect_uris": ["https://client.example.com/cb"],
                "client_name": "x" * 256,
                "scope": "mcp",
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_dcr_drops_unsupported_scope(self):
        """DCR keeps only supported scope tokens (RFC 7591 section 2).

        A client that also asks for e.g. ``offline_access`` still registers; the
        stored and echoed scope holds only the tokens the server honours.
        """
        clients = self.env["mcp.oauth.client"].sudo()
        for scope, kept in (
            ("mcp offline_access", "mcp"),
            ("mcp:read admin", "mcp:read"),
            ("offline_access", ""),
        ):
            with self.subTest(scope=scope):
                resp = self.url_open(
                    "/mcp/oauth/register",
                    json={"redirect_uris": [self.redirect_uri], "scope": scope},
                )
                self.assertIn(resp.status_code, (200, 201), resp.text[:300])
                body = resp.json()
                self.assertEqual(body.get("scope") or "", kept)
                client = clients.search([("client_id", "=", body["client_id"])])
                self.assertEqual(client.scope or "", kept)

    def test_challenge_omits_resource_metadata_when_mcp_disabled(self):
        """With the master switch off, the 401 points at no discovery document.

        The protected-resource metadata answers 404 while MCP is off, so the
        challenge must not advertise it; with MCP back on it does again.
        """
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("mcp_server.enabled", "False")
        utils.clear_mcp_caches()
        metadata = self.url_open("/.well-known/oauth-protected-resource")
        self.assertEqual(metadata.status_code, 404)
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        refused = self.url_open("/mcp", json=ping)
        self.assertEqual(refused.status_code, 401)
        www = refused.headers.get("WWW-Authenticate", "")
        self.assertIn("Bearer", www)
        self.assertNotIn("resource_metadata", www)

        params.set_param("mcp_server.enabled", "True")
        utils.clear_mcp_caches()
        refused = self.url_open("/mcp", json=ping)
        www = refused.headers.get("WWW-Authenticate", "")
        self.assertIn("resource_metadata=", www)

    def test_dcr_rejects_overlong_redirect_uri(self):
        """DCR caps the length of an individual redirect_uri."""
        long_uri = "https://client.example.com/cb?x=" + "a" * 2048
        resp = self.url_open(
            "/mcp/oauth/register",
            json={
                "redirect_uris": [long_uri],
                "client_name": "Long",
                "scope": "mcp",
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_authorize_rejects_userinfo_redirect(self):
        """A hand-registered userinfo redirect_uri is still refused at authorize."""
        # Simulate a client provisioned outside DCR (e.g. via the backend UI) that
        # bypassed the DCR redirect-URI checks and holds a spoofing redirect_uri.
        spoof_uri = "https://trusted.example.com@evil.com/cb"
        client = (
            self.env["mcp.oauth.client"]
            .sudo()
            .create(
                {
                    "client_id": f"spoof-{int(time.time() * 1000)}",
                    "redirect_uris": spoof_uri,
                }
            )
        )
        params = self._authorize_params(
            challenge=_code_challenge(secrets.token_urlsafe(48)),
            client_id=client.client_id,
            redirect_uri=spoof_uri,
        )
        resp = self.url_open("/mcp/oauth/authorize", params=params)
        self.assertEqual(resp.status_code, 400)

    def test_dcr_rejects_fragment_redirect(self):
        """DCR refuses a redirect_uri carrying a fragment (RFC 6749 3.1.2)."""
        resp = self.url_open(
            "/mcp/oauth/register",
            json={
                "redirect_uris": ["https://client.example/cb#frag"],
                "client_name": "Frag",
                "scope": "mcp",
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_dcr_rejects_malformed_redirect_uri(self):
        """A malformed redirect_uri authority is refused with a clean 400, not 500."""
        # Each authority is malformed differently: an unclosed IPv6 literal makes
        # urlsplit itself raise, while a non-numeric / out-of-range port makes
        # .port raise -- all of which must degrade to a 400, never a 500 crash.
        for bad_uri in (
            "http://[::1/cb",
            "https://evil.com:abc/cb",
            "https://evil.com:99999/cb",
        ):
            resp = self.url_open(
                "/mcp/oauth/register",
                json={
                    "redirect_uris": [bad_uri],
                    "client_name": "Malformed",
                    "scope": "mcp",
                },
            )
            self.assertEqual(
                resp.status_code,
                400,
                f"{bad_uri!r} -> {resp.status_code}: {resp.text[:200]}",
            )

    def test_dcr_ip_rate_limit_returns_429(self):
        """Registration is capped per IP independently of the API request limit."""
        # Even with the general API limit disabled, the DCR cap still applies.
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.request_limit", "0"
        )
        oauth_server._dcr_limiter.clear()
        body = {
            "redirect_uris": [self.redirect_uri],
            "client_name": "Flood",
            "scope": "mcp",
        }
        last = None
        for _i in range(oauth_server._DCR_MAX_REGISTRATIONS + 1):
            last = self.url_open("/mcp/oauth/register", json=body)
        self.assertEqual(last.status_code, 429)
        self.assertEqual(last.json().get("error"), "temporarily_unavailable")

    def test_token_endpoint_ip_rate_limited(self):
        """The token endpoint carries its own per-IP DoS backstop cap."""
        oauth_server._token_limiter.clear()
        # Cap 0 => the first request already trips the limiter, so it 429s
        # before ever reaching token processing (no valid grant needed).
        self.patch(oauth_server, "_TOKEN_MAX_REQUESTS", 0)
        resp = self.url_open(
            "/mcp/oauth/token",
            data={"grant_type": "authorization_code", "code": "nope"},
        )
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json().get("error"), "temporarily_unavailable")


@tagged("much_unit", "post_install", "-at_install")
class TestOAuthDisabled(UrlOpenCompatMixin, common.HttpCase):
    """OAuth front door explicitly OFF: endpoints 404, tokens refused.

    ``mcp_server.enable_oauth`` can be turned off, so with MCP enabled but OAuth off
    the whole authorization server (discovery + authorize/token/register) must be
    unreachable (404) and OAuth access tokens must not authenticate ``/mcp``
    -- while ordinary rpc-scope API-key auth keeps working untouched.
    """

    def setUp(self):
        super().setUp()
        utils.clear_mcp_caches()
        rate_limiting._api_limiter.clear()
        oauth_server._dcr_limiter.clear()

        unique_id = str(int(time.time() * 1000))[-6:]
        self.login = f"mcp_oauth_off_user_{unique_id}"
        self.password = "oauth_off_pw"  # nosec B105 - test fixture credential
        self.user = create_test_user(
            self.env,
            "MCP OAuth-Off User",
            self.login,
            password=self.password,
            email=f"mcp_oauth_off_{unique_id}@example.com",
        )
        grant_mcp_access(self.user)
        # An rpc-scope API key: the always-available front door, unaffected by
        # the OAuth switch.
        self.api_key = self.env(user=self.user)["res.users.apikeys"]._generate(
            "rpc", "OAuth-Off Key"
        )
        self.resource = self.base_url() + "/mcp"
        self.redirect_uri = "http://127.0.0.1:8765/callback"

        params = self.env["ir.config_parameter"].sudo()
        params.set_param("mcp_server.enabled", "True")
        # OAuth explicitly OFF -- the whole OAuth front door must be closed.
        params.set_param("mcp_server.enable_oauth", "False")
        self._enable_model("base.model_res_partner", allow_read=True)
        utils.clear_mcp_caches()

        # A resource-owner session so the auth='user' authorize route reaches its
        # handler (and hits the 404 guard) instead of redirecting to /web/login.
        self.authenticate(self.login, self.password)

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

    def _rpc(self, token, method):
        """POST a JSON-RPC ``method`` to /mcp with a bearer ``token``."""
        body = {"jsonrpc": "2.0", "id": 1, "method": method}
        headers = {"Authorization": f"Bearer {token}"}
        return self.url_open("/mcp", json=body, headers=headers)

    def test_oauth_available_by_default_when_param_unset(self):
        """OAuth is on by default: an unset enable_oauth parameter counts as on."""
        params = self.env["ir.config_parameter"].sudo()
        params.search([("key", "=", "mcp_server.enable_oauth")]).unlink()
        utils.clear_mcp_caches()
        self.assertTrue(utils.is_oauth_enabled(self.env))
        resp = self.url_open(
            "/mcp/oauth/register",
            json={"redirect_uris": [self.redirect_uri], "scope": "mcp"},
        )
        self.assertIn(resp.status_code, (200, 201), resp.text[:300])

    def test_all_oauth_endpoints_return_404_when_disabled(self):
        """Discovery docs + authorize/token/register all 404 while OAuth is off."""
        for path in (
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-authorization-server",
        ):
            resp = self.url_open(path)
            self.assertEqual(resp.status_code, 404, path)

        register = self.url_open(
            "/mcp/oauth/register",
            json={"redirect_uris": [self.redirect_uri], "scope": "mcp"},
        )
        self.assertEqual(register.status_code, 404)

        authorize = self.url_open(
            "/mcp/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": "whatever",
                "redirect_uri": self.redirect_uri,
                "code_challenge": _code_challenge(secrets.token_urlsafe(48)),
                "code_challenge_method": "S256",
            },
            allow_redirects=False,
        )
        self.assertEqual(authorize.status_code, 404)

        token = self.url_open(
            "/mcp/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": "whatever",
                "client_id": "whatever",
            },
            allow_redirects=False,
        )
        self.assertEqual(token.status_code, 404)

    def test_oauth_token_refused_but_api_key_works_when_disabled(self):
        """An mcp.oauth.token is refused at /mcp; an rpc API key still works."""
        client = (
            self.env["mcp.oauth.client"]
            .sudo()
            .create(
                {
                    "client_id": f"off-client-{int(time.time() * 1000)}",
                    "redirect_uris": self.redirect_uri,
                }
            )
        )
        raw_token = secrets.token_urlsafe(48)
        # Audience matches this resource, so ONLY the disabled-OAuth gate can be
        # what rejects it (not the RFC 8707 audience check).
        self.env["mcp.oauth.token"].sudo().create(
            {
                "access_token_hash": _sha256_hex(raw_token),
                "client": client.id,
                "user_id": self.user.id,
                "scope": "mcp",
                "audience": self.resource,
                "access_expires_at": fields.Datetime.now() + timedelta(hours=1),
            }
        )

        refused = self._rpc(raw_token, "ping")
        self.assertEqual(refused.status_code, 401)
        # No AS to discover while OAuth is off -> plain Bearer challenge.
        www = refused.headers.get("WWW-Authenticate", "")
        self.assertIn("Bearer", www)
        self.assertNotIn("resource_metadata", www)

        # The always-available API-key door still authenticates.
        ok = self._rpc(self.api_key, "ping")
        self.assertEqual(ok.status_code, 200)
        self.assertNotIn("error", ok.json())

    def test_reenabling_oauth_restores_the_front_door(self):
        """Turning enable_oauth back on re-exposes registration (spot check)."""
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("mcp_server.enable_oauth", "True")
        utils.clear_mcp_caches()

        response = self.url_open(
            "/mcp/oauth/register",
            json={
                "redirect_uris": [self.redirect_uri],
                "client_name": "Re-enabled Client",
                "scope": "mcp",
            },
        )
        self.assertIn(response.status_code, (200, 201), response.text[:300])
        self.assertTrue(response.json().get("client_id"))

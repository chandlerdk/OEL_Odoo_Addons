"""Tests for confidential OAuth clients (client secret + auth-method allow-list).

A client stores the token-endpoint auth methods it may use as a space-separated
allow-list, like ``grant_types``:

* ``none`` -- public PKCE client, no secret (the historic profile);
* ``client_secret_basic client_secret_post`` -- confidential client, the
  secret is required;
* ``client_secret_basic client_secret_post none`` -- a DCR client that omitted
  the method: it gets a secret but may also authenticate as a public client.

The secret is stored only as its SHA-256 hash.
"""

import base64
import re
import secrets
import time
import uuid
from datetime import timedelta
from urllib.parse import urlencode

from odoo import fields
from odoo.tests import common, tagged

from ..controllers import oauth_server, rate_limiting, utils
from .test_helpers import UrlOpenCompatMixin, create_test_user, grant_mcp_access
from .test_oauth import TestOAuth, _code_challenge, _sha256_hex

_STRICT = "client_secret_basic client_secret_post"
_LENIENT = "client_secret_basic client_secret_post none"
# Marks a registration field to leave out of the request body.
_OMIT = object()
# A deliberately wrong client secret.
_WRONG_SECRET = "wrong-secret"  # nosec B105 - test value, not a credential


def _basic(user, password):
    """Return an HTTP Basic ``Authorization`` header for ``user:password``."""
    raw = f"{user}:{password}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


@tagged("much_unit", "post_install", "-at_install")
class TestOAuthConfidential(common.TransactionCase):
    """Model-level secret handling and auth-method checks."""

    def _client(self, auth_methods=None, **vals):
        values = {"client_id": f"conf-test-{uuid.uuid4().hex}", **vals}
        if auth_methods is not None:
            values["token_endpoint_auth_method"] = auth_methods
        return self.env["mcp.oauth.client"].sudo().create(values)

    def test_default_auth_method_is_none(self):
        """A client created without an auth method is a public client."""
        client = self._client()
        self.assertEqual(client.token_endpoint_auth_method, "none")
        self.assertFalse(client.client_secret_hash)
        self.assertFalse(client.is_confidential)

    def test_check_client_secret_without_hash_fails(self):
        client = self._client()
        self.assertFalse(client.check_client_secret("anything"))
        self.assertFalse(client.check_client_secret(""))

    def test_check_client_secret_matches_hash_only(self):
        client = self._client(_STRICT, client_secret_hash=_sha256_hex("s3cret"))
        self.assertTrue(client.check_client_secret("s3cret"))
        self.assertFalse(client.check_client_secret("s3cret "))
        self.assertFalse(client.check_client_secret("other"))
        self.assertFalse(client.check_client_secret(""))
        self.assertFalse(client.check_client_secret(None))
        self.assertTrue(client.is_confidential)

    def test_endpoint_auth_method_public(self):
        client = self._client("none")
        self.assertTrue(client.check_endpoint_auth_method("none", "token"))
        self.assertFalse(
            client.check_endpoint_auth_method("client_secret_basic", "token")
        )
        self.assertFalse(
            client.check_endpoint_auth_method("client_secret_post", "token")
        )

    def test_endpoint_auth_method_strict(self):
        client = self._client(_STRICT)
        self.assertTrue(
            client.check_endpoint_auth_method("client_secret_basic", "token")
        )
        self.assertTrue(
            client.check_endpoint_auth_method("client_secret_post", "token")
        )
        self.assertFalse(client.check_endpoint_auth_method("none", "token"))

    def test_endpoint_auth_method_lenient(self):
        client = self._client(_LENIENT)
        for method in ("client_secret_basic", "client_secret_post", "none"):
            self.assertTrue(client.check_endpoint_auth_method(method, "token"), method)
        self.assertFalse(client.check_endpoint_auth_method("private_key_jwt", "token"))

    def test_endpoint_auth_method_empty_fails_closed(self):
        client = self._client("")
        for method in ("client_secret_basic", "client_secret_post", "none"):
            self.assertFalse(client.check_endpoint_auth_method(method, "token"), method)

    def test_generate_secret_stores_only_the_hash(self):
        client = self._client(_STRICT)
        secret = client._generate_secret()
        self.assertGreaterEqual(len(secret), 40)
        # Letters and digits only, so a Basic header cannot alter it.
        self.assertTrue(secret.isalnum(), secret)
        self.assertEqual(client.client_secret_hash, _sha256_hex(secret))
        self.assertTrue(client.check_client_secret(secret))
        # A new secret replaces the old one.
        new_secret = client._generate_secret()
        self.assertNotEqual(new_secret, secret)
        self.assertFalse(client.check_client_secret(secret))
        self.assertTrue(client.check_client_secret(new_secret))

    def test_register_client_public_by_default(self):
        """The historic call (no auth methods, no secret) stays a public client."""
        client = (
            self.env["mcp.oauth.client"]
            .sudo()
            ._register_client(
                {"client_id": "dcr-public"},
                {"redirect_uris": ["https://example.com/cb"], "client_name": "P"},
            )
        )
        self.assertEqual(client.token_endpoint_auth_method, "none")
        self.assertFalse(client.client_secret_hash)
        self.assertEqual(client.created_via, "dcr")

    def test_register_client_with_secret_stores_hash(self):
        client = (
            self.env["mcp.oauth.client"]
            .sudo()
            ._register_client(
                {"client_id": "dcr-conf", "client_secret": "dcr-s3cret"},
                {"redirect_uris": ["https://example.com/cb"], "client_name": "C"},
                auth_methods=_LENIENT,
            )
        )
        self.assertEqual(client.token_endpoint_auth_method, _LENIENT)
        self.assertEqual(client.client_secret_hash, _sha256_hex("dcr-s3cret"))
        # The secret is optional for this client, so it does not count as
        # confidential in the admin views.
        self.assertFalse(client.is_confidential)

    # ------------------------------------------------------------------
    # Garbage collection
    # ------------------------------------------------------------------
    def _aged_client(self, auth_methods, *, secret=False, used=False, via="dcr"):
        """A client created 40 days ago (past the DCR retention), no live token."""
        vals = {"created_via": via, "redirect_uris": "https://example.com/cb"}
        if used:
            vals["last_token_at"] = fields.Datetime.now() - timedelta(days=35)
        client = self._client(auth_methods, **vals)
        if secret:
            client._generate_secret()
        self.env.cr.execute(
            "UPDATE mcp_oauth_client SET create_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(days=40), client.id),
        )
        client.invalidate_recordset(["create_date"])
        return client

    def _gc(self):
        self.env["mcp.oauth.token"].sudo()._gc_oauth()

    def test_gc_deletes_unused_confidential_dcr_client(self):
        """A secret alone does not keep a client: spam registrations still go."""
        client = self._aged_client(_LENIENT, secret=True)
        self._gc()
        self.assertFalse(client.exists())

    def test_gc_keeps_used_strict_dcr_client(self):
        client = self._aged_client(_STRICT, secret=True, used=True)
        self._gc()
        self.assertTrue(client.exists())

    def test_gc_keeps_used_lenient_dcr_client(self):
        """The Microsoft 365 case: registered once, idle for over 30 days."""
        client = self._aged_client(_LENIENT, secret=True, used=True)
        self._gc()
        self.assertTrue(client.exists())

    def test_gc_still_deletes_idle_public_dcr_client(self):
        """The rule for public DCR clients does not change."""
        client = self._aged_client("none", used=True)
        self._gc()
        self.assertFalse(client.exists())

    def test_gc_never_deletes_manual_client(self):
        client = self._aged_client(_STRICT, secret=True, via="manual")
        self._gc()
        self.assertTrue(client.exists())


@tagged("much_unit", "post_install", "-at_install")
class TestOAuthConfidentialToken(UrlOpenCompatMixin, common.HttpCase):
    """Token-endpoint client authentication for public, strict and lenient clients.

    The clients are created directly in the DB, so these tests do not depend on
    what dynamic registration issues.
    """

    # The authorize/consent helpers of the public-client suite.
    _authorize_params = TestOAuth._authorize_params
    _get_authorization_code = TestOAuth._get_authorization_code

    def setUp(self):
        super().setUp()
        utils.clear_mcp_caches()
        rate_limiting._api_limiter.clear()
        oauth_server._dcr_limiter.clear()
        oauth_server._token_limiter.clear()

        unique_id = str(int(time.time() * 1000))[-6:]
        self.login = f"mcp_conf_user_{unique_id}"
        self.password = "conf_pw"  # nosec B105 - test fixture credential
        self.user = create_test_user(
            self.env,
            "MCP Confidential User",
            self.login,
            password=self.password,
            email=f"mcp_conf_{unique_id}@example.com",
        )
        grant_mcp_access(self.user)
        self.resource = self.base_url() + "/mcp"
        self.redirect_uri = "http://127.0.0.1:8765/callback"

        params = self.env["ir.config_parameter"].sudo()
        params.set_param("mcp_server.enabled", "True")
        params.set_param("mcp_server.enable_oauth", "True")
        utils.clear_mcp_caches()

        self.public, _secret = self._make_client("none")
        self.strict, self.strict_secret = self._make_client(_STRICT, with_secret=True)
        self.lenient, self.lenient_secret = self._make_client(
            _LENIENT, with_secret=True
        )
        self.authenticate(self.login, self.password)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _make_client(self, auth_methods, with_secret=False, created_via="dcr"):
        client = (
            self.env["mcp.oauth.client"]
            .sudo()
            .create(
                {
                    "client_id": f"conf-{uuid.uuid4().hex}",
                    "client_name": f"Client {auth_methods}",
                    "redirect_uris": self.redirect_uri,
                    "scope": "mcp",
                    "token_endpoint_auth_method": auth_methods,
                    "created_via": created_via,
                }
            )
        )
        secret = client._generate_secret() if with_secret else None
        return client, secret

    def _code(self, client):
        """Run authorize + consent for ``client``; return ``(code, verifier)``."""
        verifier = secrets.token_urlsafe(48)
        params = self._authorize_params(
            challenge=_code_challenge(verifier), client_id=client.client_id
        )
        return self._get_authorization_code(params), verifier

    def _exchange(self, client, *, form_id=True, secret=None, headers=None, pkce=True):
        """Redeem a fresh code of ``client``; return the token response."""
        code, verifier = self._code(client)
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "resource": self.resource,
        }
        if pkce:
            data["code_verifier"] = verifier
        if form_id:
            data["client_id"] = client.client_id
        if secret is not None:
            data["client_secret"] = secret
        return self.url_open(
            "/mcp/oauth/token", data=data, headers=headers, allow_redirects=False
        )

    def _refresh(
        self, client, refresh_token, *, form_id=True, secret=None, headers=None
    ):
        data = {"grant_type": "refresh_token", "refresh_token": refresh_token}
        if form_id:
            data["client_id"] = client.client_id
        if secret is not None:
            data["client_secret"] = secret
        return self.url_open(
            "/mcp/oauth/token", data=data, headers=headers, allow_redirects=False
        )

    def _refresh_token_of(self, client, secret=None):
        """Issue tokens for ``client`` (secret by POST); return the refresh token."""
        response = self._exchange(client, secret=secret)
        self.assertEqual(response.status_code, 200, response.text[:300])
        return response.json()["refresh_token"]

    def _assert_token(self, response):
        self.assertEqual(response.status_code, 200, response.text[:300])
        self.assertTrue(response.json().get("access_token"))

    def _assert_invalid_client(self, response):
        self.assertEqual(response.status_code, 401, response.text[:300])
        self.assertEqual(response.json().get("error"), "invalid_client")

    # ------------------------------------------------------------------
    # Authorization-code exchange
    # ------------------------------------------------------------------
    def test_strict_client_with_basic_gets_token(self):
        headers = _basic(self.strict.client_id, self.strict_secret)
        self._assert_token(self._exchange(self.strict, form_id=False, headers=headers))

    def test_strict_client_with_post_gets_token(self):
        self._assert_token(self._exchange(self.strict, secret=self.strict_secret))

    def test_wrong_secret_with_basic_is_refused(self):
        headers = _basic(self.strict.client_id, _WRONG_SECRET)
        response = self._exchange(self.strict, form_id=False, headers=headers)
        self._assert_invalid_client(response)

    def test_wrong_secret_with_post_is_refused(self):
        self._assert_invalid_client(self._exchange(self.strict, secret=_WRONG_SECRET))

    def test_strict_client_without_secret_is_refused(self):
        self._assert_invalid_client(self._exchange(self.strict))

    def test_confidential_client_still_needs_code_verifier(self):
        """PKCE stays mandatory: a valid secret does not replace the verifier."""
        response = self._exchange(self.strict, secret=self.strict_secret, pkce=False)
        self.assertEqual(response.status_code, 400, response.text[:300])
        self.assertEqual(response.json().get("error"), "invalid_request")

    def test_lenient_client_without_secret_gets_token(self):
        self._assert_token(self._exchange(self.lenient))

    def test_lenient_client_with_secret_gets_token(self):
        headers = _basic(self.lenient.client_id, self.lenient_secret)
        self._assert_token(self._exchange(self.lenient, form_id=False, headers=headers))
        self._assert_token(self._exchange(self.lenient, secret=self.lenient_secret))

    def test_lenient_client_wrong_basic_secret_does_not_fall_back_to_none(self):
        headers = _basic(self.lenient.client_id, _WRONG_SECRET)
        self._assert_invalid_client(self._exchange(self.lenient, headers=headers))

    def test_lenient_client_wrong_post_secret_is_refused(self):
        self._assert_invalid_client(self._exchange(self.lenient, secret=_WRONG_SECRET))

    def test_public_client_with_empty_basic_secret_gets_token(self):
        """``Basic base64(client_id:)`` with no secret stays a public request."""
        headers = _basic(self.public.client_id, "")
        self._assert_token(self._exchange(self.public, headers=headers))

    def test_proxy_basic_credentials_do_not_block_public_clients(self):
        """A Basic user name that is no client ID (proxy auth) is ignored."""
        headers = _basic("staging-proxy", "proxy-password")
        self._assert_token(self._exchange(self.public, headers=headers))
        self._assert_token(self._exchange(self.lenient, headers=headers))

    def test_undecodable_basic_header_counts_as_no_credentials(self):
        """A Basic header that is not UTF-8 is ignored instead of failing (500)."""
        headers = {"Authorization": "Basic /w=="}
        self._assert_token(self._exchange(self.public, headers=headers))
        self._assert_invalid_client(self._exchange(self.strict, headers=headers))

    def test_consent_post_needs_the_challenge_in_the_form(self):
        """A PKCE challenge only in the query string of the consent POST fails.

        Authlib stores the challenge from the form, so a query-only challenge
        would mint a code without PKCE, which a confidential client could then
        redeem with its secret alone.
        """
        verifier = secrets.token_urlsafe(48)
        params = self._authorize_params(
            challenge=_code_challenge(verifier), client_id=self.strict.client_id
        )
        page = self.url_open("/mcp/oauth/authorize", params=params)
        self.assertEqual(page.status_code, 200, page.text[:300])
        csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', page.text).group(1)
        pkce = {
            key: params.pop(key) for key in ("code_challenge", "code_challenge_method")
        }
        response = self.url_open(
            "/mcp/oauth/authorize?" + urlencode(pkce),
            data={**params, "csrf_token": csrf, "decision": "allow"},
            allow_redirects=False,
        )
        self.assertEqual(response.status_code, 400, response.text[:300])
        self.assertNotIn("code=", response.headers.get("Location", ""))

    def test_offline_access_scope_is_ignored(self):
        """``offline_access`` is dropped from a request, not refused.

        A client sends it to ask for a refresh token, and many Microsoft clients
        send it by habit. The server always issues a refresh token, so the scope
        adds nothing. Other unknown scopes are still refused.
        """
        verifier = secrets.token_urlsafe(48)
        params = self._authorize_params(
            challenge=_code_challenge(verifier),
            client_id=self.lenient.client_id,
            scope="mcp offline_access",
        )
        code = self._get_authorization_code(params)
        response = self.url_open(
            "/mcp/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "resource": self.resource,
                "code_verifier": verifier,
                "client_id": self.lenient.client_id,
            },
            allow_redirects=False,
        )
        self._assert_token(response)
        tokens = response.json()
        self.assertTrue(tokens.get("refresh_token"))
        self.assertNotIn("offline_access", tokens.get("scope", ""))
        refreshed = self.url_open(
            "/mcp/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "scope": "offline_access",
                "client_id": self.lenient.client_id,
            },
            allow_redirects=False,
        )
        self._assert_token(refreshed)

    def test_public_client_sending_a_secret_is_refused(self):
        """A public client has no secret; sending one fails with 401."""
        self._assert_invalid_client(self._exchange(self.public, secret=_WRONG_SECRET))

    # ------------------------------------------------------------------
    # Refresh grant
    # ------------------------------------------------------------------
    def test_strict_client_refresh_with_basic_and_post(self):
        refresh_token = self._refresh_token_of(self.strict, self.strict_secret)
        headers = _basic(self.strict.client_id, self.strict_secret)
        response = self._refresh(
            self.strict, refresh_token, form_id=False, headers=headers
        )
        self._assert_token(response)
        response = self._refresh(
            self.strict, response.json()["refresh_token"], secret=self.strict_secret
        )
        self._assert_token(response)

    def test_strict_client_refresh_without_secret_is_refused(self):
        refresh_token = self._refresh_token_of(self.strict, self.strict_secret)
        self._assert_invalid_client(self._refresh(self.strict, refresh_token))

    def test_lenient_client_refresh_wrong_basic_secret_is_refused(self):
        refresh_token = self._refresh_token_of(self.lenient)
        headers = _basic(self.lenient.client_id, _WRONG_SECRET)
        response = self._refresh(self.lenient, refresh_token, headers=headers)
        self._assert_invalid_client(response)

    def test_lenient_client_refresh_without_secret(self):
        refresh_token = self._refresh_token_of(self.lenient)
        self._assert_token(self._refresh(self.lenient, refresh_token))

    def test_regenerated_secret_replaces_the_old_one(self):
        """After "Regenerate Secret" the old secret fails and the new one works."""
        manual, old_secret = self._make_client(
            _STRICT, with_secret=True, created_via="manual"
        )
        refresh_token = self._refresh_token_of(manual, old_secret)
        wizard = (
            self.env["mcp.oauth.client.secret.wizard"]
            .with_user(self.env.ref("base.user_admin"))
            .create({"client_id": manual.id})
        )
        action = wizard._regenerate_client_secret()
        new_secret = action["context"]["default_client_secret"]
        self.env.flush_all()
        response = self._refresh(manual, refresh_token, secret=old_secret)
        self._assert_invalid_client(response)
        self._assert_token(self._refresh(manual, refresh_token, secret=new_secret))

    # ------------------------------------------------------------------
    # Last-token marker (keeps a used confidential DCR client out of GC)
    # ------------------------------------------------------------------
    def _set_last_token_at(self, client, value):
        client.write({"last_token_at": value})
        self.env.flush_all()

    def _last_token_at(self, client):
        client.invalidate_recordset(["last_token_at"])
        return client.last_token_at

    def test_code_exchange_sets_last_token_at(self):
        self.assertFalse(self.lenient.last_token_at)
        self._assert_token(self._exchange(self.lenient))
        self.assertTrue(self._last_token_at(self.lenient))

    def test_refresh_updates_last_token_at_at_most_once_a_day(self):
        refresh_token = self._refresh_token_of(self.lenient)
        recent = fields.Datetime.now() - timedelta(hours=2)
        self._set_last_token_at(self.lenient, recent)
        response = self._refresh(self.lenient, refresh_token)
        self._assert_token(response)
        self.assertEqual(self._last_token_at(self.lenient), recent)

        old = fields.Datetime.now() - timedelta(days=2)
        self._set_last_token_at(self.lenient, old)
        response = self._refresh(self.lenient, response.json()["refresh_token"])
        self._assert_token(response)
        self.assertGreater(self._last_token_at(self.lenient), old + timedelta(days=1))


_TEAMS_REDIRECT = "https://teams.microsoft.com/api/platform/v1.0/oAuthRedirect"
_OAUTH_LOGGER = "odoo.addons.mcp_server.controllers.oauth_server"


@tagged("much_unit", "post_install", "-at_install")
class TestOAuthConfidentialRegistration(UrlOpenCompatMixin, common.HttpCase):
    """Dynamic client registration decides the client class from the request."""

    def setUp(self):
        super().setUp()
        utils.clear_mcp_caches()
        oauth_server._dcr_limiter.clear()
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("mcp_server.enabled", "True")
        params.set_param("mcp_server.enable_oauth", "True")
        utils.clear_mcp_caches()

    def _register(self, method=_OMIT, **overrides):
        """Register a client; ``method`` is its token_endpoint_auth_method."""
        body = {
            "redirect_uris": [_TEAMS_REDIRECT],
            "client_name": "Microsoft 365 Agent",
            "scope": "mcp",
            **overrides,
        }
        if method is not _OMIT:
            body["token_endpoint_auth_method"] = method
        return self.url_open("/mcp/oauth/register", json=body)

    def _stored(self, client_id):
        return (
            self.env["mcp.oauth.client"]
            .sudo()
            .search([("client_id", "=", client_id)], limit=1)
        )

    def _assert_secret_only_hashed(self, client, secret):
        self.assertEqual(client.client_secret_hash, _sha256_hex(secret))
        self.env.cr.execute(
            "SELECT count(*) FROM mcp_oauth_client c WHERE c::text LIKE %s",
            [f"%{secret}%"],
        )
        self.assertEqual(self.env.cr.fetchone()[0], 0, "plaintext secret stored")

    def _assert_confidential(self, response, answered_method, stored_methods):
        self.assertEqual(response.status_code, 201, response.text[:300])
        body = response.json()
        self.assertTrue(body.get("client_secret"))
        self.assertEqual(body.get("client_secret_expires_at"), 0)
        self.assertEqual(body.get("token_endpoint_auth_method"), answered_method)
        client = self._stored(body["client_id"])
        self.assertEqual(client.token_endpoint_auth_method, stored_methods)
        self.assertEqual(client.created_via, "dcr")
        self._assert_secret_only_hashed(client, body["client_secret"])

    def test_explicit_none_registers_public_client(self):
        response = self._register("none")
        self.assertEqual(response.status_code, 201, response.text[:300])
        body = response.json()
        self.assertNotIn("client_secret", body)
        self.assertEqual(body.get("token_endpoint_auth_method"), "none")
        client = self._stored(body["client_id"])
        self.assertEqual(client.token_endpoint_auth_method, "none")
        self.assertFalse(client.client_secret_hash)

    def test_client_secret_basic_registers_strict_client(self):
        response = self._register("client_secret_basic")
        self._assert_confidential(response, "client_secret_basic", _STRICT)

    def test_client_secret_post_registers_strict_client(self):
        response = self._register("client_secret_post")
        self._assert_confidential(response, "client_secret_post", _STRICT)

    def test_omitted_method_registers_lenient_client(self):
        """RFC 7591: an omitted method means client_secret_basic."""
        response = self._register()
        self._assert_confidential(response, "client_secret_basic", _LENIENT)

    def test_null_or_empty_method_counts_as_omitted(self):
        for value in (None, ""):
            with self.subTest(value=value):
                response = self._register(value)
                self._assert_confidential(response, "client_secret_basic", _LENIENT)

    def test_offline_access_scope_registers(self):
        response = self._register(scope="mcp offline_access")
        self.assertEqual(response.status_code, 201, response.text[:300])

    def test_unsupported_scopes_register_read_only(self):
        """An empty stored scope means full access, so it is kept for none of these.

        A request with no supported token is stored as ``mcp:read``, unless it
        asked only for ``offline_access``. A request with no scope keeps the empty
        scope, and a scope that is not a string is refused.
        """
        for requested, stored in (
            ("openid profile", "mcp:read"),
            ("openid offline_access", "mcp:read"),
            ("offline_access", ""),
            ("mcp:write openid", "mcp:write"),
        ):
            with self.subTest(requested=requested):
                response = self._register(scope=requested)
                self.assertEqual(response.status_code, 201, response.text[:300])
                self.assertEqual(response.json().get("scope", ""), stored)
                client = self._stored(response.json()["client_id"])
                self.assertEqual(client.scope or "", stored)
        response = self._register(scope=None)
        self.assertEqual(response.status_code, 201, response.text[:300])
        self.assertEqual(self._stored(response.json()["client_id"]).scope or "", "")
        before = self.env["mcp.oauth.client"].sudo().search_count([])
        response = self._register(scope=["mcp:read"])
        self.assertEqual(response.status_code, 400, response.text[:300])
        self.assertEqual(response.json().get("error"), "invalid_client_metadata")
        self.assertEqual(self.env["mcp.oauth.client"].sudo().search_count([]), before)

    def test_unsupported_method_is_rejected(self):
        before = self.env["mcp.oauth.client"].sudo().search_count([])
        response = self._register("private_key_jwt")
        self.assertEqual(response.status_code, 400, response.text[:300])
        self.assertEqual(response.json().get("error"), "invalid_client_metadata")
        self.assertEqual(self.env["mcp.oauth.client"].sudo().search_count([]), before)

    def test_metadata_advertises_secret_methods(self):
        """``none`` stays first for clients that take the first entry."""
        body = self.url_open("/.well-known/oauth-authorization-server").json()
        self.assertEqual(
            body["token_endpoint_auth_methods_supported"],
            ["none", "client_secret_basic", "client_secret_post"],
        )

    def test_registration_log_line(self):
        """Each request logs its auth method and redirect hosts, never the secret."""
        with self.assertLogs(_OAUTH_LOGGER, "INFO") as logs:
            response = self._register("client_secret_post")
        self.assertEqual(response.status_code, 201, response.text[:300])
        line = next(r for r in logs.output if "OAuth DCR" in r)
        self.assertIn("client_secret_post", line)
        self.assertIn("teams.microsoft.com", line)
        self.assertIn("Microsoft 365 Agent", line)
        self.assertNotIn(response.json()["client_secret"], "\n".join(logs.output))

        with self.assertLogs(_OAUTH_LOGGER, "INFO") as logs:
            self._register()
        self.assertTrue(any("omitted" in r for r in logs.output), logs.output)

    def test_rejected_registration_is_logged(self):
        with self.assertLogs(_OAUTH_LOGGER, "INFO") as logs:
            response = self._register("private_key_jwt")
        self.assertEqual(response.status_code, 400, response.text[:300])
        self.assertTrue(any("private_key_jwt" in r for r in logs.output), logs.output)

    def test_log_line_caps_long_redirect_hosts(self):
        """An open endpoint must not write huge log lines (5 URIs of 5000 chars)."""
        long_uri = "https://" + "a" * 5000 + ".example.com/cb"
        with self.assertLogs(_OAUTH_LOGGER, "INFO") as logs:
            self._register(redirect_uris=[long_uri] * 5)
        line = next(r for r in logs.output if "OAuth DCR" in r)
        self.assertLess(len(line), 3000)

    def test_log_line_survives_odd_redirect_uris(self):
        """A non-string or malformed redirect URI does not break the log line."""
        for uris in ([123, None], ["http://[::1"], "not-a-list"):
            with self.subTest(uris=uris):
                with self.assertLogs(_OAUTH_LOGGER, "INFO") as logs:
                    self._register(redirect_uris=uris)
                self.assertTrue(any("OAuth DCR" in r for r in logs.output), logs.output)

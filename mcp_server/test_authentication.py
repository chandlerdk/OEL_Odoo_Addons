"""Tests for MCP Server authentication."""

import json

from odoo.tests import tagged
from odoo.tests.common import HttpCase

from ..controllers import auth
from .test_helpers import create_test_user


@tagged("much_unit", "post_install", "-at_install")
class TestMCPAuthentication(HttpCase):
    """Test MCP Server authentication functionality."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Create test user with API key
        cls.test_user = create_test_user(
            cls.env,
            "Test MCP User",
            "test_mcp_user",  # nosec
            password="test_password",
            group_ids=[(6, 0, [cls.env.ref("mcp_server.group_mcp_user").id])],
        )

        # Create API key for test user
        env_as_user = cls.env(user=cls.test_user)
        cls.api_key = env_as_user["res.users.apikeys"]._generate("rpc", "Test API Key")

    def setUp(self):
        super().setUp()
        # Reset the per-IP auth-failure audit throttle (a module-level global
        # that otherwise carries state across tests) so the audit-row
        # assertions below start from a clean window.
        auth._auth_failure_limiter.clear()
        # Global MCP kill-switch defaults off; enable it so the gated REST
        # endpoints under test answer 200 instead of 503 on a fresh database.
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.enabled", "True")

    def test_01_auth_with_valid_api_key(self):
        """Test authentication with valid API key."""
        headers = {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
        }
        response = self.url_open("/mcp/auth/validate", headers=headers)
        self.assertEqual(response.status_code, 200)

        data = json.loads(response.text)
        self.assertTrue(data["success"])
        self.assertTrue(data["data"]["valid"])
        self.assertEqual(data["data"]["user_id"], self.test_user.id)
        self.assertEqual(data["data"]["auth_method"], "api_key")

    def test_02_auth_with_invalid_api_key(self):
        """Test authentication with invalid API key."""
        headers = {
            "X-API-Key": "invalid_api_key_12345",
            "Accept": "application/json",
        }
        response = self.url_open("/mcp/auth/validate", headers=headers)
        self.assertEqual(response.status_code, 401)

        data = json.loads(response.text)
        self.assertFalse(data["success"])
        self.assertEqual(data["error"]["code"], "E401")

    def test_02b_invalid_api_key_is_audited(self):
        """An invalid API key is rejected (401) and the failure is audited.

        ``auth._log_auth_failure`` writes the row on an independent, committed
        cursor -- in production it survives even the request-transaction reset a
        raising ``/mcp`` auth triggers. Under HttpCase that cursor is only a
        savepoint on the shared test cursor, so a raising route's row is rolled
        back and unobservable; this REST route returns 401 instead of raising,
        so its audit row stays visible here.
        """
        domain = [
            ("event_type", "=", "auth_failure"),
            ("api_key_used", "=", True),
            ("error_message", "=", "Invalid API key"),
        ]
        log_model = self.env["mcp.log"].sudo()
        before = log_model.search_count(domain)

        response = self.url_open(
            "/mcp/auth/validate",
            headers={
                "X-API-Key": "invalid_api_key_audit",
                "Accept": "application/json",
            },
        )
        self.assertEqual(response.status_code, 401)

        self.env.invalidate_all()
        self.assertEqual(
            log_model.search_count(domain),
            before + 1,
            "an invalid API key must persist an auth_failure audit row",
        )

    def test_02c_auth_failure_audit_is_throttled(self):
        """A bad-key flood from one IP caps the independent-cursor audit writes.

        ``auth._log_auth_failure`` opens a fresh cursor + explicit commit (fsync)
        per rejected key; the legacy REST route reaches it with the default
        ``log_failure=True`` and rate limiting off. A per-IP
        ``SlidingWindowLimiter`` caps the committed write at ``_AUTH_FAILURE_MAX``
        per window, so a flood cannot amplify into one fsync per request -- while
        a single bad key still logs (test_02b).
        """
        domain = [
            ("event_type", "=", "auth_failure"),
            ("api_key_used", "=", True),
            ("error_message", "=", "Invalid API key"),
        ]
        log_model = self.env["mcp.log"].sudo()
        before = log_model.search_count(domain)

        burst = auth._AUTH_FAILURE_MAX + 5
        for _i in range(burst):
            response = self.url_open(
                "/mcp/auth/validate",
                headers={
                    "X-API-Key": "invalid_api_key_flood",
                    "Accept": "application/json",
                },
            )
            self.assertEqual(response.status_code, 401)

        self.env.invalidate_all()
        self.assertEqual(
            log_model.search_count(domain) - before,
            auth._AUTH_FAILURE_MAX,
            "auth-failure audit writes must be capped per IP under a bad-key flood",
        )

    def test_03_auth_with_no_api_key(self):
        """Test authentication with no API key header."""
        headers = {
            "Accept": "application/json",
        }
        response = self.url_open("/mcp/auth/validate", headers=headers)
        self.assertEqual(response.status_code, 401)

        data = json.loads(response.text)
        self.assertFalse(data["success"])
        self.assertEqual(data["error"]["code"], "E401")

    def test_04_auth_with_empty_api_key(self):
        """Test authentication with empty API key header."""
        headers = {
            "X-API-Key": "",
            "Accept": "application/json",
        }
        response = self.url_open("/mcp/auth/validate", headers=headers)
        self.assertEqual(response.status_code, 401)

        data = json.loads(response.text)
        self.assertFalse(data["success"])
        self.assertEqual(data["error"]["code"], "E401")

    def test_05_system_info_requires_auth(self):
        """Test that system info endpoint requires authentication."""
        # No API key
        response = self.url_open(
            "/mcp/system/info", headers={"Accept": "application/json"}
        )
        self.assertEqual(response.status_code, 401)

        # With valid API key
        headers = {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
        }
        response = self.url_open("/mcp/system/info", headers=headers)
        self.assertEqual(response.status_code, 200)

        data = json.loads(response.text)
        self.assertTrue(data["success"])
        self.assertIn("db_name", data["data"])
        self.assertIn("odoo_version", data["data"])

    def test_06_models_endpoint_requires_auth(self):
        """Test that models endpoint requires authentication."""
        # No API key
        response = self.url_open("/mcp/models", headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 401)

        # With valid API key
        headers = {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
        }
        response = self.url_open("/mcp/models", headers=headers)
        self.assertEqual(response.status_code, 200)

        data = json.loads(response.text)
        self.assertTrue(data["success"])
        self.assertIn("models", data["data"])
        self.assertIsInstance(data["data"]["models"], list)


@tagged("much_unit", "post_install", "-at_install")
class TestSessionAuth(HttpCase):
    """Test session-based authentication for MCP REST endpoints."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.test_user = create_test_user(
            cls.env,
            "Test Session User",
            "test_session_user",
            password="test_session_pass",
            group_ids=[(6, 0, [cls.env.ref("mcp_server.group_mcp_user").id])],
        )

        # Create API key for priority test
        env_as_user = cls.env(user=cls.test_user)
        cls.api_key = env_as_user["res.users.apikeys"]._generate(
            "rpc", "Test Session API Key"
        )

    def setUp(self):
        super().setUp()
        # Global MCP kill-switch defaults off; enable it so the gated REST
        # endpoints under test answer 200 instead of 503 on a fresh database.
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.enabled", "True")

    def test_01_session_auth_on_models_endpoint(self):
        """Session-authenticated user can access /mcp/models."""
        self.authenticate("test_session_user", "test_session_pass")
        response = self.url_open("/mcp/models", headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.text)
        self.assertTrue(data["success"])

    def test_02_session_auth_on_validate_endpoint(self):
        """Session-authenticated user can validate auth."""
        self.authenticate("test_session_user", "test_session_pass")
        response = self.url_open(
            "/mcp/auth/validate", headers={"Accept": "application/json"}
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.text)
        self.assertTrue(data["success"])
        self.assertEqual(data["data"]["user_id"], self.test_user.id)
        self.assertEqual(data["data"]["auth_method"], "session")

    def test_03_session_auth_on_system_info(self):
        """Session-authenticated user can access /mcp/system/info."""
        self.authenticate("test_session_user", "test_session_pass")
        response = self.url_open(
            "/mcp/system/info", headers={"Accept": "application/json"}
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.text)
        self.assertTrue(data["success"])

    def test_04_no_auth_returns_401(self):
        """Request without API key or session returns 401."""
        response = self.url_open("/mcp/models", headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 401)

    def test_05_api_key_takes_priority(self):
        """When both API key and session present, API key is used."""
        self.authenticate("test_session_user", "test_session_pass")
        response = self.url_open(
            "/mcp/auth/validate",
            headers={
                "X-API-Key": self.api_key,
                "Accept": "application/json",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.text)
        self.assertEqual(data["data"]["auth_method"], "api_key")

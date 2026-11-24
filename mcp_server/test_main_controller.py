import json

from odoo.tests import common, tagged

from ..controllers import rate_limiting, utils
from .test_helpers import create_test_user, grant_mcp_access


@tagged("much_unit", "post_install", "-at_install")
class TestMcpMainController(common.HttpCase):
    """Test cases for the MCP API Controller main endpoints"""

    def setUp(self):
        super().setUp()
        utils.clear_mcp_caches()

        # Create test user with unique login to avoid conflicts
        import time

        unique_id = str(int(time.time() * 1000))[-6:]  # Last 6 digits of timestamp
        login = f"mcp_test_user_{unique_id}"

        self.mcp_user = create_test_user(
            self.env, "MCP Test User", login, email=f"mcp_test_{unique_id}@example.com"
        )
        grant_mcp_access(self.mcp_user)

        # Generate API key
        env_as_user = self.env(user=self.mcp_user)
        self.api_key = env_as_user["res.users.apikeys"]._generate(
            "rpc", "Test MCP API Key"
        )

        # Create or get existing test enabled model for res.partner
        partner_model_id = self.env.ref("base.model_res_partner").id
        existing_model = (
            self.env["mcp.enabled.model"]
            .sudo()
            .search([("model_id", "=", partner_model_id)], limit=1)
        )

        if existing_model:
            # Update existing model to ensure correct permissions
            existing_model.write(
                {
                    "allow_read": True,
                    "allow_create": True,
                    "allow_write": True,
                    "allow_unlink": False,
                    "active": True,
                }
            )
            self.test_model = existing_model
        else:
            # Create new model
            self.test_model = (
                self.env["mcp.enabled.model"]
                .sudo()
                .create(
                    {
                        "model_id": partner_model_id,
                        "allow_read": True,
                        "allow_create": True,
                        "allow_write": True,
                        "allow_unlink": False,
                    }
                )
            )

        # Ensure res.users is NOT MCP-enabled for testing
        users_model_id = self.env.ref("base.model_res_users").id
        existing_users_model = (
            self.env["mcp.enabled.model"]
            .sudo()
            .search([("model_id", "=", users_model_id)])
        )
        if existing_users_model:
            existing_users_model.sudo().unlink()

        # Enable MCP globally
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.enabled", "True")

    def test_health_check_success(self):
        """Test health check endpoint when MCP is enabled"""
        response = self.url_open("/mcp/health")
        self.assertEqual(response.status_code, 200)

        data = json.loads(response.content.decode())
        self.assertTrue(data["success"])
        self.assertEqual(data["data"]["status"], "ok")
        self.assertIn("mcp_server_version", data["data"])

    def test_health_check_disabled(self):
        """Test health check when MCP is disabled"""
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.enabled", "False")

        response = self.url_open("/mcp/health")
        self.assertEqual(response.status_code, 503)

        data = json.loads(response.content.decode())
        self.assertFalse(data["success"])
        self.assertEqual(data["error"]["code"], "E503")

    def test_all_endpoints_disabled_when_mcp_disabled(self):
        """Test that all MCP endpoints return errors when MCP is globally disabled"""
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.enabled", "False")
        headers = {"X-API-Key": self.api_key}

        # Test system info
        response = self.url_open("/mcp/system/info", headers=headers)
        self.assertEqual(response.status_code, 503)
        data = json.loads(response.content.decode())
        self.assertFalse(data["success"])
        self.assertEqual(data["error"]["code"], "E503")
        self.assertIn("disabled globally", data["error"]["message"])

        # Test auth validate
        response = self.url_open("/mcp/auth/validate", headers=headers)
        self.assertEqual(response.status_code, 503)
        data = json.loads(response.content.decode())
        self.assertFalse(data["success"])
        self.assertEqual(data["error"]["code"], "E503")

        # Test get models
        response = self.url_open("/mcp/models", headers=headers)
        self.assertEqual(response.status_code, 503)
        data = json.loads(response.content.decode())
        self.assertFalse(data["success"])
        self.assertEqual(data["error"]["code"], "E503")

        # Test get model access
        response = self.url_open("/mcp/models/res.partner/access", headers=headers)
        self.assertEqual(response.status_code, 503)
        data = json.loads(response.content.decode())
        self.assertFalse(data["success"])
        self.assertEqual(data["error"]["code"], "E503")

    def test_system_info_success(self):
        """Test system info endpoint with valid API key"""
        headers = {"X-API-Key": self.api_key}
        response = self.url_open("/mcp/system/info", headers=headers)
        self.assertEqual(response.status_code, 200)

        data = json.loads(response.content.decode())
        self.assertTrue(data["success"])
        self.assertIn("db_name", data["data"])
        self.assertIn("odoo_version", data["data"])

    def test_system_info_no_api_key(self):
        """Test system info endpoint without API key"""
        response = self.url_open("/mcp/system/info")
        self.assertEqual(response.status_code, 401)

        data = json.loads(response.content.decode())
        self.assertFalse(data["success"])
        self.assertEqual(data["error"]["code"], "E401")

    def test_validate_auth_success(self):
        """Test auth validation with valid API key"""
        headers = {"X-API-Key": self.api_key}
        response = self.url_open("/mcp/auth/validate", headers=headers)
        self.assertEqual(response.status_code, 200)

        data = json.loads(response.content.decode())
        self.assertTrue(data["success"])
        self.assertTrue(data["data"]["valid"])
        self.assertEqual(data["data"]["user_id"], self.mcp_user.id)

    def test_validate_auth_invalid_key(self):
        """Test auth validation with invalid API key"""
        headers = {"X-API-Key": "invalid_key_123"}
        response = self.url_open("/mcp/auth/validate", headers=headers)
        self.assertEqual(response.status_code, 401)

    def test_get_models_success(self):
        """Test get models endpoint"""
        headers = {"X-API-Key": self.api_key}
        response = self.url_open("/mcp/models", headers=headers)
        self.assertEqual(response.status_code, 200)

        data = json.loads(response.content.decode())
        self.assertTrue(data["success"])
        self.assertIn("models", data["data"])

        # Check that our test model is included
        model_names = [model["model"] for model in data["data"]["models"]]
        self.assertIn("res.partner", model_names)

    def test_get_model_access_success(self):
        """Test get model access for enabled model"""
        headers = {"X-API-Key": self.api_key}
        response = self.url_open("/mcp/models/res.partner/access", headers=headers)
        self.assertEqual(response.status_code, 200)

        data = json.loads(response.content.decode())
        self.assertTrue(data["success"])
        self.assertEqual(data["data"]["model"], "res.partner")
        self.assertTrue(data["data"]["enabled"])
        self.assertIn("operations", data["data"])

        # Check operations are present
        operations = data["data"]["operations"]
        self.assertIn("read", operations)
        self.assertIn("create", operations)
        self.assertIn("write", operations)
        self.assertIn("unlink", operations)

    def test_get_model_access_invalid_model(self):
        """Test get model access with invalid model name"""
        headers = {"X-API-Key": self.api_key}
        response = self.url_open("/mcp/models/invalid-model!/access", headers=headers)
        self.assertEqual(response.status_code, 400)

        data = json.loads(response.content.decode())
        self.assertFalse(data["success"])
        self.assertEqual(data["error"]["code"], "E400")

    def test_get_model_access_nonexistent_model(self):
        """Test get model access for non-existent model"""
        headers = {"X-API-Key": self.api_key}
        response = self.url_open(
            "/mcp/models/nonexistent.model/access", headers=headers
        )
        self.assertEqual(response.status_code, 404)

        data = json.loads(response.content.decode())
        self.assertFalse(data["success"])
        self.assertEqual(data["error"]["code"], "E404")

    def test_get_model_access_not_enabled(self):
        """Test get model access for model not enabled for MCP"""
        headers = {"X-API-Key": self.api_key}
        # res.users should exist but not be MCP-enabled
        response = self.url_open("/mcp/models/res.users/access", headers=headers)
        self.assertEqual(response.status_code, 403)

        data = json.loads(response.content.decode())
        self.assertFalse(data["success"])
        self.assertEqual(data["error"]["code"], "E403")

    def test_mcp_disabled_globally(self):
        """Test endpoints return 503 when MCP is disabled globally"""
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.enabled", "False")
        headers = {"X-API-Key": self.api_key}

        # Test multiple endpoints return same error
        endpoints = [
            "/mcp/system/info",
            "/mcp/auth/validate",
            "/mcp/models",
            "/mcp/models/res.partner/access",
        ]

        for endpoint in endpoints:
            response = self.url_open(endpoint, headers=headers)
            self.assertEqual(response.status_code, 503)

            data = json.loads(response.content.decode())
            self.assertFalse(data["success"])
            self.assertEqual(data["error"]["code"], "E503")

    def test_rate_limited_call_advertises_retry_after(self):
        """A REST 429 carries the same Retry-After as the /mcp 429."""
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("mcp_server.enable_rate_limiting", "True")
        params.set_param("mcp_server.request_limit", "10")
        rate_limiting._api_limiter.clear()
        self.addCleanup(rate_limiting._api_limiter.clear)
        for _ in range(10):
            rate_limiting.record_api_request(self.mcp_user.id, self.env.cr.dbname)

        response = self.url_open("/mcp/models", headers={"X-API-Key": self.api_key})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "E429")
        self.assertEqual(
            response.headers.get("Retry-After"),
            str(rate_limiting.RATE_LIMIT_WINDOW_MINUTES * 60),
        )

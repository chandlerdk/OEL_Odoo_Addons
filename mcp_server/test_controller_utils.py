import io
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from werkzeug.exceptions import RequestEntityTooLarge

from odoo.tests import common, tagged

from ..controllers import utils
from .test_helpers import create_test_user


@tagged("much_unit", "post_install", "-at_install")
class TestMcpUtils(common.TransactionCase):
    """Test utilities module"""

    def setUp(self):
        super().setUp()
        utils.clear_mcp_caches()

        # Create test user with unique login to avoid conflicts
        import time

        unique_id = str(int(time.time() * 1000))[-6:]
        login = f"test_user_utils_{unique_id}"

        self.test_user = create_test_user(
            self.env, "Test User", login, email=f"test_utils_{unique_id}@example.com"
        )

        env_as_user = self.env(user=self.test_user)
        self.valid_api_key = env_as_user["res.users.apikeys"]._generate(
            "rpc", "Test Utils API Key"
        )

        # Enable MCP globally
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.enabled", "True")

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
                    "allow_write": False,
                    "allow_unlink": False,
                }
            )
            self.partner_enabled_model = existing_model
        else:
            self.partner_enabled_model = (
                self.env["mcp.enabled.model"]
                .sudo()
                .create(
                    {
                        "model_id": partner_model_id,
                        "allow_read": True,
                        "allow_create": True,
                        "allow_write": False,
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

    def test_clear_mcp_caches(self):
        """clear_mcp_caches invalidates the @ormcache backing the global switches.

        The three global switches are served from an ``@ormcache`` on
        ``mcp.enabled.model`` (cross-worker-invalidated like the per-model
        gates). This proves both halves: the value IS served from cache
        (a source change hidden from the ORM's own invalidation is NOT seen on
        the next call) AND ``clear_mcp_caches`` flushes it so the new value is
        read -- i.e. invalidation, not a TTL.
        """
        mock_request = MagicMock()
        mock_request.env = self.env
        with patch("odoo.addons.mcp_server.controllers.utils.request", mock_request):
            params = self.env["ir.config_parameter"].sudo()
            params.set_param("mcp_server.enabled", "True")
            utils.clear_mcp_caches()
            self.assertTrue(utils.is_mcp_enabled())  # primes the ormcache -> True

            # Force the underlying param read to report False WITHOUT going
            # through set_param (which would itself invalidate the ORM caches):
            # the primed True is still served, proving it is cached, not re-read.
            with patch.object(
                type(params), "get_param", return_value="False"
            ):
                self.assertTrue(utils.is_mcp_enabled())

                # clear_mcp_caches flushes the ormcache -> the next call re-reads
                # (now the patched False), proving invalidation, not a TTL.
                utils.clear_mcp_caches()
                self.assertFalse(utils.is_mcp_enabled())

    def test_sanitize_model_name_valid(self):
        """Test model name sanitization with valid names"""
        test_cases = [
            ("res.partner", "res.partner"),
            ("account.move", "account.move"),
            ("ir.ui.view", "ir.ui.view"),
        ]

        for input_name, expected in test_cases:
            with self.subTest(input_name=input_name):
                result = utils.sanitize_model_name(input_name)
                self.assertEqual(result, expected)

    def test_sanitize_model_name_invalid(self):
        """Test model name sanitization with invalid names"""
        invalid_names = [
            "res partner",  # space
            "res-partner",  # dash
            "",  # empty string
            None,  # None value
        ]

        for invalid_name in invalid_names:
            with self.subTest(invalid_name=invalid_name):
                with self.assertRaises(ValueError):
                    utils.sanitize_model_name(invalid_name)

    def test_is_mcp_enabled(self):
        """Test MCP global enable check"""
        mock_request = MagicMock()
        mock_request.env = self.env

        with patch("odoo.addons.mcp_server.controllers.utils.request", mock_request):
            # Test when enabled
            self.env["ir.config_parameter"].sudo().set_param(
                "mcp_server.enabled", "True"
            )
            utils.clear_mcp_caches()
            self.assertTrue(utils.is_mcp_enabled())

            # Test when disabled
            self.env["ir.config_parameter"].sudo().set_param(
                "mcp_server.enabled", "False"
            )
            utils.clear_mcp_caches()
            self.assertFalse(utils.is_mcp_enabled())

    def test_is_model_mcp_enabled(self):
        """Test model MCP enable check"""
        mock_request = MagicMock()
        mock_request.env = self.env

        with patch("odoo.addons.mcp_server.controllers.utils.request", mock_request):
            # Test enabled model
            self.assertTrue(utils.is_model_mcp_enabled(self.env, "res.partner"))

            # Test disabled model
            self.assertFalse(utils.is_model_mcp_enabled(self.env, "res.users"))

            # Test non-existent model
            self.assertFalse(utils.is_model_mcp_enabled(self.env, "fake.model"))

    def test_check_model_operation_allowed(self):
        """Test operation permission check"""
        mock_request = MagicMock()
        mock_request.env = self.env

        with patch("odoo.addons.mcp_server.controllers.utils.request", mock_request):
            # Test allowed operations on res.partner
            self.assertTrue(
                utils.check_model_operation_allowed(self.env, "res.partner", "read")
            )
            self.assertTrue(
                utils.check_model_operation_allowed(self.env, "res.partner", "create")
            )
            self.assertFalse(
                utils.check_model_operation_allowed(self.env, "res.partner", "write")
            )
            self.assertFalse(
                utils.check_model_operation_allowed(self.env, "res.partner", "unlink")
            )

            # Test invalid operation
            self.assertFalse(
                utils.check_model_operation_allowed(self.env, "res.partner", "invalid")
            )

    def test_get_enabled_models(self):
        """Test getting list of enabled models"""
        mock_request = MagicMock()
        mock_request.env = self.env

        with patch("odoo.addons.mcp_server.controllers.utils.request", mock_request):
            models = utils.get_enabled_models(self.env)

            # Should be a list
            self.assertIsInstance(models, list)

            # Should contain res.partner
            model_names = [m["model"] for m in models]
            self.assertIn("res.partner", model_names)

            # Each model should have required fields
            for model in models:
                self.assertIn("model", model)
                self.assertIn("name", model)

    def test_get_model_allowed_operations(self):
        """Test getting allowed operations for a model"""
        mock_request = MagicMock()
        mock_request.env = self.env

        with patch("odoo.addons.mcp_server.controllers.utils.request", mock_request):
            # Test enabled model
            ops = utils.get_model_allowed_operations(self.env, "res.partner")
            self.assertTrue(ops["read"])
            self.assertTrue(ops["create"])
            self.assertFalse(ops["write"])
            self.assertFalse(ops["unlink"])

            # Test disabled model
            ops = utils.get_model_allowed_operations(self.env, "res.users")
            self.assertEqual(ops, {})

    def test_map_method_to_operation(self):
        """Test XML-RPC method to operation mapping"""
        # Read operations
        self.assertEqual(utils.map_method_to_operation("search"), "read")
        self.assertEqual(utils.map_method_to_operation("search_read"), "read")
        self.assertEqual(utils.map_method_to_operation("fields_get"), "read")
        self.assertEqual(utils.map_method_to_operation("formatted_read_group"), "read")

        # Create operations
        self.assertEqual(utils.map_method_to_operation("create"), "create")
        self.assertEqual(utils.map_method_to_operation("copy"), "create")

        # Write operations
        self.assertEqual(utils.map_method_to_operation("write"), "write")
        self.assertEqual(utils.map_method_to_operation("toggle_active"), "write")
        self.assertEqual(utils.map_method_to_operation("message_post"), "write")

        # Unlink operations
        self.assertEqual(utils.map_method_to_operation("unlink"), "unlink")

        # Unknown method
        self.assertIsNone(utils.map_method_to_operation("unknown_method"))

    def test_check_mcp_access(self):
        """Test MCP access check for XML-RPC methods"""
        mock_request = MagicMock()
        mock_request.env = self.env

        with patch("odoo.addons.mcp_server.controllers.utils.request", mock_request):
            # Allowed operations
            self.assertTrue(utils.check_mcp_access(self.env, "res.partner", "search"))
            self.assertTrue(utils.check_mcp_access(self.env, "res.partner", "create"))

            # Disallowed operations
            self.assertFalse(utils.check_mcp_access(self.env, "res.partner", "write"))
            self.assertFalse(utils.check_mcp_access(self.env, "res.partner", "unlink"))

            # Private method
            self.assertFalse(
                utils.check_mcp_access(self.env, "res.partner", "_private_method")
            )

            # Non-existent model
            self.assertFalse(utils.check_mcp_access(self.env, "fake.model", "search"))

    def test_get_allowed_xmlrpc_methods(self):
        """Test getting list of allowed XML-RPC methods"""
        methods = utils.get_allowed_xmlrpc_methods()

        # Should be a list
        self.assertIsInstance(methods, list)

        # Should contain common methods
        self.assertIn("search", methods)
        self.assertIn("read", methods)
        self.assertIn("create", methods)
        self.assertIn("write", methods)
        self.assertIn("unlink", methods)

    def test_get_mcp_server_version(self):
        """Test getting MCP server version"""
        version = utils.get_mcp_server_version()

        # Should be a string
        self.assertIsInstance(version, str)

        # Should match version pattern
        import re

        self.assertTrue(re.match(r"^\d+\.\d+\.\d+\.\d+\.\d+$", version))

    def test_get_system_info(self):
        """Test getting system information"""
        mock_request = MagicMock()
        mock_request.env = self.env

        with patch("odoo.addons.mcp_server.controllers.utils.request", mock_request):
            info = utils.get_system_info(self.env)

            # Check required fields
            self.assertIn("db_name", info)
            self.assertIn("odoo_version", info)
            self.assertIn("language", info)
            self.assertIn("enabled_mcp_models", info)
            self.assertIn("mcp_server_version", info)
            self.assertIn("server_timezone", info)

            # Check types
            self.assertIsInstance(info["enabled_mcp_models"], int)
            self.assertTrue(info["enabled_mcp_models"] >= 0)

    def test_get_system_info_language_from_partner_default(self):
        """A user and a context without a language get the partner default."""
        self.env["ir.default"].set("res.partner", "lang", "en_US")
        self.env["ir.config_parameter"].sudo().set_param("base.language", "xx_XX")
        self.test_user.sudo().lang = False
        info = utils.get_system_info(self.env(user=self.test_user, context={}))
        self.assertEqual(info["language"], "en_US")

    def test_switches_invalidated_by_registry_clear_cache(self):
        """The global switches propagate cross-worker via registry.clear_cache.

        All three switches (enabled / oauth / origins) are served from an
        ``@ormcache`` on ``mcp.enabled.model``. A config write followed by
        ``registry.clear_cache()`` -- exactly what
        ``res.config.settings.set_values`` and
        ``mcp.enabled.model._invalidate_mcp_caches`` run, and what the DB
        signaling replays on OTHER workers -- yields the new value, proving the
        kill-switch takes effect on invalidation rather than after a TTL.
        """
        mock_request = MagicMock()
        mock_request.env = self.env
        with patch("odoo.addons.mcp_server.controllers.utils.request", mock_request):
            params = self.env["ir.config_parameter"].sudo()
            params.set_param("mcp_server.enabled", "True")
            params.set_param("mcp_server.enable_oauth", "True")
            params.set_param("mcp_server.allowed_origins", "https://a.example.com")
            self.env.registry.clear_cache()
            self.assertTrue(utils.is_mcp_enabled())
            self.assertTrue(utils.is_oauth_enabled(self.env))
            self.assertEqual(
                utils.get_allowed_origins(), ("https://a.example.com",)
            )

            # Flip every switch and invalidate exactly the way the write path does.
            params.set_param("mcp_server.enabled", "False")
            params.set_param("mcp_server.enable_oauth", "False")
            params.set_param("mcp_server.allowed_origins", "https://b.example.com")
            self.env.registry.clear_cache()
            self.assertFalse(utils.is_mcp_enabled())
            self.assertFalse(utils.is_oauth_enabled(self.env))
            self.assertEqual(
                utils.get_allowed_origins(), ("https://b.example.com",)
            )


# Separate test class for auth and response utils functions
@tagged("much_unit", "post_install", "-at_install")
class TestAuthAndResponseUtils(common.TransactionCase):
    """Test authentication and response utilities"""

    def setUp(self):
        super().setUp()
        from ..controllers import auth, response_utils

        self.auth = auth
        self.response_utils = response_utils

        # Create test user
        import time

        unique_id = str(int(time.time() * 1000))[-6:]
        self.test_user = create_test_user(
            self.env,
            "Test Auth User",
            f"test_auth_{unique_id}",
            email=f"test_auth_{unique_id}@example.com",
        )

        env_as_user = self.env(user=self.test_user)
        self.valid_api_key = env_as_user["res.users.apikeys"]._generate(
            "rpc", "Test Auth API Key"
        )

    def test_get_user_from_api_key(self):
        """Test getting user from API key"""
        mock_request = MagicMock()
        mock_request.env = self.env

        # Patch ``request`` in both auth and audit: the invalid-key path writes an
        # auth-failure row via audit.write_audit_row, which reads its own module's
        # ``request`` proxy.
        with patch(
            "odoo.addons.mcp_server.controllers.auth.request", mock_request
        ), patch("odoo.addons.mcp_server.controllers.audit.request", mock_request):
            # Valid key
            user = self.auth.get_user_from_api_key(self.valid_api_key)
            self.assertEqual(user.id, self.test_user.id)

            # Invalid key
            user = self.auth.get_user_from_api_key("invalid_key")
            self.assertFalse(user)

    def test_validate_api_key(self):
        """Test validating API key from request"""
        mock_http_request = MagicMock()
        mock_http_request.httprequest.headers.get.return_value = self.valid_api_key

        mock_request = MagicMock()
        mock_request.env = self.env

        with patch("odoo.addons.mcp_server.controllers.auth.request", mock_request):
            user = self.auth.validate_api_key(mock_http_request)
            self.assertEqual(user.id, self.test_user.id)

    def test_require_auth_no_credentials_uses_throttled_logger(self):
        """The no-credential 401 path logs via the throttled _log_auth_failure.

        This branch must delegate to _log_auth_failure (which carries the per-IP
        throttle from _auth_failure_limiter and the shared cursor discipline)
        with the no-credential signature, rather than writing the auth-failure
        row directly and bypassing the throttle that guards every other
        legacy-caller failure.
        """
        mock_request = MagicMock()
        mock_request.env = self.env
        mock_request.httprequest.remote_addr = "127.0.0.1"
        # No X-API-Key header presented -> the branch logs the generic failure
        # (a supplied-but-invalid key would instead be logged by
        # get_user_from_api_key and skip this branch).
        mock_request.httprequest.headers.get.return_value = None

        @self.auth.require_auth
        def _endpoint(**kwargs):
            return "reached-handler"

        with patch(
            "odoo.addons.mcp_server.controllers.auth.request", mock_request
        ), patch.object(
            self.auth, "validate_api_key", return_value=None
        ), patch.object(
            self.auth, "get_user_from_session", return_value=None
        ), patch.object(
            self.auth, "_log_auth_failure"
        ) as mock_log, patch(
            "odoo.addons.mcp_server.controllers.response_utils.error_response",
            return_value="401",
        ):
            result = _endpoint()

        # Handler not reached (401 returned) and the failure logged through the
        # throttled helper -- not written directly to mcp.log.
        self.assertEqual(result, "401")
        mock_log.assert_called_once_with(
            "No valid API key or session", api_key_used=False
        )

    def test_get_timestamp(self):
        """Test timestamp generation"""
        timestamp = self.response_utils.get_timestamp()
        # Should be able to parse as datetime
        parsed = datetime.fromisoformat(timestamp)
        self.assertIsInstance(parsed, datetime)

    def test_success_response(self):
        """Test success response format"""
        test_data = {"key": "value"}

        mock_request = MagicMock()
        mock_request.make_json_response = MagicMock()

        with patch(
            "odoo.addons.mcp_server.controllers.response_utils.request", mock_request
        ):
            self.response_utils.success_response(test_data)

            mock_request.make_json_response.assert_called_once()
            payload = mock_request.make_json_response.call_args[0][0]

            self.assertTrue(payload["success"])
            self.assertEqual(payload["data"], test_data)
            self.assertIn("timestamp", payload["meta"])

    def test_error_response(self):
        """Test error response format"""
        mock_request = MagicMock()
        mock_request.make_json_response = MagicMock()

        with patch(
            "odoo.addons.mcp_server.controllers.response_utils.request", mock_request
        ):
            self.response_utils.error_response("Test error", "E400", 400)

            mock_request.make_json_response.assert_called_once()
            payload = mock_request.make_json_response.call_args[0][0]
            status = mock_request.make_json_response.call_args.kwargs.get("status")

            self.assertFalse(payload["success"])
            self.assertEqual(payload["error"]["message"], "Test error")
            self.assertEqual(payload["error"]["code"], "E400")
            self.assertEqual(status, 400)


@tagged("much_unit", "post_install", "-at_install")
class TestReadBoundedBody(common.TransactionCase):
    def test_retried_request_gets_the_body_of_the_first_read(self):
        """A retried request reads the cached body, not the empty stream."""
        body = b'{"jsonrpc": "2.0"}'
        httprequest = SimpleNamespace(content_length=None, stream=io.BytesIO(body))
        self.assertEqual(utils.read_bounded_body(httprequest, 100), body)
        self.assertEqual(utils.read_bounded_body(httprequest, 100), body)
        with self.assertRaises(RequestEntityTooLarge):
            utils.read_bounded_body(httprequest, 4)

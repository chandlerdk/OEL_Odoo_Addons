import logging
import xmlrpc.client as xmlrpclib  # nosec
from unittest.mock import patch

import psycopg2
from passlib.exc import PasswordSizeError

from odoo.exceptions import (
    AccessDenied,
    AccessError,
    MissingError,
    RedirectWarning,
    UserError,
    ValidationError,
)
from odoo.tests.common import HttpCase, TransactionCase, tagged
from odoo.tools import mute_logger

from ..controllers import api, error_sanitizer
from ..controllers.api import (
    XMLRPC_FAULT_CODES,
    MCPObjectController,
    _generate_xmlrpc_fault,
)
from .test_helpers import create_test_user, grant_mcp_access

_logger = logging.getLogger(__name__)


@tagged("much_unit", "post_install", "-at_install")
class TestXMLRPCControllers(HttpCase):
    def test_generate_xmlrpc_fault(self):
        """Test the helper function that generates XML-RPC fault responses."""
        code = 400
        message = "Test error message"
        result = _generate_xmlrpc_fault(code, message)

        self.assertIsInstance(result, str)
        self.assertIn("methodResponse", result)
        self.assertIn("fault", result)
        self.assertIn(str(code), result)
        self.assertIn(message, result)

    def test_xmlrpc_fault_codes_defined(self):
        """Test that all expected fault codes are defined."""
        expected_codes = {
            "bad_request": 400,
            "unauthorized": 401,
            "forbidden": 403,
            "not_found": 404,
            "rate_limit": 429,
            "internal_error": 500,
        }
        self.assertEqual(XMLRPC_FAULT_CODES, expected_codes)


@tagged("much_unit", "post_install", "-at_install")
class TestMCPCommonController(HttpCase):
    def setUp(self):
        super().setUp()
        from ..controllers import utils

        # Enable MCP globally by default
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.enabled", "True")
        utils.clear_mcp_caches()

    def test_common_controller_mcp_disabled(self):
        """Test XML-RPC common request when MCP is globally disabled."""
        from ..controllers import utils

        # Disable MCP globally
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.enabled", "False")
        utils.clear_mcp_caches()

        request_data = xmlrpclib.dumps((), "version", allow_none=1)
        response = self.url_open(
            "/mcp/xmlrpc/common",
            data=request_data,
            headers={"Content-Type": "text/xml"},
        )

        # xmlrpclib.loads raises a Fault exception for fault responses
        with self.assertRaises(xmlrpclib.Fault) as cm:
            xmlrpclib.loads(response.content)

        self.assertEqual(cm.exception.faultCode, 403)
        self.assertIn("MCP Server is disabled globally", cm.exception.faultString)

    @patch("odoo.service.common.dispatch")
    def test_common_controller_success(self, mock_dispatch):
        """Test successful XML-RPC common request."""
        mock_dispatch.return_value = {"server_version": "16.0"}
        request_data = xmlrpclib.dumps((), "version", allow_none=1)

        response = self.url_open(
            "/mcp/xmlrpc/common",
            data=request_data,
            headers={"Content-Type": "text/xml"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Content-Type"), "text/xml")

        result = xmlrpclib.loads(response.text)
        self.assertEqual(result[0][0], {"server_version": "16.0"})
        mock_dispatch.assert_called_once_with("version", ())

    @mute_logger("odoo.addons.mcp_server.controllers.api")
    @patch("odoo.service.common.dispatch")
    def test_common_controller_exception(self, mock_dispatch):
        """Test exception handling in common controller."""
        mock_dispatch.side_effect = Exception("Unexpected error")
        request_data = xmlrpclib.dumps((), "test_method", allow_none=1)

        response = self.url_open(
            "/mcp/xmlrpc/common",
            data=request_data,
            headers={"Content-Type": "text/xml"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Content-Type"), "text/xml")
        self.assertIn("fault", response.text)
        self.assertIn("500", response.text)


@tagged("much_unit", "post_install", "-at_install")
class TestMCPDatabaseController(HttpCase):
    def setUp(self):
        super().setUp()
        from ..controllers import utils

        # Enable MCP globally by default
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.enabled", "True")
        utils.clear_mcp_caches()

    def test_db_controller_mcp_disabled(self):
        """Test XML-RPC database request when MCP is globally disabled."""
        from ..controllers import utils

        # Disable MCP globally
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.enabled", "False")
        utils.clear_mcp_caches()

        request_data = xmlrpclib.dumps((), "list", allow_none=1)
        response = self.url_open(
            "/mcp/xmlrpc/db",
            data=request_data,
            headers={"Content-Type": "text/xml"},
        )

        # xmlrpclib.loads raises a Fault exception for fault responses
        with self.assertRaises(xmlrpclib.Fault) as cm:
            xmlrpclib.loads(response.content)

        self.assertEqual(cm.exception.faultCode, 403)
        self.assertIn("MCP Server is disabled globally", cm.exception.faultString)

    @patch("odoo.service.db.dispatch")
    def test_db_controller_success(self, mock_dispatch):
        """Test successful XML-RPC database request."""
        mock_dispatch.return_value = ["test_db1", "test_db2"]
        request_data = xmlrpclib.dumps((), "list", allow_none=1)

        response = self.url_open(
            "/mcp/xmlrpc/db", data=request_data, headers={"Content-Type": "text/xml"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Content-Type"), "text/xml")

        result = xmlrpclib.loads(response.text)
        self.assertEqual(result[0][0], ["test_db1", "test_db2"])
        mock_dispatch.assert_called_once_with("list", ())

    @mute_logger("odoo.addons.mcp_server.controllers.api")
    @patch("odoo.service.db.dispatch")
    def test_db_controller_exception(self, mock_dispatch):
        """Test exception handling in database controller."""
        mock_dispatch.side_effect = Exception("Database error")
        request_data = xmlrpclib.dumps((), "test_method", allow_none=1)

        response = self.url_open(
            "/mcp/xmlrpc/db", data=request_data, headers={"Content-Type": "text/xml"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Content-Type"), "text/xml")
        self.assertIn("fault", response.text)
        self.assertIn("500", response.text)

    def _db_fault(self, method="list"):
        request_data = xmlrpclib.dumps((), method, allow_none=1)
        response = self.url_open(
            "/mcp/xmlrpc/db", data=request_data, headers={"Content-Type": "text/xml"}
        )
        with self.assertRaises(xmlrpclib.Fault) as cm:
            xmlrpclib.loads(response.content)
        return cm.exception

    @mute_logger("odoo.addons.mcp_server.controllers.api")
    @patch("odoo.addons.mcp_server.controllers.api.db_service_root.dispatch")
    def test_db_controller_user_errors_keep_their_message(self, mock_dispatch):
        """An Odoo user error keeps its message and core's fault code, not a 500.

        The codes match core's ``/xmlrpc/2`` (2 user error, 3 refused login,
        4 access error), so XML-RPC clients classify them as business errors.
        """
        cases = [
            (UserError("Nope"), 2),
            (ValidationError("Bad value"), 2),
            (AccessError("Not yours"), 4),
            (MissingError("Gone"), 2),
            (AccessDenied(), 3),
        ]
        for exc, code in cases:
            with self.subTest(exc=type(exc).__name__):
                mock_dispatch.side_effect = exc
                fault = self._db_fault()
                self.assertEqual(fault.faultCode, code)
                self.assertNotIn("Internal", fault.faultString)
                self.assertIn(exc.args[0], fault.faultString)

    @mute_logger(
        "odoo.addons.mcp_server.controllers.api",
        "odoo.addons.mcp_server.controllers.error_sanitizer",
    )
    @patch("odoo.addons.mcp_server.controllers.api.db_service_root.dispatch")
    def test_db_controller_internal_error_is_generic(self, mock_dispatch):
        """An unexpected error returns the generic message, not its raw text."""
        mock_dispatch.side_effect = RuntimeError("secret detail in /opt/odoo")
        fault = self._db_fault()
        self.assertEqual(fault.faultCode, 500)
        self.assertEqual(fault.faultString, "Internal server error")


@tagged("much_unit", "post_install", "-at_install")
class TestMCPObjectController(HttpCase):
    def setUp(self):
        super().setUp()
        self.controller = MCPObjectController()
        from ..controllers import utils

        # Enable MCP globally by default
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.enabled", "True")
        utils.clear_mcp_caches()

    def test_object_controller_mcp_disabled(self):
        """Test XML-RPC object request when MCP is globally disabled."""
        from ..controllers import utils

        # Disable MCP globally
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.enabled", "False")
        utils.clear_mcp_caches()

        params = ("test_db", 1, "password", "res.users", "search", [])
        request_data = xmlrpclib.dumps(params, "execute_kw", allow_none=1)
        response = self.url_open(
            "/mcp/xmlrpc/object",
            data=request_data,
            headers={"Content-Type": "text/xml"},
        )

        # xmlrpclib.loads raises a Fault exception for fault responses
        with self.assertRaises(xmlrpclib.Fault) as cm:
            xmlrpclib.loads(response.content)

        self.assertEqual(cm.exception.faultCode, 403)
        self.assertIn("MCP Server is disabled globally", cm.exception.faultString)

    def test_object_controller_rate_limit_fault_advertises_retry_after(self):
        """The rate-limit fault (429) carries the same Retry-After as /mcp."""
        from ..controllers import rate_limiting

        params = self.env["ir.config_parameter"].sudo()
        params.set_param("mcp_server.enable_rate_limiting", "True")
        params.set_param("mcp_server.request_limit", "10")
        rate_limiting._api_limiter.clear()
        self.addCleanup(rate_limiting._api_limiter.clear)
        user = create_test_user(
            self.env, "Rate Limit User", "rate_limit_user", password="rate_limit_pw"
        )
        grant_mcp_access(user)
        dbname = self.env.cr.dbname
        for _ in range(10):
            rate_limiting.record_api_request(user.id, dbname)

        def call(password):
            request_data = xmlrpclib.dumps(
                (dbname, user.id, password, "res.partner", "search_count", [[]]),
                "execute_kw",
                allow_none=1,
            )
            response = self.url_open(
                "/mcp/xmlrpc/object",
                data=request_data,
                headers={"Content-Type": "text/xml"},
            )
            with self.assertRaises(xmlrpclib.Fault) as cm:
                xmlrpclib.loads(response.content)
            return response, cm.exception.faultCode

        # The budget is charged only to a proven identity: a wrong password is
        # refused as such, and it uses up nothing.
        _response, code = call("wrong")
        self.assertEqual(code, 3)
        self.assertEqual(rate_limiting._api_limiter.count((dbname, user.id)), 10)
        response, code = call("rate_limit_pw")
        self.assertEqual(code, 429)
        self.assertEqual(
            response.headers.get("Retry-After"),
            str(rate_limiting.RATE_LIMIT_WINDOW_MINUTES * 60),
        )

    def test_object_controller_non_execute_kw_method(self):
        """Test rejection of non-execute_kw methods."""
        from ..controllers import utils

        # Ensure MCP is enabled
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.enabled", "True")
        utils.clear_mcp_caches()

        params = ("test_db", 1, "password", "res.users", "search")
        request_data = xmlrpclib.dumps(params, "invalid_method", allow_none=1)

        response = self.url_open(
            "/mcp/xmlrpc/object",
            data=request_data,
            headers={"Content-Type": "text/xml"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Content-Type"), "text/xml")
        self.assertIn("fault", response.text)
        self.assertIn("400", response.text)
        self.assertIn("Unsupported method", response.text)

    def test_object_controller_insufficient_params(self):
        """Test handling of insufficient parameters."""
        from ..controllers import utils

        # Ensure MCP is enabled
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.enabled", "True")
        utils.clear_mcp_caches()

        params = ("test_db", 1)  # Not enough params for execute_kw
        request_data = xmlrpclib.dumps(params, "execute_kw", allow_none=1)

        response = self.url_open(
            "/mcp/xmlrpc/object",
            data=request_data,
            headers={"Content-Type": "text/xml"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Content-Type"), "text/xml")
        self.assertIn("fault", response.text)
        self.assertIn("400", response.text)
        self.assertIn("Insufficient parameters", response.text)

    def test_mcp_object_dispatch_non_execute_kw(self):
        """Test _mcp_object_dispatch rejects non-execute_kw methods."""
        with self.assertRaises(xmlrpclib.Fault) as cm:
            self.controller._mcp_object_dispatch("invalid_method", [])

        self.assertEqual(cm.exception.faultCode, 400)
        self.assertIn("Unsupported method", cm.exception.faultString)
        # A caller without credentials chooses the name: it stays on one line.
        with self.assertRaises(xmlrpclib.Fault) as cm:
            self.controller._mcp_object_dispatch("forged\nline" + "x" * 200, [])
        self.assertNotIn("\n", cm.exception.faultString)
        self.assertNotIn("x" * 81, cm.exception.faultString)

    def test_mcp_object_dispatch_insufficient_params(self):
        """Test _mcp_object_dispatch validates parameter count."""
        with self.assertRaises(xmlrpclib.Fault) as cm:
            self.controller._mcp_object_dispatch("execute_kw", ("db", 1))

        self.assertEqual(cm.exception.faultCode, 400)
        self.assertIn("Insufficient parameters", cm.exception.faultString)

    @patch("odoo.addons.mcp_server.controllers.utils.sanitize_model_name")
    def test_mcp_object_dispatch_invalid_model_name(self, mock_sanitize):
        """Test _mcp_object_dispatch validates model names."""
        mock_sanitize.side_effect = ValueError("Invalid model name")
        params = ("test_db", 1, "password", "invalid.model", "search")

        with self.assertRaises(xmlrpclib.Fault) as cm:
            self.controller._mcp_object_dispatch("execute_kw", params)

        self.assertEqual(cm.exception.faultCode, 400)
        self.assertIn("Invalid model name", cm.exception.faultString)


@tagged("much_unit", "post_install", "-at_install")
class TestXmlrpcErrorMapping(TransactionCase):
    def test_redirect_warning_keeps_core_fault_code(self):
        """A RedirectWarning gets fault code 2 and its message, as in core."""
        error = RedirectWarning("Set up the company first.", 1, "Go")
        self.assertEqual(api._fault_code_for(error), api.RPC_FAULT_CODE_WARNING)
        self.assertEqual(api._audit_code_for(error), XMLRPC_FAULT_CODES["bad_request"])
        self.assertEqual(
            error_sanitizer.sanitize_exception(error), "Set up the company first."
        )

    def test_password_gate_masks_only_credential_errors(self):
        """A refused credential is AccessDenied; any other error propagates."""
        controller = MCPObjectController()
        with patch.object(api.auth, "_log_auth_failure") as log_failure:
            for error in (
                AccessDenied(),
                MissingError("gone"),
                PasswordSizeError(4096),
            ):
                with self.subTest(error=type(error).__name__), patch.object(
                    MCPObjectController, "_verify_uid_passwd", side_effect=error
                ), self.assertRaises(AccessDenied):
                    controller._gate_password_credential(2, "secret")
            with patch.object(
                MCPObjectController,
                "_verify_uid_passwd",
                side_effect=psycopg2.OperationalError("database down"),
            ), self.assertRaises(psycopg2.OperationalError):
                controller._gate_password_credential(2, "secret")
            # A credential that cannot be proven is refused before any check.
            with patch.object(MCPObjectController, "_verify_uid_passwd") as verify:
                for uid, password in (
                    (2, ""),
                    (2, None),
                    (2, 12345),
                    ("x", "secret"),
                    (float("inf"), "secret"),
                ):
                    with self.subTest(uid=uid, password=password), self.assertRaises(
                        AccessDenied
                    ):
                        controller._gate_password_credential(uid, password)
                verify.assert_not_called()
            # Every refused credential writes its auth-failure row.
            self.assertEqual(log_failure.call_count, 8)

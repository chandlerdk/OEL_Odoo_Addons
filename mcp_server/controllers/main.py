"""Main REST API controller for MCP Server."""

import functools
import logging
from datetime import datetime

from odoo import http
from odoo.http import request

from . import auth, response_utils, utils
from .rate_limiting import rate_limit

_logger = logging.getLogger(__name__)


def require_mcp_enabled(func):
    """Return a 503 when MCP is globally disabled, else run ``func``.

    Stacked *below* the auth/rate-limit decorators (innermost) so the check runs
    after authentication rather than revealing the disabled state to
    unauthenticated callers.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not utils.is_mcp_enabled():
            return response_utils.error_response(
                message="MCP Server is disabled globally.",
                code="E503",
                status=503,
            )
        return func(*args, **kwargs)

    return wrapper


class McpAPIController(http.Controller):

    # auth="none": public health probe (returns only liveness status + module
    # version, no user data); csrf=False: GET-only machine endpoint (no
    # session/state mutation).
    @http.route("/mcp/health", type="http", auth="none", methods=["GET"], csrf=False)
    @require_mcp_enabled
    def health_check(self, **kwargs):
        """Report MCP server status and version (no auth required)."""
        mcp_server_version = utils.get_mcp_server_version()
        data = {
            "status": "ok",
            "mcp_server_version": mcp_server_version,
        }
        return response_utils.success_response(data)

    @http.route(
        "/mcp/system/info", type="http", auth="public", methods=["GET"], csrf=False
    )
    @auth.require_api_key
    @rate_limit
    @require_mcp_enabled
    def system_info(self, **kwargs):
        """Return database and MCP server info (name, version, tz, model count)."""
        # Use the authenticated user's environment
        user = kwargs.get("user")
        if user:
            env = request.env(user=user.id)
        else:
            env = request.env

        system_info_data = utils.get_system_info(env)
        return response_utils.success_response(system_info_data)

    @http.route(
        "/mcp/auth/validate", type="http", auth="public", methods=["GET"], csrf=False
    )
    @auth.require_api_key
    @rate_limit
    @require_mcp_enabled
    def validate_auth(self, **kwargs):
        """Validate the API key; return validity and the associated user ID."""
        user = kwargs.get("user")
        if user:
            api_key = request.httprequest.headers.get("X-API-Key")
            auth_method = "api_key" if api_key else "session"
            data = {
                "valid": True,
                "user_id": user.id,
                "auth_method": auth_method,
            }
            return response_utils.success_response(data)
        else:
            # This case should ideally not be reached
            # if require_api_key works as expected
            return response_utils.error_response(
                "API key validation failed unexpectedly.", "E500", status=500
            )

    @http.route("/mcp/models", type="http", auth="public", methods=["GET"], csrf=False)
    @auth.require_api_key
    @rate_limit
    @require_mcp_enabled
    def get_models(self, **kwargs):
        """List all MCP-enabled models with technical and display names."""
        # Use the authenticated user's environment
        user = kwargs.get("user")
        if user:
            env = request.env(user=user.id)
        else:
            env = request.env

        enabled_models = utils.get_enabled_models(env)
        data = {"models": enabled_models}
        return response_utils.success_response(data)

    @http.route(
        "/mcp/models/<string:model>/access",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    @auth.require_api_key
    @rate_limit
    @require_mcp_enabled
    def get_model_access(self, model, **kwargs):
        """Report whether a model is MCP-enabled and its allowed operations."""
        start_time = datetime.now()

        # Use the authenticated user's environment
        user = kwargs.get("user")
        if user:
            env = request.env(user=user.id)
        else:
            env = request.env

        try:
            model_tech_name = utils.sanitize_model_name(model)
        except ValueError as e:
            return response_utils.error_response(
                message=str(e),
                code="E400",
                status=400,
            )

        # Check if the model itself exists in ir.model
        # to give a more specific error if not.
        ir_model = (
            env["ir.model"].sudo().search([("model", "=", model_tech_name)], limit=1)
        )
        if not ir_model:
            # Log error only if we have a valid environment
            error_message = f"Model '{model_tech_name}' not found in Odoo instance."
            if env and hasattr(env, "__getitem__"):
                env["mcp.log"].sudo().log_error(
                    error_message=error_message,
                    error_code="E404",
                    endpoint=request.httprequest.path,
                    model_name=model_tech_name,
                    operation="access",
                    user_id=user.id if user else None,
                    ip_address=request.httprequest.remote_addr,
                )
            return response_utils.error_response(
                message=error_message,
                code="E404",
                status=404,
            )

        is_enabled = utils.is_model_mcp_enabled(env, model_tech_name)

        if not is_enabled:
            error_message = f"Model '{model_tech_name}' is not enabled for MCP access."
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            # sudo: write to mcp.log regardless of user permissions
            env["mcp.log"].sudo().log_permission_denied(
                model_name=model_tech_name,
                operation="access",
                user_id=user.id if user else None,
                endpoint=request.httprequest.path,
                ip_address=request.httprequest.remote_addr,
                error_message=error_message,
            )
            return response_utils.error_response(
                message=error_message,
                code="E403",
                status=403,
            )

        operations = utils.get_model_allowed_operations(env, model_tech_name)

        duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
        env["mcp.log"].sudo().log_model_access(
            model_name=model_tech_name,
            operation="access",
            user_id=user.id if user else None,
            endpoint=request.httprequest.path,
            http_method=request.httprequest.method,
            duration_ms=duration_ms,
            ip_address=request.httprequest.remote_addr,
        )

        data = {
            "model": model_tech_name,
            "enabled": is_enabled,
            "operations": operations,
        }
        return response_utils.success_response(data)

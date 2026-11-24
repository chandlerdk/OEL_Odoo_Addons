"""Tests for user context (``initialize.instructions`` + ``get_current_context``).

Exercises the personalized context that spec-compliant MCP clients inject on
connect (the ``instructions`` field of the ``initialize`` result) and the
fallback ``get_current_context`` read tool served through ``POST /mcp``:

* ``initialize`` returns an ``instructions`` string naming the key owner and
  their timezone.
* ``tools/list`` advertises ``get_current_context`` with ``readOnlyHint: true``;
  ``tools/call`` returns the active company name; a user with two allowed
  companies sees both listed.
* A user with no timezone falls back to the fixed "UTC (user has no timezone
  set)" text without crashing.

Mirrors the HttpCase + ``_generate("rpc", ...)`` key-minting harness of
``test_mcp_protocol.py``.
"""

import json
import time
from unittest.mock import patch

from odoo.tests import common, tagged

from ..controllers import rate_limiting, utils
from .test_helpers import create_test_user, grant_mcp_access

# Must match mcp_server/controllers/mcp.py.
PREFERRED_PROTOCOL_VERSION = "2025-11-25"


@tagged("much_unit", "post_install", "-at_install")
class TestUserContext(common.HttpCase):
    """User context (instructions + get_current_context) on ``/mcp``."""

    def setUp(self):
        super().setUp()
        utils.clear_mcp_caches()
        # Reset the shared in-memory rate-limit cache for test independence.
        rate_limiting._api_limiter.clear()

        self.unique_id = str(int(time.time() * 1000))[-6:]

        # Key-owner user carrying an explicit timezone, whose context we assert.
        self.mcp_user = create_test_user(
            self.env,
            "MCP Context User",
            f"mcp_ctx_user_{self.unique_id}",
            email=f"mcp_ctx_{self.unique_id}@example.com",
            tz="Europe/Berlin",
        )
        grant_mcp_access(self.mcp_user)
        self.api_key = self._mint_key(self.mcp_user, "Context Key")

        # Enable MCP globally and drop any stale cached toggle value.
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.enabled", "True")
        utils.clear_mcp_caches()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _mint_key(self, user, name):
        """Mint an ``rpc``-scope API key for ``user``."""
        return self.env(user=user)["res.users.apikeys"]._generate("rpc", name)

    _DEFAULT_KEY = object()

    def _post_rpc(self, body, api_key=_DEFAULT_KEY):
        """POST a JSON-RPC ``body`` dict to ``/mcp`` with bearer auth."""
        headers = {"Content-Type": "application/json"}
        if api_key is self._DEFAULT_KEY:
            api_key = self.api_key
        if api_key is not None:
            headers["Authorization"] = f"Bearer {api_key}"
        return self.url_open("/mcp", data=json.dumps(body), headers=headers)

    def _rpc_result(self, method, params=None, api_key=_DEFAULT_KEY):
        """POST ``method`` and return the JSON-RPC ``result`` payload."""
        response = self._post_rpc(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
            api_key=api_key,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("error", payload, msg=payload.get("error"))
        return payload["result"]

    def _context_text(self, api_key=_DEFAULT_KEY):
        """Invoke ``tools/call get_current_context`` and return its text output."""
        result = self._rpc_result(
            "tools/call",
            {"name": "get_current_context", "arguments": {}},
            api_key=api_key,
        )
        self.assertFalse(result["isError"], msg=result)
        return result["content"][0]["text"]

    # ------------------------------------------------------------------
    # initialize.instructions
    # ------------------------------------------------------------------
    def test_initialize_instructions_name_user_and_timezone(self):
        """initialize returns instructions naming the key owner and timezone."""
        result = self._rpc_result(
            "initialize", {"protocolVersion": PREFERRED_PROTOCOL_VERSION}
        )
        instructions = result["instructions"]
        self.assertIsInstance(instructions, str)
        # The block names the connected user and their configured timezone.
        self.assertIn(self.mcp_user.display_name, instructions)
        self.assertIn("Europe/Berlin", instructions)

    # ------------------------------------------------------------------
    # tools/list + tools/call get_current_context
    # ------------------------------------------------------------------
    def test_tools_list_get_current_context_is_readonly(self):
        """tools/list advertises get_current_context with readOnlyHint true."""
        result = self._rpc_result("tools/list")
        tools = {tool["name"]: tool for tool in result["tools"]}
        self.assertIn("get_current_context", tools)
        annotations = tools["get_current_context"].get("annotations", {})
        self.assertIs(annotations.get("readOnlyHint"), True)

    def test_get_current_context_returns_active_company(self):
        """tools/call get_current_context returns the active company name."""
        text = self._context_text()
        self.assertIn(self.mcp_user.company_id.display_name, text)

    def test_get_current_context_lists_both_allowed_companies(self):
        """A user with two allowed companies sees both listed in the context."""
        company_a = self.mcp_user.company_id
        company_b = self.env["res.company"].create(
            {"name": f"MCP Second Co {self.unique_id}"}
        )
        multi_user = create_test_user(
            self.env,
            "MCP Multi Co User",
            f"mcp_multi_user_{self.unique_id}",
            email=f"mcp_multi_{self.unique_id}@example.com",
            company_id=company_a.id,
            company_ids=[(6, 0, [company_a.id, company_b.id])],
        )
        grant_mcp_access(multi_user)
        key = self._mint_key(multi_user, "Multi Co Key")

        text = self._context_text(api_key=key)
        self.assertIn(company_a.display_name, text)
        self.assertIn(company_b.display_name, text)

    # ------------------------------------------------------------------
    # Missing-timezone fallback
    # ------------------------------------------------------------------
    def test_missing_timezone_falls_back_to_utc_note(self):
        """A user with no timezone yields the fixed fallback note, no crash."""
        no_tz_user = create_test_user(
            self.env,
            "MCP No TZ User",
            f"mcp_notz_user_{self.unique_id}",
            email=f"mcp_notz_{self.unique_id}@example.com",
            tz=False,
        )
        grant_mcp_access(no_tz_user)
        key = self._mint_key(no_tz_user, "No TZ Key")

        # Both the fallback tool and initialize.instructions carry the note.
        text = self._context_text(api_key=key)
        self.assertIn("UTC (user has no timezone set)", text)

        instructions = self._rpc_result(
            "initialize",
            {"protocolVersion": PREFERRED_PROTOCOL_VERSION},
            api_key=key,
        )["instructions"]
        self.assertIn("UTC (user has no timezone set)", instructions)

    # ------------------------------------------------------------------
    # initialize.instructions fallback
    # ------------------------------------------------------------------
    def test_initialize_omits_instructions_when_context_build_fails(self):
        """initialize stays a valid result (no instructions key) if build raises."""
        # Patch the symbol _initialize actually calls (utils.build_user_context).
        with patch.object(
            utils, "build_user_context", side_effect=Exception("boom")
        ):
            result = self._rpc_result(
                "initialize", {"protocolVersion": PREFERRED_PROTOCOL_VERSION}
            )
        self.assertNotIn("instructions", result)
        # The handshake itself is unaffected.
        self.assertIn("serverInfo", result)
        self.assertIn("protocolVersion", result)

    # ------------------------------------------------------------------
    # get_current_context is not model-gated
    # ------------------------------------------------------------------
    def test_get_current_context_works_without_enabled_models(self):
        """get_current_context returns context even with no mcp.enabled.model rows."""
        # Global MCP switch stays ON (set in setUp); clear every enabled-model row.
        self.env["mcp.enabled.model"].sudo().search([]).unlink()
        utils.clear_mcp_caches()

        text = self._context_text()
        self.assertIn(self.mcp_user.display_name, text)

    # ------------------------------------------------------------------
    # Prompt-injection hardening: CR/LF in identity values are collapsed
    # ------------------------------------------------------------------
    def test_hostile_newlines_in_identity_are_collapsed(self):
        """CR/LF embedded in the user's name/login are collapsed, not injected.

        ``_one_line()`` collapses CR/LF in the interpolated identity values
        (display_name/login) before they enter the plain-text context block, so a
        crafted value cannot forge extra context lines (prompt injection into the
        caller's own session). The collapsed single-line forms appear; the raw
        newline-injected forms do not. (The module's UTC guidance block keeps its
        own newlines -- out of scope here; we assert only that the user VALUES are
        collapsed.)
        """
        hostile_user = create_test_user(
            self.env,
            "User\nWith\nNewlines",
            f"mcp_hostile_{self.unique_id}\ninjected",
            email=f"mcp_hostile_{self.unique_id}@example.com",
            tz="Europe/Berlin",
        )
        grant_mcp_access(hostile_user)
        key = self._mint_key(hostile_user, "Hostile Key")

        text = self._context_text(api_key=key)
        # The collapsed single-line forms appear...
        self.assertIn("User With Newlines", text)
        self.assertIn(f"mcp_hostile_{self.unique_id} injected", text)
        # ...and the raw newline-injected forms (which would forge context lines)
        # do not.
        self.assertNotIn("User\nWith\nNewlines", text)
        self.assertNotIn(f"mcp_hostile_{self.unique_id}\ninjected", text)

    # ------------------------------------------------------------------
    # Single-company user omits the "Allowed companies" line
    # ------------------------------------------------------------------
    def test_single_company_user_omits_allowed_companies_line(self):
        """A single-company user shows Active company but no Allowed companies line."""
        self.assertEqual(len(self.mcp_user.company_ids), 1)

        text = self._context_text()
        self.assertIn("Active company", text)
        self.assertNotIn("Allowed companies", text)

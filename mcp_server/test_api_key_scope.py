"""Tests for the dedicated ``mcp`` API-key scope.

Two auth surfaces are pinned here:

* ``auth.get_user_from_api_key`` now probes ``_check_credentials(scope="mcp")``
  before falling back to ``scope="rpc"``, so a key stored with scope ``mcp`` (or a
  legacy NULL-scope key) authenticates on ``POST /mcp``.
* The blast-radius promise: an ``mcp``-scoped key is NOT a general RPC key -- core
  ``_check_credentials(scope="rpc", ...)`` rejects it.

Plus the wizard plumbing that lets users mint such keys: the ``mcp_api_key_scope``
context flag flows through the overridden ``_generate`` (TransactionCase), and the
``scope_mode`` field on the standard "New API Key" wizard drives it through the
``@check_identity`` gate (HttpCase).

Mirrors the HttpCase + ``_generate`` key-minting harness of ``test_mcp_protocol``
and ``test_authentication``.
"""

import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from odoo.http import _request_stack
from odoo.tests import common, tagged

from ..controllers import auth, rate_limiting, utils
from .test_helpers import create_test_user, grant_mcp_access

# Must match mcp_server/controllers/mcp.py.
PREFERRED_PROTOCOL_VERSION = "2025-11-25"


@tagged("much_unit", "post_install", "-at_install")
class TestApiKeyScope(common.HttpCase):
    """``mcp``-scoped and NULL-scope keys at the ``/mcp`` bearer door."""

    def setUp(self):
        super().setUp()
        utils.clear_mcp_caches()
        rate_limiting._api_limiter.clear()

        unique_id = str(int(time.time() * 1000))[-6:]
        self.mcp_user = create_test_user(
            self.env,
            "MCP Scope User",
            f"mcp_scope_user_{unique_id}",
            email=f"mcp_scope_{unique_id}@example.com",
        )
        grant_mcp_access(self.mcp_user)

        env_as_user = self.env(user=self.mcp_user)
        # A key minted with the dedicated ``mcp`` scope...
        self.mcp_key = env_as_user["res.users.apikeys"]._generate(
            "mcp", "MCP Scoped Key"
        )
        # ...and a legacy NULL/global key (backward-compat wildcard).
        self.null_key = env_as_user["res.users.apikeys"]._generate(
            None, "Legacy Null Scope Key"
        )
        # ...and an explicit ``rpc``-scope key (general RPC access).
        self.rpc_key = env_as_user["res.users.apikeys"]._generate(
            "rpc", "RPC Scoped Key"
        )

        # Enable MCP globally and drop any stale cached toggle value.
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.enabled", "True")
        utils.clear_mcp_caches()

    def _post_rpc(self, body, api_key):
        """POST a JSON-RPC ``body`` dict to ``/mcp`` with a bearer key."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        return self.url_open("/mcp", data=json.dumps(body), headers=headers)

    def _get_rest(self, path, api_key):
        """GET a legacy REST ``path`` with an ``X-API-Key`` header."""
        return self.url_open(
            path, headers={"X-API-Key": api_key, "Accept": "application/json"}
        )

    def test_mcp_user_reads_only_own_api_keys(self):
        """MCP membership grants no read access to other users' key metadata."""
        self.env(user=self.env.ref("base.user_admin"))["res.users.apikeys"]._generate(
            "rpc", "Admin Key"
        )
        keys = self.env(user=self.mcp_user)["res.users.apikeys"].search([])
        self.assertTrue(keys)
        self.assertEqual(keys.user_id, self.mcp_user)

    def test_mcp_scoped_key_authenticates_on_rpc(self):
        """A key from ``_generate("mcp", ...)`` authenticates on ``/mcp``."""
        response = self._post_rpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": PREFERRED_PROTOCOL_VERSION},
            },
            api_key=self.mcp_key,
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotIn("error", body)
        self.assertEqual(
            body["result"]["protocolVersion"], PREFERRED_PROTOCOL_VERSION
        )

    def test_null_scope_key_still_authenticates_on_rpc(self):
        """Regression: a NULL-scope key keeps authenticating on ``/mcp``."""
        response = self._post_rpc(
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            api_key=self.null_key,
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotIn("error", body)
        self.assertEqual(body["result"], {})

    def test_mcp_key_is_not_a_general_rpc_key(self):
        """Blast-radius pin: the ``mcp`` key is rejected by ``scope='rpc'`` lookups.

        Core ``_check_credentials`` matches ``scope IS NULL OR scope = %s``; a key
        stored with scope ``mcp`` therefore resolves under an ``mcp`` probe but NOT
        under the ``rpc`` probe used by core bearer/XML-RPC auth.
        """
        apikeys = self.env["res.users.apikeys"].sudo()
        # Sanity: the mcp probe DOES resolve the key (the pin below is meaningful).
        self.assertTrue(
            apikeys._check_credentials(scope="mcp", key=self.mcp_key),
            "the mcp-scoped key must resolve under an mcp-scope lookup",
        )
        # The actual pin: it is NOT a general RPC key.
        self.assertFalse(
            apikeys._check_credentials(scope="rpc", key=self.mcp_key),
            "an mcp-scoped key must NOT authenticate under scope='rpc'",
        )

    def test_mcp_key_rejected_on_legacy_rest_route(self):
        """Blast-radius pin: an ``mcp`` key does NOT authenticate on the legacy
        ``X-API-Key`` REST routes.

        ``/mcp/models`` is gated by ``require_api_key`` -> ``validate_api_key`` ->
        ``get_user_from_api_key`` with the default ``allowed_scopes=("rpc",)``,
        which rejects an ``mcp``-scope key; with no session either, the route
        returns 401. This closes a metadata-disclosure hole: an ``mcp`` key must
        not leak the enabled-model list here.
        """
        response = self._get_rest("/mcp/models", self.mcp_key)
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.json()["success"])

    def test_rpc_and_null_keys_still_authenticate_on_legacy_rest_route(self):
        """Regression pin: ``rpc`` + NULL/global keys keep authenticating on the
        same legacy REST route the ``mcp`` key is now rejected on."""
        for label, key in (("rpc", self.rpc_key), ("null", self.null_key)):
            response = self._get_rest("/mcp/models", key)
            self.assertEqual(
                response.status_code,
                200,
                f"the {label} key must still authenticate on /mcp/models",
            )
            self.assertTrue(response.json()["success"])

    def test_helper_default_scope_rejects_mcp_key(self):
        """Unit pin on the shared helper: the default ``allowed_scopes=("rpc",)``
        does not resolve an ``mcp`` key, but widening to ``("mcp", "rpc")`` does.

        This is exactly the difference between the legacy callers (default) and
        the ``/mcp`` bearer door (widened).
        """
        mock_request = MagicMock()
        mock_request.env = self.env
        with patch(
            "odoo.addons.mcp_server.controllers.auth.request", mock_request
        ):
            self.assertFalse(
                auth.get_user_from_api_key(
                    self.mcp_key, allowed_scopes=("rpc",), log_failure=False
                ),
                "an mcp key must not resolve under the default rpc-only scope",
            )
            user = auth.get_user_from_api_key(
                self.mcp_key, allowed_scopes=("mcp", "rpc")
            )
            self.assertEqual(user.id, self.mcp_user.id)


@tagged("much_unit", "post_install", "-at_install")
class TestApiKeyScopeGenerate(common.TransactionCase):
    """The ``mcp_api_key_scope`` context flag flows through ``_generate``."""

    def _latest_key_scope(self):
        """Return the scope of the current user's most recently created key."""
        return (
            self.env["res.users.apikeys"]
            .sudo()
            .search(
                [("user_id", "=", self.env.user.id)], order="id desc", limit=1
            )
            .scope
        )

    def test_generate_with_context_flag_stores_mcp_scope(self):
        """``with_context(mcp_api_key_scope="mcp")._generate(None, ...)`` stores ``mcp``."""
        self.env["res.users.apikeys"].with_context(
            mcp_api_key_scope="mcp"
        )._generate(None, "ctx mcp key")
        self.assertEqual(self._latest_key_scope(), "mcp")

    def test_generate_without_context_flag_stores_null_scope(self):
        """Without the context flag, ``_generate(None, ...)`` stores a NULL scope."""
        self.env["res.users.apikeys"]._generate(None, "ctx null key")
        self.assertFalse(self._latest_key_scope())

    def test_generate_explicit_scope_arg_wins_over_context_flag(self):
        """An explicit ``scope`` arg overrides the ``mcp_api_key_scope`` context flag."""
        self.env["res.users.apikeys"].with_context(
            mcp_api_key_scope="mcp"
        )._generate("rpc", "explicit-wins")
        self.assertEqual(self._latest_key_scope(), "rpc")


@tagged("much_unit", "post_install", "-at_install")
class TestApiKeyScopeWizard(common.HttpCase):
    """The wizard ``make_key`` stores the scope chosen via ``scope_mode``.

    ``make_key`` is ``@check_identity``-gated and hard-requires an HTTP request, so
    there is no TransactionCase path. We push a minimal fake request onto the stack
    (mirroring core ``base/tests/test_res_users.py::test_revoke_all_devices``) and
    prime ``identity-check-last`` so the 10-minute gate passes and ``make_key`` runs
    directly -- no password round-trip through the identity wizard.
    """

    def setUp(self):
        super().setUp()
        unique_id = str(int(time.time() * 1000))[-6:]
        # Internal user: core ``check_access_make_key`` requires ``_is_internal()``.
        self.wizard_user = create_test_user(
            self.env,
            "MCP Key Wizard User",
            f"mcp_key_wizard_{unique_id}",
            email=f"mcp_key_wizard_{unique_id}@example.com",
            group_ids=[(6, 0, [self.env.ref("base.group_user").id])],
        )

    def _run_make_key(self, scope_mode):
        """Drive the wizard ``make_key`` as ``wizard_user``; return the stored scope."""
        # Prime the identity gate and provide the ``request`` attributes core
        # ``make_key`` -> ``_generate`` reads (session gate + REMOTE_ADDR log line).
        fake_req = SimpleNamespace(
            session={"identity-check-last": time.time()},
            env=self.env,
            httprequest=SimpleNamespace(environ={"REMOTE_ADDR": "127.0.0.1"}),
        )
        _request_stack.push(fake_req)
        self.addCleanup(_request_stack.pop)
        wizard = (
            self.env["res.users.apikeys.description"]
            .with_user(self.wizard_user)
            .create({"name": "Wizard Key", "scope_mode": scope_mode})
        )
        wizard.make_key()
        return (
            self.env["res.users.apikeys"]
            .sudo()
            .search(
                [("user_id", "=", self.wizard_user.id)], order="id desc", limit=1
            )
            .scope
        )

    def test_make_key_is_check_identity_gated(self):
        """The wizard ``make_key`` keeps the core ``@check_identity`` decorator.

        Core ``check_identity`` sets ``__has_check_identity = True`` on the wrapped
        function, and core ``run_check`` (res_users.py) resolves the target method
        via ``getattr`` on the model and asserts that flag before running it. Our
        override of ``make_key`` must stay decorated or that assertion would fail
        (and the identity gate would be silently dropped). Resolve ``make_key`` the
        same way ``run_check`` does and pin the flag, so a decorator removal fails
        here rather than only at runtime.
        """
        make_key = getattr(
            self.env["res.users.apikeys.description"], "make_key"
        )
        self.assertTrue(
            getattr(make_key, "__has_check_identity", False),
            "make_key must keep the @check_identity decorator",
        )

    def test_wizard_mcp_mode_stores_mcp_scope(self):
        """``scope_mode="mcp"`` -> the created key row has scope ``mcp``."""
        self.assertEqual(self._run_make_key("mcp"), "mcp")

    def test_wizard_global_mode_stores_null_scope(self):
        """``scope_mode="global"`` -> the created key row has a NULL scope."""
        self.assertFalse(self._run_make_key("global"))

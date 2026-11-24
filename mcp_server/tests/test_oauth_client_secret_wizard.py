"""Tests for the admin wizard that creates confidential OAuth clients.

An MCP Administrator creates a client with a secret, or gives a client that an
administrator created a new secret. The secret is shown once through the
table-less ``mcp.oauth.client.secret.show`` model and stored only as a hash.
"""

import time

from odoo.exceptions import AccessError, UserError
from odoo.tests import common, tagged
from odoo.tools import SQL

from .test_helpers import create_test_user, grant_mcp_access, users_groups_field
from .test_oauth import _sha256_hex

_TEAMS_REDIRECT = "https://teams.microsoft.com/api/platform/v1.0/oAuthRedirect"
_STRICT = "client_secret_basic client_secret_post"


@tagged("much_unit", "post_install", "-at_install")
class TestOAuthClientSecretWizard(common.TransactionCase):
    def setUp(self):
        super().setUp()
        unique_id = str(int(time.time() * 1000))[-6:]
        admin_group = self.env.ref("mcp_server.group_mcp_admin")
        self.admin = create_test_user(
            self.env,
            "MCP Wizard Admin",
            f"mcp_wizard_admin_{unique_id}",
            **{users_groups_field(self.env): [(6, 0, [admin_group.id])]},
        )
        self.plain = create_test_user(
            self.env, "MCP Wizard User", f"mcp_wizard_user_{unique_id}"
        )
        grant_mcp_access(self.plain)
        self.Wizard = self.env["mcp.oauth.client.secret.wizard"]

    def _wizard(self, user=None, **vals):
        values = {
            "client_name": "Teams Agent",
            "redirect_uris": _TEAMS_REDIRECT,
            "scope": "mcp",
            **vals,
        }
        return self.Wizard.with_user(user or self.admin).create(values)

    def _client_of(self, action):
        client_id = action["context"]["default_client_identifier"]
        return (
            self.env["mcp.oauth.client"]
            .sudo()
            .with_context(active_test=False)
            .search([("client_id", "=", client_id)])
        )

    def test_create_returns_show_action_with_secret(self):
        action = self._wizard()._create_confidential_client()
        self.assertEqual(action["res_model"], "mcp.oauth.client.secret.show")
        self.assertEqual(action["target"], "new")
        secret = action["context"]["default_client_secret"]
        self.assertTrue(secret)

        client = self._client_of(action)
        self.assertEqual(len(client), 1)
        self.assertEqual(client.created_via, "manual")
        self.assertEqual(client.token_endpoint_auth_method, _STRICT)
        self.assertEqual(client.client_secret_hash, _sha256_hex(secret))
        self.assertEqual(client.client_name, "Teams Agent")
        self.assertEqual(client.redirect_uris, _TEAMS_REDIRECT)
        self.assertEqual(client.scope, "mcp")
        self.assertTrue(client.check_client_secret(secret))

        # The show model reads both values from the action context.
        values = (
            self.env["mcp.oauth.client.secret.show"]
            .with_user(self.admin)
            .with_context(**action["context"])
            .default_get(["client_identifier", "client_secret"])
        )
        self.assertEqual(values["client_identifier"], client.client_id)
        self.assertEqual(values["client_secret"], secret)

    def test_secret_is_stored_nowhere_in_plaintext(self):
        wizard = self._wizard()
        action = wizard._create_confidential_client()
        secret = action["context"]["default_client_secret"]
        self.env.flush_all()
        for table in ("mcp_oauth_client", "mcp_oauth_client_secret_wizard"):
            self.env.cr.execute(
                SQL(
                    "SELECT count(*) FROM %s t WHERE t::text LIKE %s",
                    SQL.identifier(table),
                    f"%{secret}%",
                )
            )
            self.assertEqual(self.env.cr.fetchone()[0], 0, table)

    def test_redirect_uris_are_validated(self):
        too_many = "\n".join(f"https://host{i}.example.com/cb" for i in range(6))
        for uris in (
            "",
            "http://example.com/cb",
            "https://example.com/cb#fragment",
            "https://trusted.example.com@evil.example.com/cb",
            "https://example.com/" + "a" * 2100,
            too_many,
        ):
            with self.subTest(uris=uris[:60]), self.assertRaises(UserError):
                self._wizard(redirect_uris=uris)._create_confidential_client()

    def test_several_redirect_uris_one_per_line(self):
        uris = f"{_TEAMS_REDIRECT}\nhttp://localhost:8765/callback"
        client = self._client_of(
            self._wizard(redirect_uris=uris)._create_confidential_client()
        )
        self.assertEqual(
            client.redirect_uris.split(),
            [_TEAMS_REDIRECT, "http://localhost:8765/callback"],
        )

    def test_scope_and_name_are_validated(self):
        for vals in (
            {"scope": "mcp admin"},
            {"scope": "mcp offline_access"},
            {"scope": ""},
            {"client_name": ""},
            {"client_name": "x" * 256},
        ):
            with self.subTest(vals=vals), self.assertRaises(UserError):
                self._wizard(**vals)._create_confidential_client()

    def test_read_only_scope_is_accepted(self):
        client = self._client_of(
            self._wizard(scope="mcp:read")._create_confidential_client()
        )
        self.assertEqual(client.scope, "mcp:read")

    def test_non_admin_is_refused(self):
        with self.assertRaises(AccessError):
            self._wizard(user=self.plain)
        wizard = self._wizard()
        with self.assertRaises(AccessError):
            wizard.with_user(self.plain)._create_confidential_client()
        client = self._client_of(wizard._create_confidential_client())
        regenerate = self._wizard(client_id=client.id)
        with self.assertRaises(AccessError):
            regenerate.with_user(self.plain)._regenerate_client_secret()

    def test_regenerate_replaces_the_secret(self):
        action = self._wizard()._create_confidential_client()
        client = self._client_of(action)
        old_secret = action["context"]["default_client_secret"]

        action = self._wizard(client_id=client.id)._regenerate_client_secret()
        new_secret = action["context"]["default_client_secret"]
        self.assertEqual(
            action["context"]["default_client_identifier"], client.client_id
        )
        self.assertNotEqual(new_secret, old_secret)
        self.assertTrue(client.check_client_secret(new_secret))
        self.assertFalse(client.check_client_secret(old_secret))

    def test_secret_changes_are_logged_without_the_secret(self):
        logger = "odoo.addons.mcp_server.wizard.oauth_client_secret_wizard"
        with self.assertLogs(logger, "INFO") as logs:
            action = self._wizard()._create_confidential_client()
        client = self._client_of(action)
        with self.assertLogs(logger, "INFO") as more_logs:
            regenerated = self._wizard(client_id=client.id)._regenerate_client_secret()
        output = "\n".join(logs.output + more_logs.output)
        self.assertIn(client.client_id, output)
        self.assertIn("created", output)
        self.assertIn("regenerated", output)
        self.assertNotIn(action["context"]["default_client_secret"], output)
        self.assertNotIn(regenerated["context"]["default_client_secret"], output)

    def test_regenerate_is_refused_for_dcr_clients(self):
        """A DCR client belongs to the program that registered it."""
        client = (
            self.env["mcp.oauth.client"]
            .sudo()
            .create(
                {
                    "client_id": f"dcr-{time.time()}",
                    "redirect_uris": _TEAMS_REDIRECT,
                    "created_via": "dcr",
                    "token_endpoint_auth_method": _STRICT,
                }
            )
        )
        with self.assertRaises(UserError):
            self._wizard(client_id=client.id)._regenerate_client_secret()

    def test_actions_need_an_identity_check(self):
        """Both buttons ask for the password again; without HTTP they fail."""
        with self.assertRaises(UserError):
            self._wizard().action_create()
        client = self._client_of(self._wizard()._create_confidential_client())
        with self.assertRaises(UserError):
            self._wizard(client_id=client.id).action_regenerate()

    def test_client_found_by_name_and_by_client_id(self):
        """Search finds a client by its name and by its client ID."""
        client = (
            self.env["mcp.oauth.client"]
            .sudo()
            .create(
                {
                    "client_id": "probe-cid-4711",
                    "client_name": "Probe Search Client",
                    "redirect_uris": _TEAMS_REDIRECT,
                }
            )
        )
        clients = self.env["mcp.oauth.client"].with_user(self.admin)
        for term in ("Probe Search", "probe-cid-47"):
            with self.subTest(term=term):
                found = [row[0] for row in clients.name_search(term)]
                self.assertIn(client.id, found)

    def test_client_views_show_the_client_id(self):
        """The client list and form show the client ID."""
        for xmlid in (
            "mcp_server.mcp_oauth_client_view_list",
            "mcp_server.mcp_oauth_client_view_form",
        ):
            with self.subTest(view=xmlid):
                self.assertIn('name="client_id"', self.env.ref(xmlid).arch)

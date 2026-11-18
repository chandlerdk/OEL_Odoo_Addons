"""The 17.0.2.0.1 post-migration grants the MCP group by prior activity."""

import importlib.util
import os
import time
from datetime import datetime, timedelta

from odoo.tests import common, tagged

from .test_helpers import create_test_user, users_groups_field

_MIGRATION_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "migrations",
    "17.0.2.0.1",
    "post-migrate.py",
)


def _load_migration():
    """Load the post-migrate module by path (migrations/ is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "mcp_server_post_migrate_18_0_2_0_1", _MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged("much_unit", "post_install", "-at_install")
class TestMigrationGrant(common.TransactionCase):
    """migrate() grants group_mcp_user to active internal users with activity."""

    def setUp(self):
        super().setUp()
        self.group = self.env.ref("mcp_server.group_mcp_user")
        self.suffix = str(int(time.time() * 1000))[-6:]
        self.client = (
            self.env["mcp.oauth.client"]
            .sudo()
            .create(
                {
                    "client_id": f"mig-client-{self.suffix}",
                    "redirect_uris": "http://127.0.0.1:8765/callback",
                }
            )
        )

    def _user(self, tag, **kwargs):
        """Create an internal test user (default groups) unless overridden."""
        return create_test_user(
            self.env,
            f"Mig {tag}",
            f"mig_{tag}_{self.suffix}",
            email=f"mig_{tag}_{self.suffix}@example.com",
            **kwargs,
        )

    def _mcp_key(self, user):
        self.env(user=user)["res.users.apikeys"]._generate("mcp", "mig key")

    def _oauth_token(self, user):
        self.env["mcp.oauth.token"].sudo().create(
            {
                "access_token_hash": f"hash-{user.id}",
                "client": self.client.id,
                "user_id": user.id,
                "access_expires_at": datetime.now() + timedelta(hours=1),
            }
        )

    def _log(self, user, event_type, endpoint=None):
        self.env["mcp.log"].sudo().create(
            {"event_type": event_type, "user_id": user.id, "endpoint": endpoint}
        )

    def test_migrate_grants_by_activity_and_skips_the_rest(self):
        """Each activity source grants; failures, portal, archived, idle do not."""
        by_key = self._user("key")
        self._mcp_key(by_key)

        by_token = self._user("token")
        self._oauth_token(by_token)

        by_log = self._user("log")
        self._log(by_log, "model_access")

        # Every event type an UNAUTHENTICATED caller could plant on the
        # auth="none" XML-RPC proxy of 1.2, which wrote the raw client-supplied
        # uid into mcp.log.user_id. None of
        # them may become a grant, or the gate is self-bypassing: spray one
        # request per uid before the upgrade and be granted by it.
        spoofable = self.env["res.users"]
        for event_type in (
            "auth_failure",
            "permission_denied",
            "error",
            "rate_limit",
        ):
            user = self._user(f"spoof_{event_type}")
            self._log(user, event_type)
            spoofable += user

        portal = self._user(
            "portal",
            **{
                users_groups_field(self.env): [
                    (6, 0, [self.env.ref("base.group_portal").id])
                ]
            },
        )
        self._oauth_token(portal)

        archived = self._user("archived")
        self._oauth_token(archived)
        archived.active = False

        idle = self._user("idle")

        granted = by_key + by_token + by_log
        skipped = spoofable + portal + archived + idle
        for user in granted + skipped:
            self.assertNotIn(
                self.group,
                user[users_groups_field(self.env)],
                f"{user.login} starts without the group",
            )

        _load_migration().migrate(self.env.cr, "17.0.2.0.0")
        self.env.invalidate_all()

        for user in granted:
            self.assertIn(
                self.group,
                user[users_groups_field(self.env)],
                f"{user.login} has MCP activity and must be granted",
            )
        for user in skipped:
            self.assertNotIn(
                self.group,
                (user.with_context(active_test=False))[users_groups_field(self.env)],
                f"{user.login} must NOT be granted",
            )

    def test_migrate_grants_on_every_verified_event_type(self):
        """Each post-authentication event type is a valid activity signal.

        The probe is an allowlist, so a type added to mcp.log's selection but
        not to the query would silently stop granting. Pins all three.
        """
        users = {}
        for event_type in ("model_access", "write_operation", "resource_retrieval"):
            user = self._user(f"ok_{event_type}")
            self._log(user, event_type)
            users[event_type] = user

        _load_migration().migrate(self.env.cr, "17.0.2.0.0")
        self.env.invalidate_all()

        for event_type, user in users.items():
            self.assertIn(
                self.group,
                user[users_groups_field(self.env)],
                f"a {event_type} row must grant the group",
            )

    def test_migrate_is_idempotent(self):
        """A second run over an already-granted user is a no-op, not an error."""
        user = self._user("idem")
        self._mcp_key(user)
        migrate = _load_migration().migrate
        migrate(self.env.cr, "17.0.2.0.0")
        migrate(self.env.cr, "17.0.2.0.0")
        self.env.invalidate_all()
        self.assertIn(self.group, user[users_groups_field(self.env)])

    def test_migrate_skips_rows_of_the_1_2_xmlrpc_proxy(self):
        """1.2 logged model_access on its XML-RPC proxy for any uid: no grant.

        A row of a 1.2 REST route, which checked the API key, still grants.
        """
        by_xmlrpc = self._user("xmlrpc")
        self._log(by_xmlrpc, "model_access", "/mcp/xmlrpc/object")
        by_rest = self._user("rest")
        self._log(by_rest, "model_access", "/mcp/models/res.partner/access")

        _load_migration().migrate(self.env.cr, "17.0.1.2.0")
        self.env.invalidate_all()

        self.assertNotIn(self.group, by_xmlrpc[users_groups_field(self.env)])
        self.assertIn(self.group, by_rest[users_groups_field(self.env)])

    def test_migrate_moves_the_groups_out_of_the_hidden_category(self):
        """Version 1.2 hid both groups; after the update the user form shows them."""
        hidden = self.env.ref("base.module_category_hidden")
        admin_group = self.env.ref("mcp_server.group_mcp_admin")
        (self.group + admin_group).write({"category_id": hidden.id})

        _load_migration().migrate(self.env.cr, "17.0.1.2.0")

        self.assertFalse(self.group.category_id)
        self.assertFalse(admin_group.category_id)

from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import common, tagged

from .test_helpers import create_test_user


@tagged("much_unit", "post_install", "-at_install")
class TestMcpSecurity(common.TransactionCase):
    def setUp(self):
        super().setUp()

        # Create a user in the MCP User group
        self.mcp_user = create_test_user(
            self.env,
            "MCP User",
            "mcp_user",
            email="mcp_user@example.com",
            group_ids=[(6, 0, [self.env.ref("mcp_server.group_mcp_user").id])],
        )

        # Create a user in the MCP Admin group
        self.mcp_admin = create_test_user(
            self.env,
            "MCP Admin",
            "mcp_admin",
            email="mcp_admin@example.com",
            group_ids=[(6, 0, [self.env.ref("mcp_server.group_mcp_admin").id])],
        )

        # Create a regular user without MCP access
        self.regular_user = create_test_user(
            self.env,
            "Regular User",
            "regular_user",
            email="regular_user@example.com",
            group_ids=[(6, 0, [self.env.ref("base.group_user").id])],
        )

        # Check if the model already exists in database
        partner_model_id = self.env.ref("base.model_res_partner").id
        existing_model = (
            self.env["mcp.enabled.model"]
            .sudo()
            .search([("model_id", "=", partner_model_id)], limit=1)
        )

        if existing_model:
            self.test_model = existing_model
        else:
            # Create test enabled model
            self.test_model = (
                self.env["mcp.enabled.model"]
                .sudo()
                .create(
                    {
                        "model_id": partner_model_id,
                        "allow_read": True,
                        "allow_create": False,
                        "allow_write": False,
                        "allow_unlink": False,
                    }
                )
            )

    def test_mcp_admin_access(self):
        """Test that MCP Admin can read and modify MCP settings"""
        enabled_model = self.test_model.with_user(self.mcp_admin)

        self.assertTrue(
            enabled_model.model_id.name,
            "Admin should be able to read MCP enabled models",
        )

        # Find a model that isn't already enabled
        company_model_id = self.env.ref("base.model_res_company").id
        existing_model = (
            self.env["mcp.enabled.model"]
            .with_user(self.mcp_admin)
            .search([("model_id", "=", company_model_id)], limit=1)
        )

        if existing_model:
            # If it already exists, we'll just verify we can write to it
            existing_model.write({"allow_create": True})
            self.assertTrue(
                existing_model.allow_create,
                "Admin should be able to modify MCP enabled models",
            )
            new_model = existing_model
        else:
            new_model = (
                self.env["mcp.enabled.model"]
                .with_user(self.mcp_admin)
                .create(
                    {
                        "model_id": company_model_id,
                        "allow_read": True,
                    }
                )
            )
            self.assertTrue(
                new_model.id, "Admin should be able to create MCP enabled models"
            )

            enabled_model.with_user(self.mcp_admin).write({"allow_create": True})
            self.assertTrue(
                enabled_model.allow_create,
                "Admin should be able to modify MCP enabled models",
            )

        # Verify admin can unlink (only if we created it in this test)
        if not existing_model:
            new_model.with_user(self.mcp_admin).unlink()
            self.assertFalse(
                self.env["mcp.enabled.model"].search([("id", "=", new_model.id)]),
                "Admin should be able to delete MCP enabled models",
            )

    def test_mcp_user_access(self):
        """Test that MCP User can read but not modify MCP settings"""
        enabled_model = self.test_model.with_user(self.mcp_user)

        self.assertTrue(
            enabled_model.model_id.name,
            "User should be able to read MCP enabled models",
        )

        with self.assertRaises(
            AccessError, msg="User should not be able to create MCP enabled models"
        ):
            self.env["mcp.enabled.model"].with_user(self.mcp_user).create(
                {
                    "model_id": self.env.ref("base.model_res_company").id,
                    "allow_read": True,
                }
            )

        with self.assertRaises(
            AccessError, msg="User should not be able to modify MCP enabled models"
        ):
            enabled_model.with_user(self.mcp_user).write({"allow_create": True})

        with self.assertRaises(
            AccessError, msg="User should not be able to delete MCP enabled models"
        ):
            enabled_model.with_user(self.mcp_user).unlink()

    def test_regular_user_access(self):
        """Test that regular users have no access to MCP settings"""
        with self.assertRaises(
            AccessError,
            msg="Regular user should not be able to read MCP enabled models",
        ):
            # Access the attribute to trigger the access error
            _ = self.test_model.with_user(self.regular_user).model_id.name

    # ------------------------------------------------------------------
    # OAuth credential models must be read-only for group_mcp_admin.
    # A delegated MCP admin holding create/write on mcp.oauth.token (or the
    # authorization.code) could forge a bearer bound to any user and take over
    # their account via /mcp, bypassing consent/PKCE. The admin ACL is
    # therefore read-only; the only sanctioned state changes (token revoke,
    # client activate/deactivate) go through sudo() object actions.
    # ------------------------------------------------------------------
    def _a_client(self):
        """Create an OAuth client as sudo (the AS provisions these, not admins)."""
        return (
            self.env["mcp.oauth.client"]
            .sudo()
            .create({"client_id": "test-client", "active": True})
        )

    def test_mcp_admin_cannot_forge_oauth_token(self):
        """group_mcp_admin must not create/write/unlink OAuth tokens."""
        client = self._a_client()
        admin = self.env.ref("base.user_admin")
        Token = self.env["mcp.oauth.token"].with_user(self.mcp_admin)

        with self.assertRaises(AccessError, msg="admin must not create tokens"):
            Token.create(
                {
                    "access_token_hash": "deadbeef",
                    "user_id": admin.id,
                    "client": client.id,
                    "audience": "http://x/mcp",
                    "access_expires_at": fields.Datetime.now() + timedelta(hours=1),
                }
            )

        # An existing token (issued by the AS via sudo) must not be re-bindable
        # to another user, nor deleted, by a delegated admin over RPC.
        token = (
            self.env["mcp.oauth.token"]
            .sudo()
            .create(
                {
                    "access_token_hash": "cafe",
                    "user_id": self.regular_user.id,
                    "client": client.id,
                    "access_expires_at": fields.Datetime.now() + timedelta(hours=1),
                }
            )
        )
        with self.assertRaises(AccessError, msg="admin must not re-bind a token"):
            token.with_user(self.mcp_admin).write({"user_id": admin.id})
        with self.assertRaises(AccessError, msg="admin must not delete a token"):
            token.with_user(self.mcp_admin).unlink()

    def test_mcp_admin_cannot_forge_oauth_code(self):
        """group_mcp_admin must not create authorization codes."""
        client = self._a_client()
        admin = self.env.ref("base.user_admin")
        with self.assertRaises(AccessError, msg="admin must not create codes"):
            self.env["mcp.oauth.authorization.code"].with_user(self.mcp_admin).create(
                {
                    "code_hash": "deadbeef",
                    "user_id": admin.id,
                    "client": client.id,
                    "expires_at": fields.Datetime.now() + timedelta(minutes=1),
                }
            )

    def test_mcp_admin_can_still_revoke_token(self):
        """The sudo-routed admin revoke action still works despite the ACL."""
        client = self._a_client()
        token = (
            self.env["mcp.oauth.token"]
            .sudo()
            .create(
                {
                    "access_token_hash": "beef",
                    "user_id": self.regular_user.id,
                    "client": client.id,
                    "access_expires_at": fields.Datetime.now() + timedelta(hours=1),
                }
            )
        )
        token.with_user(self.mcp_admin).action_revoke()
        self.assertTrue(token.sudo().revoked, "admin revoke button must still work")

    def test_mcp_admin_can_toggle_client(self):
        """The sudo-routed client activate/deactivate actions still work."""
        client = self._a_client()
        client.with_user(self.mcp_admin).action_deactivate()
        self.assertFalse(client.sudo().active)
        client.with_user(self.mcp_admin).action_activate()
        self.assertTrue(client.sudo().active)

    def test_non_admin_cannot_revoke_token(self):
        """A non-MCP-admin must not reach the sudo-backed revoke.

        These actions are public methods that sudo-write; /web/dataset/call_kw
        (auth='user') invokes public methods with no ir.model.access pre-check,
        so the has_group guard on the real user is the only thing stopping any
        authenticated user from revoking arbitrary tokens.
        """
        client = self._a_client()
        token = (
            self.env["mcp.oauth.token"]
            .sudo()
            .create(
                {
                    "access_token_hash": "beef",
                    "user_id": self.regular_user.id,
                    "client": client.id,
                    "access_expires_at": fields.Datetime.now() + timedelta(hours=1),
                }
            )
        )
        with self.assertRaises(AccessError):
            token.with_user(self.regular_user).action_revoke()
        self.assertFalse(token.sudo().revoked, "non-admin revoke must not take effect")

    def test_non_admin_cannot_toggle_client(self):
        """A non-MCP-admin must not reach the sudo-backed client toggle."""
        client = self._a_client()
        with self.assertRaises(AccessError):
            client.with_user(self.regular_user).action_deactivate()
        with self.assertRaises(AccessError):
            client.with_user(self.regular_user).action_activate()
        self.assertTrue(client.sudo().active, "non-admin toggle must not take effect")

    def test_settings_menu_access(self):
        """Test MCP Admin can access settings menu but MCP User cannot"""
        # Try to find the MCP menu by ref first
        try:
            menu_root = self.env.ref("mcp_server.mcp_menu_technical")
        except ValueError:
            # Fallback to search
            menu_root = self.env["ir.ui.menu"].search([("name", "=", "MCP")], limit=1)

        if not menu_root:
            self.skipTest("Menu 'MCP' not found, skipping test")

        mcp_admin_group = self.env.ref("mcp_server.group_mcp_admin")
        mcp_user_group = self.env.ref("mcp_server.group_mcp_user")

        self.assertIn(
            mcp_admin_group,
            menu_root.groups_id,
            "Menu should be restricted to MCP Admin group",
        )

        # Even though mcp_admin implies mcp_user, the menu should only list mcp_admin
        self.assertNotIn(
            mcp_user_group,
            menu_root.groups_id,
            "Menu should NOT be directly accessible to MCP User group",
        )

        menu_as_admin = menu_root.with_user(self.mcp_admin)
        self.assertTrue(
            menu_as_admin._filter_visible_menus(),
            "MCP Admin should have access to the menu",
        )

        menu_as_user = menu_root.with_user(self.mcp_user)
        self.assertFalse(
            menu_as_user._filter_visible_menus(),
            "MCP User should NOT have access to the menu",
        )

    # ------------------------------------------------------------------
    # mcp.custom.tool is admin-only for ALL access (read included): a non-admin
    # gets no direct read/create/write/unlink. MCP discovery/display is
    # sudo-mediated in the controller (gated by the non-su _user_can_run()), so a
    # calling user never reads this model directly. A delegated non-admin must
    # not be able to read, define, alter, or remove a custom tool (which wraps a
    # server action executed for MCP callers).
    # ------------------------------------------------------------------
    def _a_code_action(self):
        """Create a Python Code server action (as admin) to back a custom tool."""
        return self.env["ir.actions.server"].create(
            {
                "name": "Echo (custom-tool security test)",
                "model_id": self.env.ref("base.model_res_partner").id,
                "state": "code",
                "code": "mcp['result'] = {'ok': True}",
            }
        )

    def test_non_admin_cannot_crud_custom_tool(self):
        """A base.group_user (non-admin) may not read/create/write/unlink."""
        action = self._a_code_action()
        Tool = self.env["mcp.custom.tool"]

        # Read is now admin-only: a non-admin has no direct read on the model.
        with self.assertRaises(
            AccessError, msg="non-admin must not read a custom tool"
        ):
            Tool.with_user(self.regular_user).search([])

        with self.assertRaises(
            AccessError, msg="non-admin must not create a custom tool"
        ):
            Tool.with_user(self.regular_user).create(
                {
                    "name": "sec_denied_create",
                    "description": "x",
                    "action_id": action.id,
                }
            )

        # An existing tool (created by an admin) must not be writable or
        # removable by a delegated non-admin over RPC.
        tool = Tool.create(
            {
                "name": "sec_denied_admin",
                "description": "x",
                "action_id": action.id,
            }
        )
        with self.assertRaises(
            AccessError, msg="non-admin must not write a custom tool"
        ):
            tool.with_user(self.regular_user).write({"description": "changed"})
        with self.assertRaises(
            AccessError, msg="non-admin must not unlink a custom tool"
        ):
            tool.with_user(self.regular_user).unlink()

    def test_delegated_admin_crud_grants_no_execution_capability(self):
        """Wiring a custom tool is a system-only boundary; the run gate still gates.

        A group_mcp_admin user WITHOUT base.group_system has no ir.actions.server
        read (base grants that ACL to group_system only), so the NON-su read
        check in _check_action_is_code DENIES it wiring any action -- a delegated
        admin cannot define a tool at all (there is no action id it may
        reference). For whoever CAN wire one (a system admin), _user_can_run()
        still re-gates execution non-su: defining a tool grants nobody the right
        to run it. The run-time gate remains the final boundary.
        """
        # Precondition: this admin is DELEGATED -- it must not hold base.group_system,
        # or the action-read denial below would not bite.
        self.assertFalse(
            self.mcp_admin.has_group("base.group_system"),
            "the MCP admin must lack base.group_system for this test to be meaningful",
        )

        # A code action whose Allowed Groups exclude the unentitled caller below.
        action = self.env["ir.actions.server"].create(
            {
                "name": "Echo (delegated-admin boundary test)",
                "model_id": self.env.ref("base.model_res_partner").id,
                "state": "code",
                "code": "mcp['result'] = {'ok': True}",
                "groups_id": [
                    (6, 0, [self.env.ref("mcp_server.group_mcp_admin").id])
                ],
            }
        )

        # (a) The delegated admin has NO ir.actions.server read, so the non-su
        # read check now DENIES wiring the action at create time.
        with self.assertRaises(
            ValidationError,
            msg="a delegated non-system admin must not wire a server action",
        ):
            self.env["mcp.custom.tool"].with_user(self.mcp_admin).create(
                {
                    "name": "delegated_admin_tool",
                    "description": "Wraps a group_system-only code action.",
                    "action_id": action.id,
                }
            )

        # ...and it likewise cannot re-point an existing (admin-created) tool's
        # action -- the same non-su read check fires on write.
        tool = self.env["mcp.custom.tool"].create(
            {
                "name": "delegated_admin_tool_existing",
                "description": "Admin-created tool wrapping the code action.",
                "action_id": action.id,
            }
        )
        other_action = self.env["ir.actions.server"].create(
            {
                "name": "Echo 2 (delegated-admin boundary test)",
                "model_id": self.env.ref("base.model_res_partner").id,
                "state": "code",
                "code": "mcp['result'] = {'ok': True}",
            }
        )
        with self.assertRaises(
            ValidationError,
            msg="a delegated non-system admin must not re-wire a tool's action",
        ):
            tool.with_user(self.mcp_admin).write({"action_id": other_action.id})

        # (b) For whoever CAN wire one (here the system env), the run-time gate is
        # still the boundary: a caller not entitled by _user_can_run() cannot run
        # it. regular_user is not in the action's Allowed Groups, so the non-su
        # gate is False and _run_tool raises -- wiring granted no run capability.
        self.assertFalse(
            tool.with_user(self.regular_user)._user_can_run(),
            "an unentitled caller must not pass the run gate",
        )
        with self.assertRaises(
            AccessError, msg="an unentitled caller must not be able to run the tool"
        ):
            tool.with_user(self.regular_user)._run_tool({})

    def test_is_readonly_requires_system_admin(self):
        """Only a system administrator may mark a custom tool read-only.

        is_readonly governs the OAuth mcp:read/mcp:write scope boundary, so it is
        a security-boundary setting: a delegated (non-system) MCP admin must not
        be able to set it, a system admin can, and is_readonly=False is fine for
        anyone. Exercised over the write path -- the create path for a delegated
        admin is already blocked by the action-read check.
        """
        self.assertFalse(
            self.mcp_admin.has_group("base.group_system"),
            "the MCP admin must lack base.group_system for this test to bite",
        )
        action = self._a_code_action()

        # A system admin (the superuser test env, in base.group_system) may
        # create a read-only tool.
        tool = self.env["mcp.custom.tool"].create(
            {
                "name": "sysadmin_readonly_tool",
                "description": "x",
                "action_id": action.id,
                "is_readonly": True,
            }
        )
        self.assertTrue(tool.is_readonly, "a system admin may mark a tool read-only")

        # A delegated (non-system) admin may NOT flip a tool read-only.
        with self.assertRaises(
            ValidationError,
            msg="a delegated admin must not mark a tool read-only",
        ):
            tool.with_user(self.mcp_admin).write({"is_readonly": True})

        # ...but writing is_readonly=False is fine for the delegated admin.
        writable = self.env["mcp.custom.tool"].create(
            {
                "name": "delegated_write_tool",
                "description": "x",
                "action_id": action.id,
                "is_readonly": False,
            }
        )
        writable.with_user(self.mcp_admin).write({"is_readonly": False})
        self.assertFalse(writable.is_readonly)

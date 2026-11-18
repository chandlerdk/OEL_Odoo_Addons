from odoo.tests.common import HttpCase, tagged

from .test_helpers import create_test_user

TEST_PASSWORD = "a_safe_test_password_for_mcp"  # nosec


@tagged("much_unit", "post_install", "-at_install")
class TestModelSelectionUI(HttpCase):
    """Test the MCP model selection UI."""

    def setUp(self):
        super().setUp()
        # Create a user with MCP admin rights
        create_test_user(
            self.env,
            "MCP Admin User",
            "mcp_admin",
            password=TEST_PASSWORD,
            group_ids=[(6, 0, [self.env.ref("mcp_server.group_mcp_admin").id])],
        )

    def test_model_list_view(self):
        """Test that the model list view loads correctly."""
        self.authenticate("mcp_admin", TEST_PASSWORD)

        response = self.url_open("/web#action=mcp_server.action_mcp_enabled_models")
        self.assertEqual(response.status_code, 200)

    def test_model_form_view(self):
        """Test that the model form view loads correctly."""
        self.authenticate("mcp_admin", TEST_PASSWORD)

        partner_model = self.env["ir.model"].search(
            [("model", "=", "res.partner")], limit=1
        )

        # Check if model already exists in mcp.enabled.model
        existing_model = self.env["mcp.enabled.model"].search(
            [("model_id", "=", partner_model.id)]
        )

        if not existing_model:
            # Create a test enabled model
            enabled_model = self.env["mcp.enabled.model"].create(
                {
                    "model_id": partner_model.id,
                    "allow_read": True,
                }
            )
        else:
            enabled_model = existing_model

        response = self.url_open(
            f"/web#id={enabled_model.id}&model=mcp.enabled.model&view_type=form"
        )
        self.assertEqual(response.status_code, 200)

    def test_model_selection_wizard_loads(self):
        """Test that the model selection wizard loads correctly."""
        self.authenticate("mcp_admin", TEST_PASSWORD)

        response = self.url_open(
            "/web#action=mcp_server.action_mcp_model_selection_wizard"
        )
        self.assertEqual(response.status_code, 200)

    def test_model_selection_wizard_action(self):
        """Test the action of the model selection wizard."""
        # Find models that are not enabled yet (e.g., res.company, res.users)
        company_model = self.env["ir.model"].search(
            [("model", "=", "res.company")], limit=1
        )
        user_model = self.env["ir.model"].search([("model", "=", "res.users")], limit=1)
        # Ensure the models exist
        self.assertTrue(company_model, "Test requires res.company model")
        self.assertTrue(user_model, "Test requires res.users model")
        models_to_enable = company_model + user_model

        # Ensure these models are not already enabled
        self.env["mcp.enabled.model"].search(
            [("model_id", "in", models_to_enable.ids)]
        ).unlink()

        # Create the wizard instance
        wizard = (
            self.env["mcp.model.selection.wizard"]
            .with_user(
                self.env.ref("mcp_server.group_mcp_admin").users[0]
            )  # Run as MCP Admin
            .create(
                {
                    "model_ids": [(6, 0, models_to_enable.ids)],
                    "allow_read": True,
                    "allow_create": True,
                    "allow_write": False,
                    "allow_unlink": False,
                }
            )
        )

        # Execute the wizard action
        result = wizard.action_enable_models()

        # Check the result (should close the wizard)
        self.assertEqual(result["type"], "ir.actions.act_window_close")

        # Verify that the models were enabled with the correct permissions
        enabled_company = self.env["mcp.enabled.model"].search(
            [("model_id", "=", company_model.id)]
        )
        self.assertTrue(enabled_company)
        self.assertTrue(enabled_company.allow_read)
        self.assertTrue(enabled_company.allow_create)
        self.assertFalse(enabled_company.allow_write)
        self.assertFalse(enabled_company.allow_unlink)

        enabled_user = self.env["mcp.enabled.model"].search(
            [("model_id", "=", user_model.id)]
        )
        self.assertTrue(enabled_user, "res.users model should be enabled by wizard")
        self.assertTrue(enabled_user.allow_read)
        self.assertTrue(enabled_user.allow_create)
        self.assertFalse(enabled_user.allow_write)
        self.assertFalse(enabled_user.allow_unlink)

    def test_toggle_model_access(self):
        """Test enabling and disabling models."""
        # Login as admin which should have MCP admin rights
        self.authenticate("admin", "admin")

        user_model = self.env["ir.model"].search([("model", "=", "res.users")], limit=1)

        # Check if model already exists in mcp.enabled.model (archived included:
        # the model_id is unique over all rows)
        existing_model = (
            self.env["mcp.enabled.model"]
            .with_context(active_test=False)
            .search([("model_id", "=", user_model.id)])
        )

        if not existing_model:
            # Create a test enabled model
            enabled_model = self.env["mcp.enabled.model"].create(
                {
                    "model_id": user_model.id,
                    "allow_read": True,
                    "active": True,
                }
            )
        else:
            enabled_model = existing_model
            # Ensure it's active for our test
            enabled_model.active = True

        # Test deactivating the model
        enabled_model.active = False
        self.assertFalse(enabled_model.active)

        # Verify model is not considered enabled for MCP
        self.assertFalse(
            self.env["mcp.enabled.model"].is_model_enabled("res.users"),
            "Deactivated model should not be enabled for MCP access",
        )

        # Test reactivating the model
        enabled_model.active = True
        self.assertTrue(enabled_model.active)

        # Verify model is now considered enabled for MCP
        self.assertTrue(
            self.env["mcp.enabled.model"].is_model_enabled("res.users"),
            "Reactivated model should be enabled for MCP access",
        )

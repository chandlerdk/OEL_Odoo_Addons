"""Tests for the 17.0.2.2.0 post-migration (custom tool name collisions)."""

import importlib.util
import os

from odoo.exceptions import ValidationError
from odoo.tests import common, tagged

_MIGRATION_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "migrations",
    "17.0.2.2.0",
    "post-migrate.py",
)


def _load_migration():
    """Load the post-migrate module by path (migrations/ is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "mcp_server_post_migrate_18_0_2_2_0", _MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged("much_unit", "post_install", "-at_install")
class TestMigrationCustomToolCollision(common.TransactionCase):
    """migrate() archives custom tools named like the new builtin tools."""

    def setUp(self):
        super().setUp()
        self.action = self.env["ir.actions.server"].create(
            {
                "name": "Collision Action",
                "model_id": self.env.ref("base.model_res_partner").id,
                "state": "code",
                "code": "mcp['result'] = 1",
            }
        )

    def _tool(self, name):
        """A custom tool renamed via SQL to bypass the (now stricter) constraint."""
        tool = self.env["mcp.custom.tool"].create(
            {
                "name": "placeholder_tool",
                "description": "x",
                "action_id": self.action.id,
            }
        )
        self.env.cr.execute(
            "UPDATE mcp_custom_tool SET name = %s WHERE id = %s", (name, tool.id)
        )
        tool.invalidate_recordset(["name"])
        return tool

    def test_constraint_rejects_new_builtin_names(self):
        with self.assertRaises(ValidationError):
            self.env["mcp.custom.tool"].create(
                {
                    "name": "read_attachment",
                    "description": "x",
                    "action_id": self.action.id,
                }
            )

    def test_migrate_archives_colliding_tools_only(self):
        colliding = self._tool("read_attachment")
        colliding_upload = self._tool("upload_attachment")
        harmless = self._tool("my_own_tool")
        # The module is loaded by path, so its logger carries the spec name.
        with self.assertLogs(
            "mcp_server_post_migrate_18_0_2_2_0", level="WARNING"
        ) as logs:
            _load_migration().migrate(self.env.cr, "17.0.2.1.2")
        self.assertFalse(colliding.active)
        self.assertFalse(colliding_upload.active)
        self.assertTrue(harmless.active)
        self.assertTrue(any("read_attachment" in line for line in logs.output))

    def test_migrate_is_idempotent(self):
        migration = _load_migration()
        migration.migrate(self.env.cr, "17.0.2.1.2")
        migration.migrate(self.env.cr, "17.0.2.2.0")

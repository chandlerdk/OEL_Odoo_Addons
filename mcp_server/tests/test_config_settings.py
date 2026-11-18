from unittest.mock import MagicMock, patch

from lxml import etree

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..controllers import utils
from .test_helpers import create_test_config_settings


@tagged("much_unit", "post_install", "-at_install")
class TestConfigSettings(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Settings = self.env["res.config.settings"]

    def test_create_and_save_settings(self):
        """Test that settings can be created and saved."""
        settings = create_test_config_settings(
            self.env,
            mcp_enabled=True,
            mcp_request_limit=100,
            mcp_enable_logging=True,
            mcp_enable_rate_limiting=True,
        )

        # Execute the "save" onchange
        settings.execute()

        # Check that the values were saved to the system parameters
        param_obj = self.env["ir.config_parameter"].sudo()
        self.assertEqual(param_obj.get_param("mcp_server.enabled"), "True")
        self.assertEqual(param_obj.get_param("mcp_server.request_limit"), "100")
        self.assertEqual(param_obj.get_param("mcp_server.enable_logging"), "True")
        self.assertEqual(param_obj.get_param("mcp_server.enable_rate_limiting"), "True")

    def test_default_values(self):
        """Test that default values are set correctly when not specified."""
        # Clear any existing parameters
        param_obj = self.env["ir.config_parameter"].sudo()
        params = [
            "mcp_server.enabled",
            "mcp_server.request_limit",
            "mcp_server.enable_logging",
            "mcp_server.enable_rate_limiting",
        ]
        for param in params:
            param_obj.set_param(param, "")

        # Create new settings with defaults
        settings = create_test_config_settings(self.env)

        # Check default values
        self.assertFalse(settings.mcp_enabled)
        self.assertEqual(settings.mcp_request_limit, 300)
        self.assertTrue(settings.mcp_enable_logging)
        self.assertFalse(settings.mcp_enable_rate_limiting)

    def test_unlimited_rate_limit_setting(self):
        """Test that request limit can be set to 0 for unlimited."""
        settings = self.Settings.create(
            {
                "mcp_request_limit": 0,  # 0 means unlimited
                "mcp_enable_rate_limiting": True,
            }
        )

        # Execute the "save" onchange
        settings.execute()

        # Check that the value was saved correctly
        param_obj = self.env["ir.config_parameter"].sudo()
        self.assertEqual(param_obj.get_param("mcp_server.request_limit"), "0")

    def test_max_batch_size_round_trip(self):
        """mcp_max_batch_size defaults to 100 and round-trips to the param."""
        param_obj = self.env["ir.config_parameter"].sudo()
        param_obj.set_param("mcp_server.max_batch_size", "")
        self.assertEqual(self.Settings.create({}).mcp_max_batch_size, 100)

        settings = self.Settings.create({"mcp_max_batch_size": 25})
        settings.execute()
        self.assertEqual(param_obj.get_param("mcp_server.max_batch_size"), "25")
        self.assertEqual(self.Settings.create({}).mcp_max_batch_size, 25)

    def test_link_ttl_hours_round_trip(self):
        """mcp_link_ttl_hours defaults to 1 and round-trips to the param."""
        param_obj = self.env["ir.config_parameter"].sudo()
        param_obj.set_param("mcp_server.link_ttl_hours", "")
        self.assertEqual(self.Settings.create({}).mcp_link_ttl_hours, 1)

        settings = self.Settings.create({"mcp_link_ttl_hours": 24})
        settings.execute()
        self.assertEqual(param_obj.get_param("mcp_server.link_ttl_hours"), "24")
        self.assertEqual(self.Settings.create({}).mcp_link_ttl_hours, 24)

    def test_max_field_length_round_trip(self):
        """mcp_max_field_length defaults to 2000 and round-trips to the param."""
        param_obj = self.env["ir.config_parameter"].sudo()
        param_obj.set_param("mcp_server.max_field_length", "")
        self.assertEqual(self.Settings.create({}).mcp_max_field_length, 2000)

        settings = self.Settings.create({"mcp_max_field_length": 500})
        settings.execute()
        self.assertEqual(param_obj.get_param("mcp_server.max_field_length"), "500")
        self.assertEqual(self.Settings.create({}).mcp_max_field_length, 500)

    def test_load_settings(self):
        """Test that settings are loaded from system parameters."""
        # Set some values in system parameters
        param_obj = self.env["ir.config_parameter"].sudo()
        param_obj.set_param("mcp_server.enabled", "False")
        param_obj.set_param("mcp_server.request_limit", "200")

        # Create settings and check they load from params
        settings = self.Settings.create({})

        # Check loaded values
        self.assertFalse(settings.mcp_enabled)
        self.assertEqual(settings.mcp_request_limit, 200)

    def test_switches_saved_off_stay_off(self):
        """A switch saved as off reads back off and stays off after a second save."""
        switches = {
            "mcp_enabled": "mcp_server.enabled",
            "mcp_enable_oauth": "mcp_server.enable_oauth",
            "mcp_enable_logging": "mcp_server.enable_logging",
            "mcp_enable_rate_limiting": "mcp_server.enable_rate_limiting",
        }
        param_obj = self.env["ir.config_parameter"].sudo()
        self.Settings.create(dict.fromkeys(switches, False)).execute()
        reloaded = self.Settings.create({})
        for name in switches:
            self.assertFalse(reloaded[name], name)
        reloaded.execute()
        for name, key in switches.items():
            self.assertEqual(param_obj.get_param(key), "False", name)

    def test_switch_stored_true_reads_on(self):
        """A switch whose parameter holds "True" reads back on."""
        param_obj = self.env["ir.config_parameter"].sudo()
        param_obj.set_param("mcp_server.enable_rate_limiting", "True")
        self.assertTrue(self.Settings.create({}).mcp_enable_rate_limiting)

    def test_set_values_flushes_mcp_cache(self):
        """Saving settings flushes the cache so the toggle applies immediately.

        ``utils.is_mcp_enabled`` serves the master switch from an ``@ormcache``.
        Saving the settings must invalidate it (``set_values`` calls
        ``registry.clear_cache()``, which also signals the other workers) so a
        kill-switch flip takes effect now -- and cross-worker -- rather than
        lingering on a stale cached value.
        """
        mock_request = MagicMock()
        mock_request.env = self.env

        with patch("odoo.addons.mcp_server.controllers.utils.request", mock_request):
            # Enable via Settings, then prime the cache with the enabled value.
            create_test_config_settings(self.env, mcp_enabled=True).execute()
            self.assertTrue(utils.is_mcp_enabled())

            # Disabling via Settings must flush the primed cache: without the
            # flush, is_mcp_enabled() would keep serving the stale True.
            create_test_config_settings(self.env, mcp_enabled=False).execute()
            self.assertFalse(utils.is_mcp_enabled())

    def test_direct_set_param_flushes_mcp_cache(self):
        """A DIRECT ir.config_parameter.set_param propagates to the cached
        global switches without a manual cache clear.

        ``_get_mcp_enabled`` / ``_get_oauth_enabled`` / ``_get_allowed_origins``
        are ``@ormcache``-d in the registry ``default`` pool. A plain
        ``set_param`` (shell, XML data, another module) bypasses the
        res.config.settings / mcp.enabled.model invalidation paths, but core
        ``set_param`` still writes through ir.config_parameter create/write/unlink
        -- each calling ``registry.clear_cache()``, which flushes the ``default``
        pool both in-process and cross-worker. This locks in that reliance: a
        kill-switch / OAuth-switch / Origin-allowlist change made the direct way
        takes effect on the next read.
        """
        param_obj = self.env["ir.config_parameter"].sudo()
        mock_request = MagicMock()
        mock_request.env = self.env

        with patch("odoo.addons.mcp_server.controllers.utils.request", mock_request):
            # master kill-switch (mcp_server.enabled, default off)
            param_obj.set_param("mcp_server.enabled", "False")
            self.assertFalse(utils.is_mcp_enabled())  # prime cache = False
            param_obj.set_param("mcp_server.enabled", "True")  # direct, no clear
            self.assertTrue(utils.is_mcp_enabled())
            param_obj.set_param("mcp_server.enabled", "False")
            self.assertFalse(utils.is_mcp_enabled())

            # OAuth front-door switch (mcp_server.enable_oauth, default on)
            param_obj.set_param("mcp_server.enable_oauth", "True")
            self.assertTrue(utils.is_oauth_enabled(self.env))  # prime cache = True
            param_obj.set_param("mcp_server.enable_oauth", "False")
            self.assertFalse(utils.is_oauth_enabled(self.env))
            param_obj.set_param("mcp_server.enable_oauth", "True")
            self.assertTrue(utils.is_oauth_enabled(self.env))

            # Origin allowlist (mcp_server.allowed_origins, default empty)
            param_obj.set_param("mcp_server.allowed_origins", "")
            self.assertEqual(utils.get_allowed_origins(), ())  # prime cache = ()
            param_obj.set_param("mcp_server.allowed_origins", "https://claude.ai")
            self.assertEqual(utils.get_allowed_origins(), ("https://claude.ai",))
            param_obj.set_param("mcp_server.allowed_origins", "")
            self.assertEqual(utils.get_allowed_origins(), ())

    def test_settings_ui_display(self):
        """Test that settings UI view loads correctly."""
        view_id = self.env.ref("mcp_server.res_config_settings_view_form_mcp")
        self.assertTrue(view_id, "Settings view not found")

        # Try to load the view to check for errors
        settings = self.Settings.create({})
        view = settings.get_view(view_id.id, "form")
        self.assertTrue(view, "Failed to load settings view")

    def test_every_mcp_setting_shows_its_help(self):
        """Each MCP setting and nested label shows the help text of its field."""
        view = self.env.ref("mcp_server.res_config_settings_view_form_mcp")
        arch = etree.fromstring(view.arch)
        fields = self.env["res.config.settings"]._fields
        settings = list(arch.iter("setting"))
        labels = [
            label
            for label in arch.iter("label")
            if (label.get("for") or "").startswith("mcp_")
        ]
        self.assertTrue(settings)
        self.assertTrue(labels)
        for setting in settings:
            with self.subTest(setting=setting.get("id")):
                field_name = setting.find("field").get("name")
                # The template extension draws the help link only for mcp_ fields.
                self.assertTrue(field_name.startswith("mcp_"))
                self.assertEqual(setting.get("title"), fields[field_name].help)
        for label in labels:
            with self.subTest(field=label.get("for")):
                link = label.getnext()
                self.assertIsNotNone(link)
                self.assertIn("o_doc_link", link.get("class") or "")
                self.assertEqual(link.get("title"), fields[label.get("for")].help)

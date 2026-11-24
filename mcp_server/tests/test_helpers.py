"""Test helpers for creating module-agnostic test data.

This module provides utilities to create test records that work regardless
of which Odoo modules are installed. It dynamically detects required fields
and provides safe defaults.
"""

import contextlib
import http.client
import json as _json
import logging
from urllib.parse import urlencode, urlsplit

from odoo.tests.common import TEST_CURSOR_COOKIE_NAME

_logger = logging.getLogger(__name__)


class UrlOpenCompatMixin:
    """v19-compatible ``url_open`` kwargs for the v18 ``HttpCase``.

    v19 extended ``HttpCase.url_open`` with ``json=`` (serialized body +
    ``Content-Type: application/json``) and ``params=`` (urlencoded query
    string); v18's signature has neither. Translating them here lets the
    HTTP tests use the richer kwargs regardless of version. Mix in FIRST:
    ``class TestFoo(UrlOpenCompatMixin, common.HttpCase)``.
    """

    def url_open(self, url, data=None, *, json=None, params=None, headers=None, **kwargs):
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params)
        if json is not None:
            data = _json.dumps(json)
            # Caller-supplied Content-Type wins, mirroring requests' json=.
            headers = {"Content-Type": "application/json", **(headers or {})}
        return super().url_open(url, data, headers=headers, **kwargs)

    @contextlib.contextmanager
    def allow_requests(self, browser=None, all_requests=False):
        """v19 ``HttpCase.allow_requests``: releases the test lock and rotates
        the request key for a request made outside ``url_open``. v18 has no
        test lock and ``http_request_key`` is the plain ``canonical_tag``, so a
        client only needs the ``TEST_CURSOR_COOKIE_NAME`` cookie -- nothing to
        set up or tear down here.
        """
        yield


def post_chunked(case, path, chunks, headers=None):
    """POST ``chunks`` as a chunked body (no Content-Length) from an HttpCase.

    Uses ``http.client``: the ``requests`` of the Odoo 17 image cannot send a
    chunked body. Returns ``(status, body bytes)``.
    """
    url = urlsplit(case.base_url())
    conn = http.client.HTTPConnection(url.hostname, url.port, timeout=30)
    headers = dict(headers or {})
    headers["Cookie"] = f"{TEST_CURSOR_COOKIE_NAME}={case.http_request_key}"
    try:
        conn.request(
            "POST", path, body=iter(chunks), headers=headers, encode_chunked=True
        )
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


class TestHelpers:
    """Utilities for creating test data that works with any installed modules."""

    _cache = {}

    @classmethod
    def get_required_defaults(cls, env, model_name):
        """Get required field defaults for a model.

        Args:
            env: Odoo environment
            model_name: Name of the model (e.g. 'res.partner')

        Returns:
            dict: Dictionary of field_name: default_value for required fields
        """
        cache_key = f"{env.cr.dbname}:{model_name}"
        if cache_key in cls._cache:
            return cls._cache[cache_key].copy()

        model = env[model_name]
        defaults = {}

        fields_info = model.fields_get()
        model_defaults = model.default_get(list(fields_info.keys()))

        for field_name, field_info in fields_info.items():
            # Skip computed, related, and readonly fields
            if (
                field_info.get("compute")
                or field_info.get("related")
                or field_info.get("readonly")
            ):
                continue

            if field_info.get("required"):
                # First check if there's a default value from default_get
                if field_name in model_defaults:
                    defaults[field_name] = model_defaults[field_name]
                    continue

                # Otherwise provide safe defaults based on field type
                field_type = field_info.get("type")

                if field_type == "selection":
                    # Use first available option
                    selection = field_info.get("selection", [])
                    if selection and isinstance(selection, list) and len(selection) > 0:
                        defaults[field_name] = selection[0][0]
                elif field_type == "boolean":
                    defaults[field_name] = False
                elif field_type in ("integer", "float", "monetary"):
                    defaults[field_name] = 0
                elif field_type in ("char", "text", "html"):
                    defaults[field_name] = ""
                elif field_type == "date":
                    defaults[field_name] = "2025-01-01"
                elif field_type == "datetime":
                    defaults[field_name] = "2025-01-01 00:00:00"
                elif field_type == "many2one":
                    # Skip: would need to create or find a related record
                    pass

        cls._cache[cache_key] = defaults.copy()
        return defaults

    @classmethod
    def create_test_partner(cls, env, name, **kwargs):
        """Create a test partner with all required fields filled.

        Args:
            env: Odoo environment
            name: Partner name
            **kwargs: Additional field values to override defaults

        Returns:
            res.partner: Created partner record
        """
        defaults = cls.get_required_defaults(env, "res.partner")

        values = defaults.copy()
        values["name"] = name
        values.update(kwargs)

        return env["res.partner"].create(values)

    @classmethod
    def create_test_user(cls, env, name, login, **kwargs):
        """Create a test user with all required fields filled.

        Args:
            env: Odoo environment
            name: User name
            login: User login
            **kwargs: Additional field values to override defaults

        Returns:
            res.users: Created user record
        """
        # Callers may pass the v19 field name ``group_ids``; translate it
        # to whatever this Odoo version calls the users->groups field.
        if "group_ids" in kwargs:
            kwargs[users_groups_field(env)] = kwargs.pop("group_ids")

        # Check if we need to handle autopost_bills
        partner_has_autopost_bills = False
        try:
            partner_fields = env["res.partner"]._fields
            if "autopost_bills" in partner_fields:
                partner_has_autopost_bills = True
        except (AttributeError, KeyError):
            # AttributeError: env["res.partner"] might not exist
            # KeyError: _fields might not be accessible
            _logger.debug("Could not access 'autopost_bills' field on res.partner")

        # If autopost_bills exists and we need to set it, create partner first
        if partner_has_autopost_bills and "partner_id" not in kwargs:
            # Create partner with autopost_bills set
            partner_vals = {
                "name": name,
                "email": kwargs.get("email", ""),
                "autopost_bills": "ask",  # Safe default
            }
            partner_defaults = cls.get_required_defaults(env, "res.partner")
            for key, value in partner_defaults.items():
                if key not in partner_vals:
                    partner_vals[key] = value

            partner = env["res.partner"].create(partner_vals)
            kwargs["partner_id"] = partner.id

        values = cls.get_required_defaults(env, "res.users")
        values["name"] = name
        values["login"] = login
        values.update(kwargs)

        return env["res.users"].create(values)


# Convenience functions
def get_required_defaults(env, model_name):
    """Get required field defaults for a model."""
    return TestHelpers.get_required_defaults(env, model_name)


def create_test_partner(env, name, **kwargs):
    """Create a test partner with all required fields filled."""
    return TestHelpers.create_test_partner(env, name, **kwargs)


def create_test_user(env, name, login, **kwargs):
    """Create a test user with all required fields filled."""
    return TestHelpers.create_test_user(env, name, login, **kwargs)


def users_groups_field(env):
    """Return the res.users groups field name for this Odoo version.

    v19 renamed the user->groups field ``groups_id`` -> ``group_ids``; detect it
    at runtime so the suite stays portable across the supported versions.
    """
    return "group_ids" if "group_ids" in env["res.users"]._fields else "groups_id"


def grant_mcp_access(*users):
    """ADD the MCP access group to ``users`` (keeps their existing groups).

    Every MCP door (native ``/mcp`` bearer, REST, XML-RPC proxy, OAuth
    authorize/token) requires membership in ``mcp_server.group_mcp_user``, so
    HTTP-driving fixtures grant it on top of whatever groups the test scenario
    itself is about. A ``(4, id)`` link -- never ``(6, 0, ...)`` -- so a fixture
    pinned to specific groups (bare internal, partner manager, ...) keeps its
    intended Odoo ACL surface.
    """
    for user in users:
        group = user.env.ref("mcp_server.group_mcp_user")
        user.write({users_groups_field(user.env): [(4, group.id)]})


def create_test_config_settings(env, **kwargs):
    """Create test config settings with all required fields filled.

    This is specifically designed to handle res.config.settings which
    can have required fields from many different modules.
    """
    Settings = env["res.config.settings"]

    fields_info = Settings.fields_get()
    defaults = Settings.default_get(list(fields_info.keys()))

    values = defaults.copy()

    # Add safe defaults for any missing required fields
    for field_name, field_info in fields_info.items():
        if field_info.get("required") and field_name not in values:
            field_type = field_info.get("type")

            # Skip computed, readonly and related fields: Odoo fills related
            # (company-backed) settings fields itself, and writing one can
            # trip a company constraint (e.g. account_price_include once
            # the company has posted invoices).
            if (
                field_info.get("compute")
                or field_info.get("readonly")
                or field_info.get("related")
            ):
                continue

            # Provide safe defaults based on field type
            if field_type == "boolean":
                values[field_name] = False
            elif field_type == "integer":
                values[field_name] = 0
            elif field_type == "float":
                values[field_name] = 0.0
            elif field_type == "char":
                values[field_name] = ""
            elif field_type == "text":
                values[field_name] = ""
            elif field_type == "selection":
                # Get the first available option
                selection = field_info.get("selection", [])
                if selection and isinstance(selection, list) and selection[0]:
                    values[field_name] = selection[0][0]
            elif field_type == "many2one":
                # Try to find a default record
                comodel = field_info.get("relation")
                if comodel:
                    try:
                        # Look for a default or first record
                        default_rec = env[comodel].search([], limit=1)
                        if default_rec:
                            values[field_name] = default_rec.id
                    except Exception as e:
                        _logger.debug(
                            f"Failed to find default record for many2one field "
                            f"'{field_name}' (comodel: {comodel}): {e}"
                        )

    values.update(kwargs)
    return Settings.create(values)

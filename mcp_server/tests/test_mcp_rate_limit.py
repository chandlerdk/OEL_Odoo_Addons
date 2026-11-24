"""Endpoint rate-limit enforcement on ``POST /mcp``.

Drives real JSON-RPC requests through the dispatcher to prove the per-request
limiter trips with an HTTP 429 + ``Retry-After`` header, exempts ``ping``,
persists a ``rate_limit`` audit row on the independent cursor, and is skipped
entirely when the ``mcp_server.enable_rate_limiting`` master switch is off.

Every request rides ``self.url_open`` so the HttpCase test-cursor travels with
it (needed for the audit's independent cursor).
"""

import json
import time

from odoo.tests import common, tagged

from ..controllers import rate_limiting, utils
from .test_helpers import create_test_user, grant_mcp_access


@tagged("much_unit", "post_install", "-at_install")
class TestMcpRateLimit(common.HttpCase):
    """Per-request rate limiting on the native ``/mcp`` endpoint."""

    def setUp(self):
        super().setUp()
        utils.clear_mcp_caches()
        rate_limiting._api_limiter.clear()

        unique_id = str(int(time.time() * 1000))[-6:]
        self.mcp_user = create_test_user(
            self.env,
            "MCP RateLimit User",
            f"mcp_rl_user_{unique_id}",
            email=f"mcp_rl_{unique_id}@example.com",
        )
        grant_mcp_access(self.mcp_user)
        self.api_key = self.env(user=self.mcp_user)["res.users.apikeys"]._generate(
            "rpc", "Rate Limit Key"
        )

        self.params = self.env["ir.config_parameter"].sudo()
        self._orig_limit = self.params.get_param("mcp_server.request_limit", "300")
        self._orig_enabled = self.params.get_param(
            "mcp_server.enable_rate_limiting", "True"
        )
        self.params.set_param("mcp_server.enabled", "True")
        self.params.set_param("mcp_server.enable_logging", "True")
        # Enable the limiter explicitly: it defaults to "False", so without this
        # the 429 test only passes on a DB where the flag was left on manually.
        self.params.set_param("mcp_server.enable_rate_limiting", "True")
        utils.clear_mcp_caches()

    def tearDown(self):
        self.params.set_param("mcp_server.request_limit", self._orig_limit)
        self.params.set_param("mcp_server.enable_rate_limiting", self._orig_enabled)
        rate_limiting._api_limiter.clear()
        super().tearDown()

    def _post_rpc(self, body):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        return self.url_open("/mcp", data=json.dumps(body), headers=headers)

    def _tools_list(self):
        return self._post_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    def test_endpoint_returns_429_when_over_limit(self):
        """``tools/list`` trips 429 (+ Retry-After) once the per-minute cap is hit.

        Asserts the limiter counts ``tools/list``, exempts ``ping`` (it still
        answers while over the limit), and persists a ``rate_limit`` audit row.
        """
        rate_limiting._api_limiter.clear()
        # The minimum enforced limit is 10, so the 11th *counting* request is the
        # first one refused (check-then-record: 10 recorded -> 11th check fails).
        self.params.set_param("mcp_server.request_limit", "10")
        utils.clear_mcp_caches()

        statuses = [self._tools_list().status_code for _ in range(11)]
        self.assertEqual(statuses[:10], [200] * 10, msg=statuses)
        self.assertEqual(statuses[10], 429, msg=statuses)

        over_limit = self._tools_list()
        self.assertEqual(over_limit.status_code, 429)
        # The back-off must be the full window (60s), not just any truthy value.
        self.assertEqual(
            over_limit.headers.get("Retry-After"),
            str(rate_limiting.RATE_LIMIT_WINDOW_MINUTES * 60),
            "a 429 response must advertise the window-length Retry-After header",
        )
        self.assertEqual(over_limit.json()["error"]["code"], -32000)

        # ``ping`` is exempt from the limiter -> still answered with 200.
        ping = self._post_rpc({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        self.assertEqual(ping.status_code, 200)
        self.assertEqual(ping.json()["result"], {})

        # The over-limit event is audited (independent committed cursor).
        self.env.invalidate_all()
        rows = (
            self.env["mcp.log"]
            .sudo()
            .search(
                [
                    ("event_type", "=", "rate_limit"),
                    ("user_id", "=", self.mcp_user.id),
                ]
            )
        )
        self.assertTrue(rows, "expected a rate_limit audit row for the user")

    def test_rate_limiting_disabled_skips_429(self):
        """With the master switch off, the 429 path is skipped entirely."""
        rate_limiting._api_limiter.clear()
        self.params.set_param("mcp_server.request_limit", "10")
        self.params.set_param("mcp_server.enable_rate_limiting", "False")
        utils.clear_mcp_caches()

        statuses = [self._tools_list().status_code for _ in range(15)]
        self.assertEqual(statuses, [200] * 15, msg=statuses)

    def test_unknown_notification_counts_against_limit(self):
        """A made-up ``notifications/*`` name is NOT exempt from the limiter.

        Only the spec-defined client->server notifications skip the cap; an
        arbitrary ``notifications/<x>`` must count, otherwise it is a free flood
        channel that still costs an auth lookup + audit write per request.
        """
        rate_limiting._api_limiter.clear()
        self.params.set_param("mcp_server.request_limit", "10")
        utils.clear_mcp_caches()

        bogus = {"jsonrpc": "2.0", "method": "notifications/bogus"}
        statuses = [self._post_rpc(bogus).status_code for _ in range(11)]
        # The 10 accepted acks return 202; the 11th counting request trips 429.
        self.assertEqual(statuses[:10], [202] * 10, msg=statuses)
        self.assertEqual(statuses[10], 429, msg=statuses)

    def test_request_without_id_counts_against_limit(self):
        """A request method sent without an id is not run, but still counts.

        Dropping the id must not open a free flood channel next to the
        unknown-notification one above.
        """
        rate_limiting._api_limiter.clear()
        self.params.set_param("mcp_server.request_limit", "10")
        utils.clear_mcp_caches()

        no_id = {"jsonrpc": "2.0", "method": "tools/list"}
        statuses = [self._post_rpc(no_id).status_code for _ in range(11)]
        self.assertEqual(statuses[:10], [202] * 10, msg=statuses)
        self.assertEqual(statuses[10], 429, msg=statuses)

    def test_spec_notification_is_exempt_from_limit(self):
        """``notifications/initialized`` is acked even while over the limit."""
        rate_limiting._api_limiter.clear()
        self.params.set_param("mcp_server.request_limit", "10")
        utils.clear_mcp_caches()

        init = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        statuses = [self._post_rpc(init).status_code for _ in range(15)]
        self.assertEqual(statuses, [202] * 15, msg=statuses)

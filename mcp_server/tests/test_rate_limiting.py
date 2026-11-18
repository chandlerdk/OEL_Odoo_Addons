import threading
import time
from unittest.mock import MagicMock, patch

from odoo.tests import common, tagged
from odoo.tools import mute_logger

from ..controllers import rate_limiting
from .test_helpers import create_test_user


@tagged("much_unit", "post_install", "-at_install")
class TestRateLimiting(common.TransactionCase):
    """Test rate limiting functionality"""

    def setUp(self):
        super().setUp()
        # Clear the rate limiting cache before each test
        rate_limiting._api_limiter.clear()
        # The rate-limit cache is namespaced by database; tests share one DB.
        self.dbname = self.env.cr.dbname

        # Create test user
        self.test_user = create_test_user(
            self.env,
            "Rate Limit Test User",
            "rate_limit_test_user",
            email="rate_limit_test@example.com",
        )

        # Set default rate limiting configuration
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.request_limit", "300"
        )
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.enable_rate_limiting", "True"
        )

    def test_get_request_limit_default(self):
        """Test getting default request limit"""
        mock_request = MagicMock()
        mock_request.env = self.env

        with patch(
            "odoo.addons.mcp_server.controllers.rate_limiting.request", mock_request
        ):
            limit = rate_limiting.get_request_limit()
            self.assertEqual(limit, 300)

    def test_get_request_limit_custom(self):
        """Test getting custom request limit"""
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.request_limit", "100"
        )

        mock_request = MagicMock()
        mock_request.env = self.env

        with patch(
            "odoo.addons.mcp_server.controllers.rate_limiting.request", mock_request
        ):
            limit = rate_limiting.get_request_limit()
            self.assertEqual(limit, 100)

    def test_get_request_limit_unlimited(self):
        """Test getting unlimited request limit (0)"""
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.request_limit", "0"
        )

        mock_request = MagicMock()
        mock_request.env = self.env

        with patch(
            "odoo.addons.mcp_server.controllers.rate_limiting.request", mock_request
        ):
            limit = rate_limiting.get_request_limit()
            self.assertEqual(limit, 0)  # Should return 0 for unlimited

    def test_get_request_limit_minimum_enforced(self):
        """Test that minimum request limit is enforced"""
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.request_limit", "5"
        )

        mock_request = MagicMock()
        mock_request.env = self.env

        with patch(
            "odoo.addons.mcp_server.controllers.rate_limiting.request", mock_request
        ):
            limit = rate_limiting.get_request_limit()
            self.assertEqual(limit, rate_limiting.MINIMUM_REQUEST_LIMIT)  # Should be 10

    @mute_logger("odoo.addons.mcp_server.controllers.rate_limiting")
    def test_get_request_limit_invalid_value(self):
        """Test handling of invalid request limit value"""
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.request_limit", "invalid"
        )

        mock_request = MagicMock()
        mock_request.env = self.env

        with patch(
            "odoo.addons.mcp_server.controllers.rate_limiting.request", mock_request
        ):
            limit = rate_limiting.get_request_limit()
            self.assertEqual(
                limit, rate_limiting.DEFAULT_REQUEST_LIMIT
            )  # Should fallback to 300

    def test_record_api_request(self):
        """Test recording API requests"""
        user_id = self.test_user.id

        # Record a request
        rate_limiting.record_api_request(user_id, self.dbname)

        # Check that request was recorded
        self.assertIn((self.dbname, user_id), rate_limiting._api_limiter._buckets)
        self.assertEqual(
            len(rate_limiting._api_limiter._buckets[(self.dbname, user_id)]), 1
        )

    def test_record_api_request_multiple(self):
        """Test recording multiple API requests"""
        user_id = self.test_user.id

        # Record multiple requests
        for _ in range(3):
            rate_limiting.record_api_request(user_id, self.dbname)

        # Check that all requests were recorded
        self.assertEqual(
            len(rate_limiting._api_limiter._buckets[(self.dbname, user_id)]), 3
        )

    def test_record_api_request_cleanup_old(self):
        """Test cleanup of old timestamps"""
        user_id = self.test_user.id

        # Add an old timestamp manually
        old_time = time.monotonic() - 120
        rate_limiting._api_limiter._buckets[(self.dbname, user_id)] = [old_time]

        # Record a new request (should clean up old one)
        rate_limiting.record_api_request(user_id, self.dbname)

        # Should only have the new request
        self.assertEqual(
            len(rate_limiting._api_limiter._buckets[(self.dbname, user_id)]), 1
        )

    def test_check_rate_limit_no_requests(self):
        """Test rate limit check with no previous requests"""
        user_id = self.test_user.id

        mock_request = MagicMock()
        mock_request.env = self.env

        with patch(
            "odoo.addons.mcp_server.controllers.rate_limiting.request", mock_request
        ):
            result = rate_limiting.check_rate_limit(user_id, self.dbname)
            self.assertTrue(result)  # Should be within limit

    def test_check_rate_limit_within_limit(self):
        """Test rate limit check within limit"""
        user_id = self.test_user.id

        # Record a few requests (well under limit)
        for _ in range(5):
            rate_limiting.record_api_request(user_id, self.dbname)

        mock_request = MagicMock()
        mock_request.env = self.env

        with patch(
            "odoo.addons.mcp_server.controllers.rate_limiting.request", mock_request
        ):
            result = rate_limiting.check_rate_limit(user_id, self.dbname)
            self.assertTrue(result)  # Should be within limit

    def test_check_rate_limit_exceeded(self):
        """Test rate limit check when limit is exceeded"""
        user_id = self.test_user.id

        # Set a limit just above minimum (10) for testing
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.request_limit", "12"
        )

        mock_request = MagicMock()
        mock_request.env = self.env

        with patch(
            "odoo.addons.mcp_server.controllers.rate_limiting.request", mock_request
        ):
            # Record requests exceeding the limit of 12
            for _ in range(13):  # More than the limit of 12
                rate_limiting.record_api_request(user_id, self.dbname)

            result = rate_limiting.check_rate_limit(user_id, self.dbname)
            self.assertFalse(result)  # Should exceed limit

    def test_check_rate_limit_old_requests_ignored(self):
        """Test that old requests don't count towards limit"""
        user_id = self.test_user.id

        # Set a limit above minimum (10) for testing
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.request_limit", "12"
        )

        # Add old timestamps that should be ignored
        old_time = time.monotonic() - 120
        rate_limiting._api_limiter._buckets[(self.dbname, user_id)] = [
            old_time
        ] * 15  # More than limit but old

        mock_request = MagicMock()
        mock_request.env = self.env

        with patch(
            "odoo.addons.mcp_server.controllers.rate_limiting.request", mock_request
        ):
            result = rate_limiting.check_rate_limit(user_id, self.dbname)
            self.assertTrue(result)  # Should be within limit (old requests ignored)

    def test_check_rate_limit_unlimited(self):
        """Test rate limit check with unlimited setting (0)"""
        user_id = self.test_user.id

        # Set unlimited (0)
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.request_limit", "0"
        )

        mock_request = MagicMock()
        mock_request.env = self.env

        with patch(
            "odoo.addons.mcp_server.controllers.rate_limiting.request", mock_request
        ):
            # Record many requests
            for _ in range(1000):  # Way more than any reasonable limit
                rate_limiting.record_api_request(user_id, self.dbname)

            result = rate_limiting.check_rate_limit(user_id, self.dbname)
            self.assertTrue(result)  # Should always pass with unlimited

    def test_rate_limit_decorator_enabled_within_limit(self):
        """Test rate limit decorator when enabled and within limit"""

        @rate_limiting.rate_limit
        def test_endpoint(user=None):
            return {"success": True, "user_id": user.id if user else None}

        mock_request = MagicMock()
        mock_request.env = self.env

        with patch(
            "odoo.addons.mcp_server.controllers.rate_limiting.request", mock_request
        ):
            result = test_endpoint(user=self.test_user)
            self.assertEqual(result["success"], True)
            self.assertEqual(result["user_id"], self.test_user.id)

    def test_rate_limit_decorator_disabled(self):
        """Test rate limit decorator when disabled"""
        # Disable rate limiting
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.enable_rate_limiting", "False"
        )

        @rate_limiting.rate_limit
        def test_endpoint(user=None):
            return {"success": True}

        mock_request = MagicMock()
        mock_request.env = self.env

        with patch(
            "odoo.addons.mcp_server.controllers.rate_limiting.request", mock_request
        ):
            result = test_endpoint(user=self.test_user)
            self.assertEqual(result["success"], True)

    def test_rate_limit_decorator_exceeded(self):
        """Test rate limit decorator when limit is exceeded"""
        # Set a limit above minimum (10) for testing
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.request_limit", "11"
        )

        @rate_limiting.rate_limit
        def test_endpoint(user=None):
            return {"success": True}

        mock_request = MagicMock()
        mock_request.env = self.env

        # Mock the error response to avoid request object issues
        mock_error_response = MagicMock(headers={})

        with patch(
            "odoo.addons.mcp_server.controllers.rate_limiting.request", mock_request
        ):
            with patch(
                "odoo.addons.mcp_server.controllers.response_utils.error_response",
                return_value=mock_error_response,
            ) as mock_err:
                # Fill the cache to exceed limit of 11
                for _ in range(12):
                    rate_limiting.record_api_request(self.test_user.id, self.dbname)

                result = test_endpoint(user=self.test_user)
                self.assertEqual(result, mock_error_response)
                # The rejection must surface as HTTP 429, not a silent 200,
                # with the window-length back-off hint.
                self.assertEqual(mock_err.call_args.kwargs.get("status"), 429)
                self.assertEqual(
                    result.headers["Retry-After"],
                    str(rate_limiting.RATE_LIMIT_WINDOW_MINUTES * 60),
                )

    def test_rate_limit_decorator_no_user(self):
        """Test rate limit decorator without user (anonymous)"""

        @rate_limiting.rate_limit
        def test_endpoint(user=None):
            return {"success": True}

        mock_request = MagicMock()
        mock_request.env = self.env

        with patch(
            "odoo.addons.mcp_server.controllers.rate_limiting.request", mock_request
        ):
            result = test_endpoint()  # No user provided
            self.assertEqual(result["success"], True)

            # Check that anonymous request was recorded
            self.assertIn(
                (self.dbname, -1), rate_limiting._api_limiter._buckets
            )  # Anonymous user ID is -1

    def test_rate_limit_decorator_anonymous_exceeded(self):
        """Test rate limit decorator for anonymous user when limit exceeded"""
        # Set limit above minimum (10) for testing
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.request_limit", "11"
        )

        @rate_limiting.rate_limit
        def test_endpoint():
            return {"success": True}

        mock_request = MagicMock()
        mock_request.env = self.env

        # Mock the error response to avoid request object issues
        mock_error_response = MagicMock(headers={})

        with patch(
            "odoo.addons.mcp_server.controllers.rate_limiting.request", mock_request
        ):
            with patch(
                "odoo.addons.mcp_server.controllers.response_utils.error_response",
                return_value=mock_error_response,
            ) as mock_err:
                # Fill anonymous cache to exceed limit of 11
                for _ in range(12):
                    rate_limiting.record_api_request(
                        -1, self.dbname
                    )  # Anonymous user ID

                result = test_endpoint()
                self.assertEqual(result, mock_error_response)
                # The rejection must surface as HTTP 429, not a silent 200,
                # with the window-length back-off hint.
                self.assertEqual(mock_err.call_args.kwargs.get("status"), 429)
                self.assertEqual(
                    result.headers["Retry-After"],
                    str(rate_limiting.RATE_LIMIT_WINDOW_MINUTES * 60),
                )

    def test_cache_persistence_across_calls(self):
        """Test that cache persists across multiple function calls"""
        user_id = self.test_user.id

        # Record requests in separate calls
        rate_limiting.record_api_request(user_id, self.dbname)
        rate_limiting.record_api_request(user_id, self.dbname)

        # Cache should contain both requests
        self.assertEqual(
            len(rate_limiting._api_limiter._buckets[(self.dbname, user_id)]), 2
        )

        # Check rate limit should see both requests
        mock_request = MagicMock()
        mock_request.env = self.env

        with patch(
            "odoo.addons.mcp_server.controllers.rate_limiting.request", mock_request
        ):
            # Should still be within default limit of 300
            result = rate_limiting.check_rate_limit(user_id, self.dbname)
            self.assertTrue(result)


@tagged("much_unit", "post_install", "-at_install")
class TestSlidingWindowLimiter(common.TransactionCase):
    """The shared sliding-window limiter must not leak keys.

    A ``defaultdict`` retains a bucket per distinct caller-key forever. On the
    IP-keyed limiters an attacker can present a fresh source address per request
    (trivially over IPv6), so single-shot keys must be swept, not accumulated.
    """

    def test_amortized_sweep_evicts_stale_one_shot_keys(self):
        """A once-per-window sweep drops buckets a distinct-key flood left behind."""
        limiter = rate_limiting.SlidingWindowLimiter(window_seconds=1)
        now = time.monotonic()
        stale = now - 10  # older than the 1s window
        for i in range(1000):
            limiter._buckets[("ip", i)] = [stale]
        self.assertEqual(len(limiter._buckets), 1000)

        # A call that has not crossed the window must NOT sweep -- sweeping on
        # every call would turn a distinct-key flood into O(n^2).
        limiter._last_sweep = time.monotonic()
        self.assertEqual(limiter.count(("ip", "probe")), 0)
        self.assertEqual(
            len(limiter._buckets),
            1000,
            "sweep must be amortized, not run on every call",
        )

        # Backdate the sweep clock past the window: the next hit triggers the
        # amortized cross-bucket sweep and evicts every stale bucket, leaving
        # only the freshly seen key.
        limiter._last_sweep = time.monotonic() - 10
        self.assertEqual(limiter.hit(("ip", "fresh")), 1)
        self.assertEqual(
            list(limiter._buckets),
            [("ip", "fresh")],
            "stale one-shot buckets must be swept, leaving only the live key",
        )

    def test_concurrent_hits_are_serialized_and_not_lost(self):
        """Under N-thread contention on one key, no hit is dropped.

        ``hit`` does a read-modify-write on the bucket list; without the lock two
        threads can read the same list and one thread's append is clobbered by
        the other's store. A wide window keeps every timestamp live for the whole
        test, so the final count must equal the total number of hits -- any
        deficit means the lock was dropped and increments were lost.
        """
        limiter = rate_limiting.SlidingWindowLimiter(window_seconds=3600)
        threads_n = 16
        hits_per_thread = 500
        key = ("ip", "shared")
        # Release every worker at once to maximise contention on the lock.
        barrier = threading.Barrier(threads_n)

        def hammer():
            barrier.wait()
            for _ in range(hits_per_thread):
                limiter.hit(key)

        threads = [threading.Thread(target=hammer) for _ in range(threads_n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(limiter.count(key), threads_n * hits_per_thread)

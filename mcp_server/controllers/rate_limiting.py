import functools
import logging
import threading
import time

from odoo.http import request

_logger = logging.getLogger(__name__)

DEFAULT_REQUEST_LIMIT = 300
MINIMUM_REQUEST_LIMIT = 10
RATE_LIMIT_WINDOW_MINUTES = 1


class SlidingWindowLimiter:
    """Thread-safe per-key sliding-window request counter.

    Backed by a PLAIN dict, keyed by an opaque caller key (a client IP, or a
    ``(dbname, user_id)`` tuple). A *lookup* must never insert a key, so reads go
    through ``dict.get(key, ())`` -- a ``defaultdict`` would mint (and then keep
    forever) a bucket for every distinct key merely observed.

    That distinction is load-bearing: callers key this by client IP on
    unauthenticated endpoints, and over IPv6 an attacker can present a fresh
    source address on every request, so a key seen exactly once must not linger.
    Every call prunes the *caller's own* stale timestamps; on top of that, at
    most once per window, an amortized cross-bucket sweep drops every bucket that
    holds no live entry. The sweep is deliberately NOT run on each call --
    sweeping on a distinct-key flood would be O(n^2); once-per-window keeps it
    O(1) amortized and bounds both memory and CPU (the DoS backstop must not
    itself become a memory-exhaustion vector).

    Timestamps are ``time.monotonic()`` seconds, so the window is immune to
    wall-clock jumps (NTP steps, DST).
    """

    def __init__(self, window_seconds):
        self._window = window_seconds
        self._buckets = {}
        self._lock = threading.Lock()
        self._last_sweep = time.monotonic()

    def _sweep_locked(self, now, cutoff):
        # Caller holds the lock. Amortized: only run once a full window has
        # elapsed since the last sweep, so single-shot keys cannot accumulate
        # while an every-call sweep is avoided.
        if now - self._last_sweep < self._window:
            return
        self._last_sweep = now
        self._buckets = {
            key: live
            for key, live in (
                (key, [t for t in stamps if t > cutoff])
                for key, stamps in self._buckets.items()
            )
            if live
        }

    def hit(self, key):
        """Record a request for ``key`` and return its in-window count."""
        with self._lock:
            now = time.monotonic()
            cutoff = now - self._window
            live = [t for t in self._buckets.get(key, ()) if t > cutoff]
            live.append(now)
            self._buckets[key] = live
            self._sweep_locked(now, cutoff)
            return len(live)

    def count(self, key):
        """Return ``key``'s in-window count WITHOUT recording a new request."""
        with self._lock:
            now = time.monotonic()
            cutoff = now - self._window
            live = [t for t in self._buckets.get(key, ()) if t > cutoff]
            self._sweep_locked(now, cutoff)
            return len(live)

    def is_limited(self, key, cap):
        """Check-then-record: whether ``key`` is at/over ``cap`` this window.

        A rejected caller (already at/over ``cap``) is NOT recorded -- only an
        accepted one hits the bucket -- so a flood cannot keep topping up its own
        window (which would re-scan an ever-longer list under the lock). The
        count and the record are separate locked steps, as befits a best-effort
        per-worker limiter: a race only lets a hair more through, never fewer.
        """
        if self.count(key) >= cap:
            return True
        self.hit(key)
        return False

    def clear(self):
        """Drop all buckets (used for test isolation)."""
        with self._lock:
            self._buckets.clear()
            self._last_sweep = time.monotonic()


# Per-user request counter, keyed by (dbname, user_id) so a bare uid in DB A
# never shares a quota bucket with the same uid in DB B in a multi-database
# worker process (cross-tenant 429s + misleading rate_limit audit). Still a
# per-worker in-memory store -- the cap is enforced per worker.
_api_limiter = SlidingWindowLimiter(RATE_LIMIT_WINDOW_MINUTES * 60)


def is_rate_limiting_enabled():
    """Whether the ``mcp_server.enable_rate_limiting`` master switch is on.

    Disabled by default. The limiter is an in-memory per-worker counter, so it
    is best-effort abuse protection rather than a strict global cap; admins opt
    in explicitly.
    """
    return (
        request.env["ir.config_parameter"]
        .sudo()  # sudo: read a global config flag, not user-scoped data.
        .get_param("mcp_server.enable_rate_limiting", "False")
        == "True"
    )


def get_request_limit():
    """Get request limit from system parameter `mcp_server.request_limit`.

    Default is 300 requests per minute per user; 0 means unlimited.
    """
    try:
        limit = int(
            request.env["ir.config_parameter"]
            .sudo()
            .get_param("mcp_server.request_limit", DEFAULT_REQUEST_LIMIT)
        )
        # 0 means unlimited, don't enforce minimum
        if limit == 0:
            return 0
        # For non-zero limits, ensure a sensible minimum
        return max(MINIMUM_REQUEST_LIMIT, limit)
    except (ValueError, TypeError) as e:
        _logger.error(f"Error parsing request limit: {e}. Using default value.")
        return DEFAULT_REQUEST_LIMIT


def record_api_request(user_id: int, dbname: str) -> None:
    """Record an API request timestamp for rate limiting.

    :param dbname: namespaces the per-user bucket so the same uid in different
        databases does not share a quota.
    """
    _api_limiter.hit((dbname, user_id))


def check_rate_limit(user_id: int, dbname: str) -> bool:
    """Return True if the user is within the configured request limit.

    Counts the in-window requests WITHOUT recording a new one, preserving the
    ``@rate_limit`` decorator's check-then-record contract: a request rejected
    with a 429 must not append to the bucket (which would keep the caller locked
    out by topping up its own window).
    """
    limit = get_request_limit()

    if limit == 0:
        return True

    return _api_limiter.count((dbname, user_id)) < limit


def retry_after_seconds():
    """``Retry-After`` value (seconds) advertised on every 429.

    The limiter only exposes its (fixed) window, so the window length is the
    upper-bound back-off: the worst case before a slot frees up. Shared by
    ``/mcp``, the REST routes and the XML-RPC proxy.
    """
    return RATE_LIMIT_WINDOW_MINUTES * 60


def _too_many_requests(message):
    """HTTP 429 REST error response carrying the ``Retry-After`` header."""
    # Import here to avoid circular imports
    from . import response_utils

    response = response_utils.error_response(message, "E429", status=429)
    response.headers["Retry-After"] = str(retry_after_seconds())
    return response


def rate_limit(func):
    """Decorator enforcing the per-minute request limit; returns 429 if exceeded.

    Assumes ``require_api_key`` (or similar) ran first so ``kwargs['user']`` is
    set.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not is_rate_limiting_enabled():
            return func(*args, **kwargs)

        # Namespace the rate-limit bucket by database (cross-tenant collision).
        dbname = request.env.cr.dbname

        user = kwargs.get("user")
        if not user:
            # For anonymous requests, use a special ID
            _logger.warning(
                "Rate limit decorator called without a user context. "
                "Using fallback anonymous rate limiting."
            )
            anonymous_id = -1
            if not check_rate_limit(anonymous_id, dbname):
                return _too_many_requests(
                    "Too many anonymous requests. Please try again later."
                )
            record_api_request(anonymous_id, dbname)
            return func(*args, **kwargs)

        if not check_rate_limit(user.id, dbname):
            request.env["mcp.log"].sudo().log_rate_limit_exceeded(
                user_id=user.id,
                endpoint=request.httprequest.path,
                ip_address=request.httprequest.remote_addr,
            )
            return _too_many_requests("Too many requests. Please try again later.")

        record_api_request(user.id, dbname)
        return func(*args, **kwargs)

    return wrapper

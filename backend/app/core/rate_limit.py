"""In-process fixed-window rate limiting for the auth endpoints (Phase 4A).

Scope is deliberately narrow: only ``/api/auth/login`` and ``/api/auth/signup``
are limited (per instructions — limiting everything else is out of scope for
4A). State lives in this process only: the API runs as a single uvicorn
worker (``scripts/backend-on.sh``), so counters are process-stable; a shared
store would be needed for multi-instance deployments (documented as 4D).

Keying: ``Request.client.host`` — the socket peer address, which cannot be
spoofed by request headers. ``X-Forwarded-For`` is intentionally ignored
because trusting it without a proxy configuration would let any client mint
fresh keys. Behind a reverse proxy every request would share the proxy IP
until trusted-proxy handling exists (4D).
"""

from __future__ import annotations

import time


class InMemoryRateLimiter:
    """Fixed-window counter: ``max_hits`` allowed per ``window_seconds`` per key.

    ``hit()`` counts every attempt (allowed or blocked). When blocked it
    returns the remaining wait in whole seconds for a ``Retry-After`` header.
    Memory is bounded: buckets are pruned once expired when near ``max_keys``,
    and the oldest bucket is evicted if the cap is still reached, so the store
    can never grow without limit.
    """

    def __init__(self, window_seconds: int, max_hits: int, *, max_keys: int = 4096) -> None:
        if window_seconds <= 0 or max_hits <= 0:
            raise ValueError("window_seconds and max_hits must be positive")
        self._window_seconds = window_seconds
        self._max_hits = max_hits
        self._max_keys = max_keys
        self._buckets: dict[str, tuple[float, int]] = {}

    @property
    def window_seconds(self) -> int:
        return self._window_seconds

    @property
    def max_hits(self) -> int:
        return self._max_hits

    def hit(self, key: str) -> tuple[bool, int]:
        """Record one attempt. Returns ``(allowed, retry_after_seconds)``."""
        now = time.monotonic()
        self._prune_if_full(now)
        window_start, count = self._buckets.get(key, (now, 0))
        if now - window_start >= self._window_seconds:
            window_start, count = now, 0
        count += 1
        self._buckets[key] = (window_start, count)
        if count > self._max_hits:
            wait = int(self._window_seconds - (now - window_start)) + 1
            return False, max(wait, 1)
        return True, 0

    def remaining(self, key: str) -> int:
        """Attempts left in the current window for ``key`` (diagnostics/tests)."""
        window_start, count = self._buckets.get(key, (0.0, 0))
        if time.monotonic() - window_start >= self._window_seconds:
            return self._max_hits
        return max(self._max_hits - count, 0)

    def reset(self) -> None:
        """Clear all counters (called between tests)."""
        self._buckets.clear()

    def _prune_if_full(self, now: float) -> None:
        if len(self._buckets) < self._max_keys:
            return
        expired = [
            key for key, (start, _) in self._buckets.items()
            if now - start >= self._window_seconds
        ]
        for key in expired:
            del self._buckets[key]
        overflow = len(self._buckets) - self._max_keys + 1
        if overflow > 0:
            for key in list(self._buckets)[:overflow]:
                del self._buckets[key]


# Login: 10 attempts per 60s per client IP. Signup: 5 per 300s per client IP.
# Both count blocked attempts, so retrying inside the window never restores quota.
login_limiter = InMemoryRateLimiter(window_seconds=60, max_hits=10)
signup_limiter = InMemoryRateLimiter(window_seconds=300, max_hits=5)


def reset_all() -> None:
    """Reset every endpoint limiter (test fixture hook)."""
    login_limiter.reset()
    signup_limiter.reset()

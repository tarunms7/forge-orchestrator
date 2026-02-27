"""In-memory sliding-window rate limiter."""

from __future__ import annotations

import time
from collections import defaultdict


class RateLimiter:
    """Simple in-memory rate limiter using a sliding window.

    Each *key* (e.g. IP address or user ID) is allowed at most
    ``max_requests`` calls within any rolling ``window_seconds`` period.
    """

    def __init__(
        self,
        max_requests: int = 60,
        window_seconds: float = 60.0,
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    async def check(self, key: str) -> bool:
        """Check whether *key* is within its rate limit.

        Returns ``True`` if the request is allowed, ``False`` if it should
        be throttled.  A successful check also records the current timestamp.
        """
        now = time.monotonic()
        cutoff = now - self.window_seconds

        # Prune expired timestamps
        timestamps = self._requests[key]
        self._requests[key] = [t for t in timestamps if t > cutoff]

        if len(self._requests[key]) >= self.max_requests:
            return False

        self._requests[key].append(now)
        return True

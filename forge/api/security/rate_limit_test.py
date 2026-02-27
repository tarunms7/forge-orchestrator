"""Tests for the in-memory rate limiter."""

import asyncio



async def test_allows_requests_under_limit():
    """Requests under the limit should be allowed."""
    from forge.api.security.rate_limit import RateLimiter

    limiter = RateLimiter(max_requests=5, window_seconds=60)

    for _ in range(5):
        allowed = await limiter.check("user-1")
        assert allowed is True


async def test_blocks_requests_over_limit():
    """Requests over the limit should be blocked."""
    from forge.api.security.rate_limit import RateLimiter

    limiter = RateLimiter(max_requests=3, window_seconds=60)

    for _ in range(3):
        await limiter.check("user-2")

    blocked = await limiter.check("user-2")
    assert blocked is False


async def test_different_keys_independent():
    """Rate limits should be independent per key."""
    from forge.api.security.rate_limit import RateLimiter

    limiter = RateLimiter(max_requests=2, window_seconds=60)

    await limiter.check("key-a")
    await limiter.check("key-a")

    # key-a is exhausted
    assert await limiter.check("key-a") is False

    # key-b should still work
    assert await limiter.check("key-b") is True


async def test_window_expires_allows_again():
    """After the window expires, requests should be allowed again."""
    from forge.api.security.rate_limit import RateLimiter

    # Use a very short window
    limiter = RateLimiter(max_requests=1, window_seconds=0.1)

    assert await limiter.check("user-3") is True
    assert await limiter.check("user-3") is False

    # Wait for the window to expire
    await asyncio.sleep(0.15)

    assert await limiter.check("user-3") is True


async def test_default_limits():
    """Default rate limiter should have reasonable defaults."""
    from forge.api.security.rate_limit import RateLimiter

    limiter = RateLimiter()
    assert limiter.max_requests > 0
    assert limiter.window_seconds > 0

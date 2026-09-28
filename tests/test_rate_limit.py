import pytest

from app.services.rate_limit import SlidingWindowRateLimiter


@pytest.mark.asyncio
async def test_sliding_window_rate_limit() -> None:
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=10)
    assert await limiter.allow(1, now=0)
    assert await limiter.allow(1, now=1)
    assert not await limiter.allow(1, now=2)
    assert await limiter.allow(1, now=11)
    assert await limiter.allow(2, now=2)

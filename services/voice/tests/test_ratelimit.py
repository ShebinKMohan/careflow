import pytest

from voice_agent.ratelimit import SlidingWindowRateLimiter


def test_sliding_window_rate_limiter_blocks_over_limit_then_recovers() -> None:
    clock = {"t": 0.0}
    limiter = SlidingWindowRateLimiter(
        max_requests=2, window_seconds=10.0, time_fn=lambda: clock["t"]
    )

    assert limiter.allow("key") is True
    assert limiter.allow("key") is True
    assert limiter.allow("key") is False  # third within the window is rejected

    clock["t"] = 11.0  # window elapsed
    assert limiter.allow("key") is True


def test_sliding_window_rate_limiter_is_per_key() -> None:
    limiter = SlidingWindowRateLimiter(
        max_requests=1, window_seconds=10.0, time_fn=lambda: 0.0
    )
    assert limiter.allow("a") is True
    assert limiter.allow("b") is True
    assert limiter.allow("a") is False


def test_sliding_window_rate_limiter_validates_args() -> None:
    with pytest.raises(ValueError):
        SlidingWindowRateLimiter(max_requests=0, window_seconds=10.0)
    with pytest.raises(ValueError):
        SlidingWindowRateLimiter(max_requests=1, window_seconds=0.0)

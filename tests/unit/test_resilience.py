"""Unit tests for ``backend.services.resilience``.

Covers:
* CircuitBreaker CLOSED/OPEN/HALF_OPEN transitions
* with_retry exponential-backoff behavior
* TTLCache get/set/invalidate + expiry
* Resilient composition breaker + retry
* DependencyDownError typing

All tests use deterministic mono-clocks / mock sleep / mock random so
the suite runs in milliseconds with no flakiness.
"""
from __future__ import annotations

import pytest

from backend.services.resilience import (
    CircuitBreaker,
    DependencyDownError,
    Resilient,
    TTLCache,
    with_retry,
)

# ---------------------------------------------------------------------------
# Deterministic clock helper
# ---------------------------------------------------------------------------


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, s: float) -> None:
        self.now += s


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


def test_breaker_starts_closed_and_allows_calls() -> None:
    cb = CircuitBreaker("test", failure_threshold=3, cooldown_s=1.0)
    assert cb.state == "closed"
    assert cb.allow() is True


def test_breaker_opens_after_threshold_failures() -> None:
    clock = _FakeClock()
    cb = CircuitBreaker("test", failure_threshold=3, cooldown_s=10.0, now=clock)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "closed"  # still under threshold
    cb.record_failure()
    assert cb.state == "open"
    assert cb.allow() is False


def test_breaker_half_opens_after_cooldown_and_closes_on_success() -> None:
    clock = _FakeClock()
    cb = CircuitBreaker("test", failure_threshold=2, cooldown_s=5.0, now=clock)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "open"
    clock.advance(6.0)
    assert cb.state == "half_open"
    # First allow() consumes the half-open probe slot.
    assert cb.allow() is True
    # No more probes allowed until success/failure retires the first.
    assert cb.allow() is False
    cb.record_success()
    assert cb.state == "closed"
    # Post-recovery: full throughput.
    assert cb.allow() is True


def test_breaker_reopens_on_half_open_probe_failure() -> None:
    clock = _FakeClock()
    cb = CircuitBreaker("test", failure_threshold=1, cooldown_s=2.0, now=clock)
    cb.record_failure()  # open
    clock.advance(3.0)
    assert cb.state == "half_open"
    assert cb.allow() is True
    cb.record_failure()  # probe fails -> re-open
    assert cb.state == "open"


def test_breaker_success_in_closed_state_resets_failure_counter() -> None:
    cb = CircuitBreaker("test", failure_threshold=3, cooldown_s=1.0)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()  # counter reset to 0
    cb.record_failure()  # only 1 failure now -- still closed
    assert cb.state == "closed"


def test_breaker_invalid_config_raises() -> None:
    with pytest.raises(ValueError):
        CircuitBreaker("test", failure_threshold=0)
    with pytest.raises(ValueError):
        CircuitBreaker("test", cooldown_s=0)
    with pytest.raises(ValueError):
        CircuitBreaker("test", half_open_probes=0)


# ---------------------------------------------------------------------------
# with_retry
# ---------------------------------------------------------------------------


def test_with_retry_returns_on_first_success() -> None:
    sleeps: list[float] = []
    result = with_retry(lambda: 42, attempts=3, sleep=sleeps.append, rand=lambda: 0.5)
    assert result == 42
    assert sleeps == []  # no retries needed


def test_with_retry_retries_until_success() -> None:
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    sleeps: list[float] = []
    result = with_retry(
        flaky, attempts=5, backoff_base=0.1, sleep=sleeps.append, rand=lambda: 0.5
    )
    assert result == "ok"
    assert calls["n"] == 3
    # Two sleeps (after attempts 0 and 1). Jitter factor = 1.0 at rand=0.5.
    assert len(sleeps) == 2
    assert sleeps[0] == pytest.approx(0.1)
    assert sleeps[1] == pytest.approx(0.2)


def test_with_retry_raises_last_exception_on_exhaustion() -> None:
    def always_fails() -> None:
        raise RuntimeError("boom-3")

    with pytest.raises(RuntimeError, match="boom-3"):
        with_retry(
            always_fails,
            attempts=3,
            backoff_base=0.01,
            sleep=lambda _s: None,
            rand=lambda: 0.5,
        )


def test_with_retry_honors_retry_on_filter() -> None:
    """Non-retryable exceptions short-circuit immediately."""

    class BadCall(Exception):
        pass

    calls = {"n": 0}

    def raise_bad() -> None:
        calls["n"] += 1
        raise BadCall("nope")

    with pytest.raises(BadCall):
        with_retry(
            raise_bad,
            attempts=5,
            retry_on=(RuntimeError,),  # BadCall not in list
            sleep=lambda _s: None,
            rand=lambda: 0.5,
        )
    assert calls["n"] == 1


def test_with_retry_invalid_attempts() -> None:
    with pytest.raises(ValueError):
        with_retry(lambda: None, attempts=0)


# ---------------------------------------------------------------------------
# TTLCache
# ---------------------------------------------------------------------------


def test_ttl_cache_miss_returns_none() -> None:
    cache = TTLCache()
    assert cache.get("nothing-here") is None


def test_ttl_cache_hit_within_ttl() -> None:
    clock = _FakeClock()
    cache = TTLCache(now=clock)
    cache.set("k", "v", ttl_s=10.0)
    assert cache.get("k") == "v"
    clock.advance(5.0)
    assert cache.get("k") == "v"


def test_ttl_cache_expires_after_ttl() -> None:
    clock = _FakeClock()
    cache = TTLCache(now=clock)
    cache.set("k", 42, ttl_s=1.0)
    clock.advance(1.1)
    assert cache.get("k") is None


def test_ttl_cache_invalidate_drops_entry() -> None:
    cache = TTLCache()
    cache.set("k", "v", ttl_s=60.0)
    cache.invalidate("k")
    assert cache.get("k") is None


def test_ttl_cache_zero_ttl_disables_entry() -> None:
    cache = TTLCache()
    cache.set("k", "v", ttl_s=0.0)
    # 0 TTL is the documented "disabled" signal; nothing should land.
    assert cache.get("k") is None


# ---------------------------------------------------------------------------
# Resilient composition
# ---------------------------------------------------------------------------


def test_resilient_call_passes_through_on_success() -> None:
    cb = CircuitBreaker("wh", failure_threshold=3, cooldown_s=10.0)
    r = Resilient[int](
        breaker=cb, dependency_name="warehouse", attempts=1
    )
    assert r.call(lambda: 7) == 7
    assert cb.state == "closed"


def test_resilient_call_opens_breaker_on_repeated_failure() -> None:
    clock = _FakeClock()
    cb = CircuitBreaker("wh", failure_threshold=2, cooldown_s=10.0, now=clock)

    def boom() -> None:
        raise RuntimeError("dep-down")

    r = Resilient[int](
        breaker=cb, dependency_name="warehouse", attempts=1
    )
    with pytest.raises(DependencyDownError) as info:
        r.call(boom)
    assert info.value.dependency == "warehouse"
    # First call recorded one failure.
    with pytest.raises(DependencyDownError):
        r.call(boom)
    assert cb.state == "open"
    # Breaker-open raises WITHOUT invoking fn.
    calls = {"n": 0}

    def tracker() -> None:
        calls["n"] += 1

    with pytest.raises(DependencyDownError, match="circuit breaker is open"):
        r.call(tracker)
    assert calls["n"] == 0


def test_resilient_wraps_underlying_error_as_dependency_down() -> None:
    cb = CircuitBreaker("wh")

    class ConnectionBoom(RuntimeError):
        pass

    def boom() -> None:
        raise ConnectionBoom("socket-reset")

    r = Resilient[None](breaker=cb, dependency_name="warehouse", attempts=1)
    with pytest.raises(DependencyDownError) as info:
        r.call(boom)
    # Underlying exception is preserved for debugging.
    assert isinstance(info.value.last_error, ConnectionBoom)
    assert "socket-reset" in info.value.reason

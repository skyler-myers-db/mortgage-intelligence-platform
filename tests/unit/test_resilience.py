"""Unit tests for ``backend.services.resilience``.

Covers:
* CircuitBreaker CLOSED/OPEN/HALF_OPEN transitions
* with_retry exponential-backoff behavior
* TTLCache get/set/invalidate + expiry
* StaleWhileRevalidateCache soft/hard TTL + background refresh
* Resilient composition breaker + retry
* DependencyDownError typing

All tests use deterministic mono-clocks / mock sleep / mock random so
the suite runs in milliseconds with no flakiness.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest

from backend.services.resilience import (
    CircuitBreaker,
    DependencyDownError,
    Resilient,
    StaleWhileRevalidateCache,
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
# R6-18: force_close_if_config_changed escape hatch
# ---------------------------------------------------------------------------


def test_force_close_if_config_changed_closes_when_predicate_true() -> None:
    """R6-18: a breaker jammed OPEN by ``force_open_for_placeholder_config``
    must be closeable when the operator fixes the config at runtime.

    Predicate returns True -> breaker closes, failure counter resets,
    ``allow()`` returns True again.
    """
    cb = CircuitBreaker("genie", failure_threshold=3, cooldown_s=20.0)
    cb.force_open_for_placeholder_config()
    assert cb.state == "open"
    assert cb.allow() is False

    closed = cb.force_close_if_config_changed(lambda: True)
    assert closed is True
    assert cb.state == "closed"
    assert cb.allow() is True


def test_force_close_if_config_changed_noop_when_predicate_false() -> None:
    """R6-18: predicate False -> breaker stays in its current state.
    No log event is emitted; no state mutation happens. The caller
    gets ``False`` so it can tell "still placeholder" apart from
    "just recovered".
    """
    cb = CircuitBreaker("genie", failure_threshold=3, cooldown_s=20.0)
    cb.force_open_for_placeholder_config()

    closed = cb.force_close_if_config_changed(lambda: False)
    assert closed is False
    assert cb.state == "open"


def test_force_close_if_config_changed_noop_when_already_closed() -> None:
    """Closing an already-closed breaker is a no-op and returns False
    (the contract: True means 'we actually flipped state')."""
    cb = CircuitBreaker("genie", failure_threshold=3, cooldown_s=20.0)
    assert cb.state == "closed"
    assert cb.force_close_if_config_changed(lambda: True) is False


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


# ---------------------------------------------------------------------------
# StaleWhileRevalidateCache
#
# The SWR cache wraps /api/health's three dependency probes and is the
# tip of the spear for the slice-13 p95 latency work. These tests use a
# fake clock plus a single-worker executor so refresh scheduling is
# deterministic (no time.sleep, no concurrency racing on real threads).
# ---------------------------------------------------------------------------


class _InlineExecutor:
    """ThreadPoolExecutor-shaped shim that runs submitted work in the
    caller's thread. Lets us assert refresh behaviour without juggling
    real thread-scheduling timing. Used only where the test explicitly
    does NOT care whether the refresh ran async."""

    def submit(self, fn, *args, **kwargs):  # type: ignore[no-untyped-def]
        fn(*args, **kwargs)
        # Return a dummy future-shaped object -- SWR code only calls
        # .submit() and never inspects the return value.
        class _Done:
            def result(self) -> None:
                return None

        return _Done()


def test_swr_cache_miss_runs_sync_probe_and_stores() -> None:
    clock = _FakeClock()
    cache = StaleWhileRevalidateCache(
        soft_ttl_s=2.0, hard_ttl_s=10.0, executor=_InlineExecutor(), now=clock
    )
    calls = {"n": 0}

    def probe() -> str:
        calls["n"] += 1
        return "fresh"

    assert cache.get_or_refresh("warehouse", probe) == "fresh"
    assert calls["n"] == 1
    # Second call well under soft TTL -- no probe.
    clock.advance(0.5)
    assert cache.get_or_refresh("warehouse", probe) == "fresh"
    assert calls["n"] == 1


def test_swr_cache_under_soft_ttl_does_not_refresh() -> None:
    clock = _FakeClock()
    cache = StaleWhileRevalidateCache(
        soft_ttl_s=2.0, hard_ttl_s=10.0, executor=_InlineExecutor(), now=clock
    )
    calls = {"n": 0}

    def probe() -> str:
        calls["n"] += 1
        return "v"

    cache.get_or_refresh("warehouse", probe)
    # Three hits inside the soft window should share one probe.
    clock.advance(0.5)
    cache.get_or_refresh("warehouse", probe)
    clock.advance(0.5)
    cache.get_or_refresh("warehouse", probe)
    clock.advance(0.5)
    cache.get_or_refresh("warehouse", probe)
    assert calls["n"] == 1


def test_swr_cache_between_soft_and_hard_kicks_background_refresh() -> None:
    clock = _FakeClock()
    cache = StaleWhileRevalidateCache(
        soft_ttl_s=2.0, hard_ttl_s=10.0, executor=_InlineExecutor(), now=clock
    )
    values = iter(["v1", "v2"])

    def probe() -> str:
        return next(values)

    # First call stores "v1".
    assert cache.get_or_refresh("warehouse", probe) == "v1"
    # Advance past soft TTL but before hard TTL. Next call must return
    # the cached v1 AND schedule a refresh (inline executor runs it
    # synchronously, so "v2" is already stored by the time we return).
    clock.advance(3.0)
    assert cache.get_or_refresh("warehouse", probe) == "v1"
    # Subsequent call inside the new soft window sees the refreshed value.
    assert cache.get_or_refresh("warehouse", probe) == "v2"


def test_swr_cache_above_hard_ttl_forces_sync_refresh() -> None:
    clock = _FakeClock()
    cache = StaleWhileRevalidateCache(
        soft_ttl_s=2.0, hard_ttl_s=10.0, executor=_InlineExecutor(), now=clock
    )
    values = iter(["v1", "v2"])

    def probe() -> str:
        return next(values)

    assert cache.get_or_refresh("warehouse", probe) == "v1"
    # Past hard TTL -- cached entry is evicted and the caller must get
    # the freshly probed value on THIS call (not a stale one).
    clock.advance(10.1)
    assert cache.get_or_refresh("warehouse", probe) == "v2"


def test_swr_cache_concurrent_stale_callers_only_kick_one_refresh() -> None:
    """Between soft and hard TTL, N concurrent callers must only
    enqueue ONE background refresh -- not N. The refresh_in_flight
    flag is the mechanism; this test validates it under real threading
    with a blocking probe."""
    clock = _FakeClock()
    release = Event()
    probe_calls = {"n": 0}
    probe_lock = Lock()

    def slow_probe() -> str:
        with probe_lock:
            probe_calls["n"] += 1
        # Block until the test thread releases us; this keeps the
        # refresh "in flight" long enough for the other callers to race.
        release.wait(timeout=2.0)
        return "fresh"

    pool = ThreadPoolExecutor(max_workers=4)
    try:
        cache = StaleWhileRevalidateCache(
            soft_ttl_s=2.0, hard_ttl_s=10.0, executor=pool, now=clock
        )
        # Seed with a cached value; use a separate fast probe so seeding
        # doesn't block and doesn't count toward the assertion.
        cache.get_or_refresh("warehouse", lambda: "seed")
        # Advance into the stale-but-fresh window.
        clock.advance(3.0)

        # Fan out several concurrent callers against the SLOW probe.
        caller_pool = ThreadPoolExecutor(max_workers=8)
        try:
            futs = [
                caller_pool.submit(cache.get_or_refresh, "warehouse", slow_probe)
                for _ in range(8)
            ]
            # All callers must see the cached seed value immediately,
            # never block on the slow probe themselves.
            for f in futs:
                assert f.result(timeout=1.0) == "seed"
        finally:
            caller_pool.shutdown(wait=True)

        # Exactly one refresh is in flight. Release it and let the
        # worker finish before we assert.
        release.set()
    finally:
        pool.shutdown(wait=True)

    assert probe_calls["n"] == 1


def test_swr_cache_clear_resets_state() -> None:
    clock = _FakeClock()
    cache = StaleWhileRevalidateCache(
        soft_ttl_s=2.0, hard_ttl_s=10.0, executor=_InlineExecutor(), now=clock
    )
    calls = {"n": 0}

    def probe() -> int:
        calls["n"] += 1
        return calls["n"]

    assert cache.get_or_refresh("warehouse", probe) == 1
    cache.clear()
    # After clear(), the next call is a sync miss -- another probe runs.
    assert cache.get_or_refresh("warehouse", probe) == 2
    assert calls["n"] == 2


def test_swr_cache_background_refresh_failure_keeps_last_good_value() -> None:
    """A probe that raises during a background refresh must not wedge
    the cache. The caller still gets the last-good cached value; the
    refresh_in_flight flag is cleared so the NEXT caller can retry."""
    clock = _FakeClock()
    cache = StaleWhileRevalidateCache(
        soft_ttl_s=2.0, hard_ttl_s=10.0, executor=_InlineExecutor(), now=clock
    )
    calls = {"ok": 0, "boom": 0}

    def ok_probe() -> str:
        calls["ok"] += 1
        return "v1"

    def boom_probe() -> str:
        calls["boom"] += 1
        raise RuntimeError("transient")

    # Seed with a good value.
    assert cache.get_or_refresh("warehouse", ok_probe) == "v1"
    # Advance into the stale-but-fresh window; kick a refresh that blows
    # up. Caller must still receive the seeded value.
    clock.advance(3.0)
    assert cache.get_or_refresh("warehouse", boom_probe) == "v1"
    assert calls["boom"] == 1
    # Next stale-but-fresh call should kick ANOTHER refresh (flag was
    # cleared even though the last attempt errored).
    assert cache.get_or_refresh("warehouse", boom_probe) == "v1"
    assert calls["boom"] == 2


def test_swr_cache_rejects_bad_ttls() -> None:
    with pytest.raises(ValueError, match="soft_ttl_s"):
        StaleWhileRevalidateCache(soft_ttl_s=0.0, hard_ttl_s=10.0)
    with pytest.raises(ValueError, match="hard_ttl_s"):
        StaleWhileRevalidateCache(soft_ttl_s=5.0, hard_ttl_s=5.0)
    with pytest.raises(ValueError, match="hard_ttl_s"):
        StaleWhileRevalidateCache(soft_ttl_s=5.0, hard_ttl_s=1.0)


# ---------------------------------------------------------------------------
# R5-06: SWR probe-timeout + atexit-shutdown hardening
# ---------------------------------------------------------------------------


def test_swr_background_probe_timeout_clears_in_flight_flag() -> None:
    """R5-06: a probe that hangs past ``_SWR_PROBE_TIMEOUT_S`` must NOT
    wedge the refresh slot forever.

    Regression: pre-fix, a hung probe held the in-flight flag for the
    full underlying client timeout (tens of seconds). With only three
    SWR slots, a TCP-black-hole warehouse could exhaust the pool and
    every subsequent health request fell to the sync-miss path,
    defeating the cache.

    Assertion: after the timeout the worker clears the flag so a
    subsequent stale-but-fresh call is eligible to schedule a fresh
    refresh. The orphaned probe thread is daemonic and does not block
    test teardown.
    """
    from backend.services import resilience as res

    clock = _FakeClock()
    cache = StaleWhileRevalidateCache(
        soft_ttl_s=2.0, hard_ttl_s=10.0, executor=_InlineExecutor(), now=clock
    )
    # Seed with a fast probe so the cache has a cached value; the
    # slow probe below only runs on the stale-window refresh path.
    assert cache.get_or_refresh("warehouse", lambda: "seed") == "seed"

    # Shrink the probe timeout so the test doesn't wait a full second.
    # We only touch the module-level constant inside this test; the
    # try/finally restores it.
    original_timeout = res._SWR_PROBE_TIMEOUT_S
    res._SWR_PROBE_TIMEOUT_S = 0.05

    probe_started = Event()
    release = Event()
    second_probe_calls = {"n": 0}

    def hang_probe() -> str:
        probe_started.set()
        # Block past the per-probe timeout so the SWR worker gives up.
        release.wait(timeout=1.0)
        return "should-be-ignored"

    def fast_probe() -> str:
        second_probe_calls["n"] += 1
        return "fresh"

    try:
        clock.advance(3.0)
        # _InlineExecutor runs the worker inline; the worker in turn
        # spawns a daemon probe thread and joins with timeout. After
        # the timeout the worker returns (cached seed is still served).
        assert cache.get_or_refresh("warehouse", hang_probe) == "seed"
        assert probe_started.is_set(), "worker should have launched the probe"

        # The in-flight flag must be cleared: the next stale-window
        # caller has to be able to schedule a fresh refresh and it
        # must actually run (not be skipped because refreshing==True).
        clock.advance(0.1)  # still within the stale-but-fresh window
        cache.get_or_refresh("warehouse", fast_probe)
        assert second_probe_calls["n"] == 1, (
            "in-flight flag was not cleared -- next stale caller did "
            "not schedule a refresh"
        )
    finally:
        release.set()
        res._SWR_PROBE_TIMEOUT_S = original_timeout


def test_swr_executor_shutdown_registered_at_import() -> None:
    """R5-06: the module-level SWR executor must be handed to ``atexit``
    at import time so uvicorn reload / worker restart does not leak the
    pool (threads blocked on socket reads would otherwise keep the
    interpreter from exiting cleanly).

    We can't observe ``atexit._exithandlers`` portably across CPython
    versions; instead, we verify that the module exposes the shutdown
    function and that calling it twice is idempotent (the second call
    is a no-op because the executor slot is cleared).
    """
    from backend.services import resilience as res

    # Touch the executor once so it's actually instantiated.
    pool = res._get_swr_executor()
    assert pool is not None

    # Shutdown should tear it down AND null the slot.
    res._shutdown_swr_executor()
    # Second shutdown must be a no-op (pool slot is None). If it raised
    # the atexit hook would crash the interpreter teardown.
    res._shutdown_swr_executor()

    # After shutdown the next call reconstructs a fresh pool so the
    # test process doesn't leave other resilience tests without an
    # executor. This also validates that the slot reset is symmetric.
    fresh = res._get_swr_executor()
    assert fresh is not None
    # Clean up so the atexit hook at real-process shutdown doesn't see
    # two pools.
    res._shutdown_swr_executor()

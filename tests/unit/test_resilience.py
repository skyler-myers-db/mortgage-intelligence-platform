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

import time
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


def test_ttl_cache_evicts_least_recently_used_entry() -> None:
    cache = TTLCache(max_entries=2)
    cache.set("a", 1, ttl_s=60.0)
    cache.set("b", 2, ttl_s=60.0)
    assert cache.get("a") == 1  # make a most-recent
    cache.set("c", 3, ttl_s=60.0)

    assert cache.get("a") == 1
    assert cache.get("b") is None
    assert cache.get("c") == 3


def test_ttl_cache_get_or_set_coalesces_concurrent_misses() -> None:
    cache = TTLCache()
    start = Event()
    release = Event()
    calls = 0
    calls_lock = Lock()

    def factory() -> str:
        nonlocal calls
        with calls_lock:
            calls += 1
        start.set()
        release.wait(timeout=2.0)
        return "fresh"

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [
            pool.submit(cache.get_or_set, "hot", factory, ttl_s=60.0)
            for _ in range(5)
        ]
        assert start.wait(timeout=2.0)
        release.set()
        assert [future.result(timeout=2.0) for future in futures] == ["fresh"] * 5

    assert calls == 1


def test_ttl_cache_stale_if_error_returns_expired_last_good_value() -> None:
    clock = _FakeClock()
    cache = TTLCache(now=clock)
    cache.set("aggregate", {"count": 7}, ttl_s=1.0)
    clock.advance(2.0)

    def boom() -> dict[str, int]:
        raise RuntimeError("warehouse down")

    assert cache.get("aggregate") is None
    assert cache.get_or_set(
        "aggregate",
        boom,
        ttl_s=60.0,
        stale_if_error=True,
    ) == {"count": 7}


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
# Health-probe single-flight, caller deadline, and atexit hardening
# ---------------------------------------------------------------------------


def test_swr_stale_slow_refresh_has_no_fanout_and_late_result_wins() -> None:
    clock = _FakeClock()
    probe_started = Event()
    release = Event()
    calls = {"n": 0}

    def slow_probe() -> str:
        calls["n"] += 1
        probe_started.set()
        release.wait(timeout=2.0)
        return "late-fresh"

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        cache = StaleWhileRevalidateCache(
            soft_ttl_s=2.0, hard_ttl_s=10.0, executor=pool, now=clock
        )
        assert cache.get_or_refresh("warehouse", lambda: "seed") == "seed"
        clock.advance(3.0)

        assert cache.get_or_refresh("warehouse", slow_probe) == "seed"
        assert probe_started.wait(timeout=1.0)
        for _ in range(20):
            assert cache.get_or_refresh("warehouse", slow_probe) == "seed"
        assert calls["n"] == 1

        release.set()
    finally:
        pool.shutdown(wait=True)

    assert cache.get_or_refresh("warehouse", slow_probe) == "late-fresh"
    assert calls["n"] == 1


def test_swr_twenty_concurrent_cold_callers_share_one_probe() -> None:
    probe_started = Event()
    release = Event()
    calls = {"n": 0}
    calls_lock = Lock()

    def probe() -> bool:
        with calls_lock:
            calls["n"] += 1
        probe_started.set()
        release.wait(timeout=2.0)
        return True

    worker_pool = ThreadPoolExecutor(max_workers=1)
    caller_pool = ThreadPoolExecutor(max_workers=20)
    try:
        cache = StaleWhileRevalidateCache(
            soft_ttl_s=2.0, hard_ttl_s=10.0, executor=worker_pool
        )
        futures = [
            caller_pool.submit(
                cache.get_or_refresh,
                "lakebase",
                probe,
                wait_timeout_s=1.0,
            )
            for _ in range(20)
        ]
        assert probe_started.wait(timeout=1.0)
        release.set()
        assert [future.result(timeout=1.0) for future in futures] == [True] * 20
    finally:
        release.set()
        caller_pool.shutdown(wait=True)
        worker_pool.shutdown(wait=True)

    assert calls["n"] == 1


def test_swr_caller_timeout_is_not_cached_and_late_completion_updates_cache() -> None:
    release = Event()
    calls = {"n": 0}

    def slow_probe() -> bool:
        calls["n"] += 1
        release.wait(timeout=2.0)
        return True

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        cache = StaleWhileRevalidateCache(
            soft_ttl_s=2.0, hard_ttl_s=10.0, executor=pool
        )
        started = time.monotonic()
        assert (
            cache.get_or_refresh(
                "lakebase", slow_probe, wait_timeout_s=0.05
            )
            is None
        )
        assert time.monotonic() - started < 0.25
        assert "lakebase" not in cache._entries

        # A second timed-out caller joins the same real work; it cannot
        # create a replacement probe while the first is still running.
        assert (
            cache.get_or_refresh(
                "lakebase", slow_probe, wait_timeout_s=0.01
            )
            is None
        )
        assert calls["n"] == 1
        release.set()
    finally:
        release.set()
        pool.shutdown(wait=True)

    assert cache.get_or_refresh("lakebase", slow_probe) is True
    assert calls["n"] == 1


def test_swr_clear_fences_late_single_flight_completion() -> None:
    release = Event()

    def slow_probe() -> bool:
        release.wait(timeout=2.0)
        return True

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        cache = StaleWhileRevalidateCache(
            soft_ttl_s=2.0, hard_ttl_s=10.0, executor=pool
        )
        assert cache.get_or_refresh("lakebase", slow_probe, wait_timeout_s=0.01) is None
        cache.clear()
        release.set()
    finally:
        release.set()
        pool.shutdown(wait=True)

    assert "lakebase" not in cache._entries
    assert "lakebase" not in cache._inflight


def test_swr_batch_uses_one_request_deadline_for_nonreturning_dependencies() -> None:
    release = Event()
    calls = {"warehouse": 0, "lakebase": 0, "genie": 0}

    def probe(name: str) -> bool:
        calls[name] += 1
        release.wait(timeout=2.0)
        return True

    pool = ThreadPoolExecutor(max_workers=3)
    try:
        cache = StaleWhileRevalidateCache(
            soft_ttl_s=2.0, hard_ttl_s=10.0, executor=pool
        )
        probes = {name: (lambda name=name: probe(name)) for name in calls}
        started = time.monotonic()
        assert cache.get_or_refresh_many(probes, wait_timeout_s=0.05) == {
            "warehouse": None,
            "lakebase": None,
            "genie": None,
        }
        assert time.monotonic() - started < 0.25

        # A new request shares the same three active probes.
        cache.get_or_refresh_many(probes, wait_timeout_s=0.01)
        assert calls == {"warehouse": 1, "lakebase": 1, "genie": 1}
        release.set()
    finally:
        release.set()
        pool.shutdown(wait=True)


def test_swr_probe_exception_then_recovery_is_not_poisoned() -> None:
    cache = StaleWhileRevalidateCache(
        soft_ttl_s=2.0, hard_ttl_s=10.0, executor=_InlineExecutor()
    )
    calls = {"n": 0}

    def probe() -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("temporary")
        return True

    assert cache.get_or_refresh("lakebase", probe) is None
    assert "lakebase" not in cache._entries
    assert cache.get_or_refresh("lakebase", probe) is True
    assert calls["n"] == 2


def test_swr_real_down_result_recovers_on_next_stale_refresh() -> None:
    clock = _FakeClock()
    cache = StaleWhileRevalidateCache(
        soft_ttl_s=2.0,
        hard_ttl_s=10.0,
        executor=_InlineExecutor(),
        now=clock,
    )
    values = iter([False, True])

    assert cache.get_or_refresh("lakebase", lambda: next(values)) is False
    clock.advance(3.0)
    # Stale callers retain the completed down result while the one refresh
    # runs. The inline executor makes its recovery deterministic here.
    assert cache.get_or_refresh("lakebase", lambda: next(values)) is False
    assert cache.get_or_refresh("lakebase", lambda: False) is True


def test_swr_executor_shutdown_registered_at_import() -> None:
    """The module-level bounded executor is handed to ``atexit``.

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

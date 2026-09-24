"""Stale-while-revalidate gold aggregate cache (2026-09-21 audit ``delivery-06``).

Every test injects the clock; refresh timing is owned by the test through an
inline or a deferred executor, except the isolation test, which exercises the
real dedicated ``mip-gold-swr`` pool against the real health-probe pool.
"""
from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Executor, Future
from typing import Any

import pytest

from backend.config.settings import settings
from backend.services import gold_cache, health_probes, server_timing
from backend.services.gold_cache import (
    GoldAggregateCache,
    bump_workflow_generation,
    get_or_set_capped,
    workflow_generation,
)
from backend.services.resilience_cache import TTLCache


class _DeferredExecutor(Executor):
    """Collects submitted refreshes; the test decides when they run."""

    def __init__(self) -> None:
        self.jobs: list[Callable[[], Any]] = []

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        self.jobs.append(lambda: fn(*args, **kwargs))
        return Future()

    def run_all(self) -> None:
        jobs, self.jobs = self.jobs, []
        for job in jobs:
            job()


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Factory:
    def __init__(self, *values: Any) -> None:
        self._values = list(values)
        self.calls = 0
        self.fail: BaseException | None = None

    def __call__(self) -> Any:
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return self._values[min(self.calls, len(self._values)) - 1]


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def deferred() -> _DeferredExecutor:
    return _DeferredExecutor()


def test_soft_hit_serves_the_cached_value_without_a_second_query(clock: _Clock, deferred: _DeferredExecutor) -> None:
    cache = GoldAggregateCache(now=clock, executor=deferred)
    factory = _Factory("v1", "v2")

    assert cache.get_or_set("k", factory, ttl_s=60) == "v1"
    clock.now += 59
    assert cache.get_or_set("k", factory, ttl_s=60) == "v1"

    assert factory.calls == 1
    assert deferred.jobs == []


def test_stale_value_is_served_with_exactly_one_background_refresh(
    clock: _Clock, deferred: _DeferredExecutor
) -> None:
    cache = GoldAggregateCache(now=clock, executor=deferred)
    factory = _Factory("v1", "v2")
    cache.get_or_set("k", factory, ttl_s=60, hard_ttl_s=600)

    clock.now += 61
    served = [cache.get_or_set("k", factory, ttl_s=60, hard_ttl_s=600) for _ in range(3)]

    assert served == ["v1", "v1", "v1"], "the caller and every follower get the stale value"
    assert len(deferred.jobs) == 1, "exactly one background refresh per key"
    assert factory.calls == 1, "no caller paid the query inline"

    deferred.run_all()
    assert factory.calls == 2
    assert cache.get_or_set("k", factory, ttl_s=60, hard_ttl_s=600) == "v2"
    assert deferred.jobs == [], "the refresh restarted the soft TTL from its completion"


def test_hard_expired_entry_is_recomputed_inline(clock: _Clock, deferred: _DeferredExecutor) -> None:
    cache = GoldAggregateCache(now=clock, executor=deferred)
    factory = _Factory("v1", "v2")
    cache.get_or_set("k", factory, ttl_s=60, hard_ttl_s=600)

    clock.now += 601

    assert cache.get_or_set("k", factory, ttl_s=60, hard_ttl_s=600) == "v2"
    assert factory.calls == 2
    assert deferred.jobs == []


def test_default_hard_cap_comes_from_settings(
    clock: _Clock, deferred: _DeferredExecutor, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "mip_gold_cache_max_stale_s", 300.0)
    cache = GoldAggregateCache(now=clock, executor=deferred)
    factory = _Factory("v1", "v2", "v3")
    cache.get_or_set("k", factory, ttl_s=60)

    clock.now += 299
    assert cache.get_or_set("k", factory, ttl_s=60) == "v1", "inside the cap: stale"
    assert len(deferred.jobs) == 1
    deferred.jobs.clear()
    cache.invalidate("k")
    assert cache.get_or_set("k", factory, ttl_s=60) == "v2"
    clock.now += 301
    assert cache.get_or_set("k", factory, ttl_s=60) == "v3", "past the cap: inline"
    assert deferred.jobs == []


@pytest.mark.parametrize("stale_if_error", [True, False])
def test_cold_failure_raises_the_original_exception(
    clock: _Clock, deferred: _DeferredExecutor, stale_if_error: bool
) -> None:
    cache = GoldAggregateCache(now=clock, executor=deferred)
    factory = _Factory("v1")
    factory.fail = LookupError("warehouse said no")

    with pytest.raises(LookupError, match="warehouse said no"):
        cache.get_or_set("k", factory, ttl_s=60, stale_if_error=stale_if_error)

    factory.fail = None
    assert cache.get_or_set("k", factory, ttl_s=60) == "v1", "a failure is never cached"


def test_inline_failure_serves_the_retained_entry_when_stale_if_error(
    clock: _Clock, deferred: _DeferredExecutor
) -> None:
    cache = GoldAggregateCache(now=clock, executor=deferred)
    factory = _Factory("v1")
    cache.get_or_set("k", factory, ttl_s=60, hard_ttl_s=60)
    clock.now += 61
    factory.fail = RuntimeError("flap")

    assert cache.get_or_set("k", factory, ttl_s=60, hard_ttl_s=60, stale_if_error=True) == "v1"
    with pytest.raises(RuntimeError, match="flap"):
        cache.get_or_set("k", factory, ttl_s=60, hard_ttl_s=60, stale_if_error=False)


def test_background_failure_with_stale_if_error_keeps_last_good(
    clock: _Clock, deferred: _DeferredExecutor
) -> None:
    cache = GoldAggregateCache(now=clock, executor=deferred)
    factory = _Factory("v1")
    cache.get_or_set("k", factory, ttl_s=60, stale_if_error=True, hard_ttl_s=600)
    clock.now += 61
    assert cache.get_or_set("k", factory, ttl_s=60, stale_if_error=True, hard_ttl_s=600) == "v1"

    factory.fail = RuntimeError("refresh failed")
    deferred.run_all()

    assert cache.get_or_set("k", factory, ttl_s=60, stale_if_error=True, hard_ttl_s=600) == "v1"
    assert len(deferred.jobs) == 1, "the next stale read schedules a fresh attempt"


def test_background_failure_without_stale_if_error_evicts_the_entry(
    clock: _Clock, deferred: _DeferredExecutor
) -> None:
    cache = GoldAggregateCache(now=clock, executor=deferred)
    factory = _Factory("v1")
    cache.get_or_set("k", factory, ttl_s=60, hard_ttl_s=600)
    clock.now += 61
    assert cache.get_or_set("k", factory, ttl_s=60, hard_ttl_s=600) == "v1"

    factory.fail = RuntimeError("refresh failed")
    deferred.run_all()

    with pytest.raises(RuntimeError, match="refresh failed"):
        cache.get_or_set("k", factory, ttl_s=60, hard_ttl_s=600)


def test_zero_ttl_bypasses_the_cache(clock: _Clock, deferred: _DeferredExecutor) -> None:
    cache = GoldAggregateCache(now=clock, executor=deferred)
    factory = _Factory("v1", "v2")

    assert cache.get_or_set("k", factory, ttl_s=0) == "v1"
    assert cache.get_or_set("k", factory, ttl_s=0) == "v2"
    assert factory.calls == 2


def test_lru_bound_evicts_the_least_recently_used_key(clock: _Clock, deferred: _DeferredExecutor) -> None:
    cache = GoldAggregateCache(max_entries=2, now=clock, executor=deferred)
    a, b, c = _Factory("a1", "a2"), _Factory("b1"), _Factory("c1")
    cache.get_or_set("a", a, ttl_s=60)
    cache.get_or_set("b", b, ttl_s=60)
    cache.get_or_set("a", a, ttl_s=60)  # a is now most recent
    cache.get_or_set("c", c, ttl_s=60)  # evicts b

    assert cache.get_or_set("a", a, ttl_s=60) == "a1"
    cache.get_or_set("b", b, ttl_s=60)
    assert (a.calls, b.calls, c.calls) == (1, 2, 1)


def test_hard_cap_equal_to_soft_ttl_behaves_as_plain_ttl(clock: _Clock, deferred: _DeferredExecutor) -> None:
    cache = GoldAggregateCache(now=clock, executor=deferred)
    factory = _Factory("v1", "v2")
    get_or_set_capped(cache, "k", factory, ttl_s=60, hard_ttl_s=60)

    clock.now += 60
    assert get_or_set_capped(cache, "k", factory, ttl_s=60, hard_ttl_s=60) == "v2"
    assert deferred.jobs == [], "no stale window on a governed build step"


def test_capped_helper_passes_plain_ttl_to_an_injected_ttl_cache(clock: _Clock) -> None:
    cache = TTLCache(now=clock)
    factory = _Factory("v1", "v2")

    assert get_or_set_capped(cache, "k", factory, ttl_s=60, hard_ttl_s=60) == "v1"
    clock.now += 61
    assert get_or_set_capped(cache, "k", factory, ttl_s=60, hard_ttl_s=60) == "v2"


def test_cold_single_flight_runs_one_inline_query_for_concurrent_callers(clock: _Clock) -> None:
    cache = GoldAggregateCache(now=clock, executor=_DeferredExecutor())
    started, release = threading.Event(), threading.Event()
    calls = {"n": 0}

    def slow() -> str:
        calls["n"] += 1
        started.set()
        release.wait(5)
        return "v1"

    results: list[str] = []
    leader = threading.Thread(target=lambda: results.append(cache.get_or_set("k", slow, ttl_s=60)))
    leader.start()
    assert started.wait(5)
    follower = threading.Thread(target=lambda: results.append(cache.get_or_set("k", slow, ttl_s=60)))
    follower.start()
    release.set()
    leader.join(5)
    follower.join(5)

    assert results == ["v1", "v1"]
    assert calls["n"] == 1


def test_a_follower_of_a_failed_leader_serves_the_retained_entry_without_requerying(clock: _Clock) -> None:
    cache = GoldAggregateCache(now=clock, executor=_DeferredExecutor())
    cache.get_or_set("k", lambda: "v1", ttl_s=60, hard_ttl_s=60)
    clock.now += 61  # hard-expired: the next reads compute inline
    started, release = threading.Event(), threading.Event()
    calls = {"n": 0}

    def failing() -> str:
        calls["n"] += 1
        started.set()
        release.wait(5)
        raise RuntimeError("warehouse flap")

    results: list[str] = []

    def read() -> None:
        results.append(cache.get_or_set("k", failing, ttl_s=60, hard_ttl_s=60, stale_if_error=True))

    leader = threading.Thread(target=read)
    leader.start()
    assert started.wait(5)
    follower = threading.Thread(target=read)
    follower.start()
    assert _eventually(lambda: _inside(follower, "_follow")), "the follower is waiting on the leader"
    release.set()
    leader.join(5)
    follower.join(5)

    assert results == ["v1", "v1"]
    assert calls["n"] == 1, "the follower did not re-run the failed read"


def _inside(thread: threading.Thread, function: str) -> bool:
    frame = sys._current_frames().get(thread.ident or -1)
    while frame is not None:
        if frame.f_code.co_name == function:
            return True
        frame = frame.f_back
    return False


def _eventually(check: Callable[[], bool], timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.005)
    return False


def test_clear_discards_a_late_background_refresh(clock: _Clock, deferred: _DeferredExecutor) -> None:
    cache = GoldAggregateCache(now=clock, executor=deferred)
    factory = _Factory("v1", "v2", "v3")
    cache.get_or_set("k", factory, ttl_s=60)
    clock.now += 61
    cache.get_or_set("k", factory, ttl_s=60)

    cache.clear()
    deferred.run_all()  # computes v2, but the cache was cleared meanwhile

    assert cache.get_or_set("k", factory, ttl_s=60) == "v3"


def test_outcomes_reach_the_request_server_timing_collector(
    clock: _Clock, deferred: _DeferredExecutor
) -> None:
    cache = GoldAggregateCache(now=clock, executor=deferred)
    factory = _Factory("v1")

    def outcome_of(read: Callable[[], Any]) -> str:
        collector = server_timing.TimingCollector()
        token = server_timing._COLLECTOR.set(collector)
        try:
            read()
        finally:
            server_timing._COLLECTOR.reset(token)
        return collector.header_value(0.0).split(", ")[0]

    assert outcome_of(lambda: cache.get_or_set("k", factory, ttl_s=60)) == "cache;desc=miss"
    assert outcome_of(lambda: cache.get_or_set("k", factory, ttl_s=60)) == "cache;desc=hit"
    clock.now += 61
    assert outcome_of(lambda: cache.get_or_set("k", factory, ttl_s=60)) == "cache;desc=stale"


def test_workflow_generation_moves_forward() -> None:
    before = workflow_generation()

    assert bump_workflow_generation() == before + 1
    assert workflow_generation() == before + 1


@pytest.fixture
def released_gate() -> Iterator[threading.Event]:
    gate = threading.Event()
    try:
        yield gate
    finally:
        gate.set()


def test_blocked_gold_refreshes_never_starve_the_health_probe_pool(
    clock: _Clock, released_gate: threading.Event, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refreshes run on the dedicated two-worker pool, not the three-worker
    ``mip-swr`` pool the health probes need: with every refresh stuck on a cold
    warehouse, health still answers inside its own budget."""
    cache = GoldAggregateCache(now=clock)  # the real, default executor
    blocking_started = threading.Semaphore(0)

    def stuck() -> str:
        blocking_started.release()
        released_gate.wait(30)
        return "late"

    for key in ("a", "b", "c"):
        cache.get_or_set(key, lambda: "primed", ttl_s=60)
    clock.now += 61
    for key in ("a", "b", "c"):
        assert cache.get_or_set(key, stuck, ttl_s=60) == "primed"
    for _ in range(gold_cache.GOLD_SWR_WORKERS):
        assert blocking_started.acquire(timeout=5), "both gold refresh workers are now busy"

    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: True)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)
    health_probes._probe_cache.clear()
    try:
        started = time.monotonic()
        status, deps = health_probes.probe_snapshot()
        elapsed = time.monotonic() - started
    finally:
        health_probes._probe_cache.clear()

    assert (status, deps) == ("ok", {"warehouse": "up", "lakebase": "up", "genie": "up"})
    assert elapsed < settings.mip_health_cold_wait_budget_s

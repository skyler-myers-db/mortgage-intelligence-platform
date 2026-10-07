"""The learned gold snapshot in cache identity (audit delivery-06 remainder).

``gold_snapshot`` learns gold.source_readiness's MAX(checked_at) and moves a
generation when it advances; ``GoldAggregateCache`` and the gold-versioned
``TTLCache`` keys treat an older-generation entry as a miss. Pins:

* the first learn sets the id without a bump; a later different id bumps;
* after an advance the next read misses (inline, single-flight) and returns
  the new value, with ``reason='snapshot_advanced'``;
* a failing recompute after an advance serves the older value with the
  X-Data-Last-Good-At marker under stale_if_error, and propagates the
  original exception without it;
* no probe without a warehouse-bound read; at most one probe per TTL; a
  failed probe keeps the generation; TTL <= 0 means no probe; no watch under
  pytest unless installed;
* the workflow_key sweep is intact, the e1 invariants hold, and the closed
  TTL prefixes behave as specified (each still occurs in backend/);
* under the REAL executor's ordering (the inline miss's probe runs
  asynchronously, so it can bump the generation mid-read; ``_Deferred``) a
  single-flight follower takes the value its successful leader stored with no
  marker, only a failed leader yields a marked serve, and the read that
  overlapped the advance keeps the generation it began in.
"""
from __future__ import annotations

import functools
import logging
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Executor, Future
from pathlib import Path
from typing import Any

import pytest

from backend.config.settings import settings
from backend.services import gold_cache, gold_snapshot, resilience_cache, server_timing
from backend.services.gold_cache import (
    GoldAggregateCache,
    bump_workflow_generation,
    workflow_key,
)
from backend.services.gold_snapshot import (
    GoldSnapshotWatch,
    get_gold_snapshot_watch,
    install_gold_snapshot_watch_for_tests,
    snapshot_generation,
    snapshot_id_sql,
)
from backend.services.resilience_cache import GOLD_VERSIONED_TTL_PREFIXES, TTLCache

REPO = Path(__file__).resolve().parents[2]
_WALL0 = 1_790_000_000.0


class _Inline(Executor):
    def __init__(self) -> None:
        self.submitted = 0

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        self.submitted += 1
        future: Future[Any] = Future()
        future.set_result(fn(*args, **kwargs))
        return future


class _Deferred(Executor):
    """Holds submitted work until ``run_all``: the real executor's async ordering."""

    def __init__(self) -> None:
        self.pending: list[Callable[[], Any]] = []

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        self.pending.append(functools.partial(fn, *args, **kwargs))
        future: Future[Any] = Future()
        future.set_result(None)
        return future

    def run_all(self) -> None:
        while self.pending:
            self.pending.pop(0)()


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Probe:
    def __init__(self, *ids: str | None) -> None:
        self.ids = list(ids)
        self.calls = 0
        self.fail: BaseException | None = None

    def __call__(self) -> str | None:
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return self.ids[min(self.calls, len(self.ids)) - 1]


class _Factory:
    def __init__(self, *values: Any) -> None:
        self.values = list(values)
        self.calls = 0
        self.fail: BaseException | None = None

    def __call__(self) -> Any:
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return self.values[min(self.calls, len(self.values)) - 1]


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def watch(clock: _Clock) -> Iterator[GoldSnapshotWatch]:
    probe = _Probe("2026-10-05 02:00:00", "2026-10-06 02:00:00")
    installed = GoldSnapshotWatch(probe, ttl_s=300, now=clock)
    install_gold_snapshot_watch_for_tests(installed)
    try:
        yield installed
    finally:
        install_gold_snapshot_watch_for_tests(None)


def _probe_of(watch: GoldSnapshotWatch) -> _Probe:
    probe = watch._probe
    assert isinstance(probe, _Probe)
    return probe


def _served(read: Callable[[], Any]) -> tuple[Any, str | None]:
    collector = server_timing.TimingCollector()
    token = server_timing._COLLECTOR.set(collector)
    try:
        value = read()
    finally:
        server_timing._COLLECTOR.reset(token)
    return value, collector.last_good_at()


def _iso(wall: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(wall))


# -- the watch ----------------------------------------------------------------


def test_the_snapshot_id_is_source_readiness_checked_at() -> None:
    assert snapshot_id_sql() == (
        "SELECT CAST(MAX(checked_at) AS STRING) AS snapshot_id FROM mip.gold.source_readiness"
    )


def test_the_first_learn_sets_the_id_without_a_bump(
    watch: GoldSnapshotWatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        watch.probe_if_due()
    assert watch.snapshot_id() == "2026-10-05 02:00:00"
    assert watch.generation() == 0
    assert not [r for r in caplog.records if getattr(r, "mip_event", None) == "gold_snapshot_advanced"]


def test_a_different_id_bumps_once_and_logs_timestamps_only(
    watch: GoldSnapshotWatch, clock: _Clock, caplog: pytest.LogCaptureFixture
) -> None:
    watch.probe_if_due()
    clock.now += 300
    with caplog.at_level(logging.INFO):
        watch.probe_if_due()
    clock.now += 300
    watch.probe_if_due()  # the same id again: no bump

    assert watch.generation() == 1
    advanced = [r for r in caplog.records if getattr(r, "mip_event", None) == "gold_snapshot_advanced"]
    assert len(advanced) == 1
    assert advanced[0].mip_extras == {  # type: ignore[attr-defined]
        "generation": 1,
        "previous": "2026-10-05 02:00:00",
        "current": "2026-10-06 02:00:00",
    }


def test_at_most_one_probe_per_ttl(watch: GoldSnapshotWatch, clock: _Clock) -> None:
    watch.probe_if_due()
    watch.probe_if_due()
    clock.now += 299
    watch.probe_if_due()
    assert _probe_of(watch).calls == 1
    clock.now += 1
    watch.probe_if_due()
    assert _probe_of(watch).calls == 2


def test_a_failed_probe_keeps_the_generation_and_retries_after_the_next_ttl(
    watch: GoldSnapshotWatch, clock: _Clock, caplog: pytest.LogCaptureFixture
) -> None:
    watch.probe_if_due()
    probe = _probe_of(watch)
    probe.fail = TimeoutError("socket 10.0.0.7 timed out")
    clock.now += 300
    with caplog.at_level(logging.WARNING):
        watch.probe_if_due()
    assert watch.generation() == 0 and watch.snapshot_id() == "2026-10-05 02:00:00"
    failed = [r for r in caplog.records if getattr(r, "mip_event", None) == "gold_snapshot_probe_failed"]
    assert len(failed) == 1
    assert failed[0].mip_extras == {"exc_type": "TimeoutError"}  # type: ignore[attr-defined]
    assert "10.0.0.7" not in caplog.text

    watch.probe_if_due()
    assert probe.calls == 2, "no retry inside the same soft TTL"
    probe.fail = None
    clock.now += 300
    watch.probe_if_due()
    assert watch.generation() == 1


def test_ttl_zero_means_no_probe(clock: _Clock) -> None:
    probe = _Probe("a")
    off = GoldSnapshotWatch(probe, ttl_s=0, now=clock)
    off.probe_if_due()
    off.schedule_probe(_Inline())
    assert probe.calls == 0


def test_no_watch_runs_under_pytest_unless_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    install_gold_snapshot_watch_for_tests(None)
    assert get_gold_snapshot_watch() is None
    assert snapshot_generation() == 0
    monkeypatch.setattr(gold_snapshot, "_running_under_pytest", lambda: False)
    monkeypatch.setattr(settings, "mip_cache_ttl_s", 0)
    assert get_gold_snapshot_watch() is None, "TTL <= 0: the watch is off"


# -- GoldAggregateCache -------------------------------------------------------


def test_after_an_advance_the_next_read_misses_and_returns_the_new_value(
    watch: GoldSnapshotWatch, clock: _Clock, caplog: pytest.LogCaptureFixture
) -> None:
    cache = GoldAggregateCache(now=clock, executor=_Inline())
    hero = _Factory("hero@snapshot-1", "hero@snapshot-2")
    assert cache.get_or_set("hero", hero, ttl_s=900) == "hero@snapshot-1"
    assert watch.snapshot_id() == "2026-10-05 02:00:00", "the inline miss learned the id"

    clock.now += 300
    assert cache.get_or_set("hero", hero, ttl_s=900) == "hero@snapshot-1", "a hit never probes"
    assert _probe_of(watch).calls == 1

    # Another key's inline miss carries the probe that sees the refresh.
    cache.get_or_set("other", _Factory("other"), ttl_s=900)
    assert watch.generation() == 1

    with caplog.at_level(logging.DEBUG):
        assert cache.get_or_set("hero", hero, ttl_s=900) == "hero@snapshot-2"
    assert hero.calls == 2
    misses = [r for r in caplog.records if getattr(r, "mip_event", None) == "gold_cache_miss"]
    assert misses[-1].mip_extras["reason"] == "snapshot_advanced"  # type: ignore[attr-defined]
    assert cache._entries["hero"].generation == 1
    assert cache.get_or_set("hero", hero, ttl_s=900) == "hero@snapshot-2" and hero.calls == 2


def test_a_background_refresh_probes_first_and_stores_the_new_generation(
    watch: GoldSnapshotWatch, clock: _Clock
) -> None:
    cache = GoldAggregateCache(now=clock, executor=_Inline())
    factory = _Factory("v1", "v2")
    cache.get_or_set("k", factory, ttl_s=60, hard_ttl_s=3600)
    clock.now += 300

    assert cache.get_or_set("k", factory, ttl_s=60, hard_ttl_s=3600) == "v1", "the stale serve"
    assert watch.generation() == 1
    assert cache._entries["k"].value == "v2"
    assert cache._entries["k"].generation == 1


def test_after_an_advance_a_failing_recompute_serves_last_good_with_the_marker(
    watch: GoldSnapshotWatch, clock: _Clock
) -> None:
    wall = [_WALL0]
    cache = GoldAggregateCache(now=clock, executor=_Inline(), wall=lambda: wall[0])
    factory = _Factory("v1")
    cache.get_or_set("k", factory, ttl_s=900, stale_if_error=True)
    clock.now += 300
    wall[0] += 300
    cache.get_or_set("other", _Factory("o"), ttl_s=900)  # carries the advancing probe
    assert watch.generation() == 1
    factory.fail = RuntimeError("warehouse flap")

    served = _served(lambda: cache.get_or_set("k", factory, ttl_s=900, stale_if_error=True))

    assert served == ("v1", _iso(_WALL0))


def test_after_an_advance_a_failing_recompute_without_stale_if_error_propagates(
    watch: GoldSnapshotWatch, clock: _Clock
) -> None:
    cache = GoldAggregateCache(now=clock, executor=_Inline())
    factory = _Factory("v1")
    cache.get_or_set("k", factory, ttl_s=900)
    clock.now += 300
    cache.get_or_set("other", _Factory("o"), ttl_s=900)
    factory.fail = RuntimeError("warehouse flap")

    with pytest.raises(RuntimeError, match="warehouse flap"):
        cache.get_or_set("k", factory, ttl_s=900)


def test_no_probe_without_a_warehouse_bound_read(watch: GoldSnapshotWatch, clock: _Clock) -> None:
    executor = _Inline()
    cache = GoldAggregateCache(now=clock, executor=executor)
    cache.get_or_set("k", _Factory("v"), ttl_s=9000)
    calls = _probe_of(watch).calls
    submitted = executor.submitted
    for _ in range(5):
        clock.now += 300
        cache.get_or_set("k", _Factory("v"), ttl_s=9000)
    assert _probe_of(watch).calls == calls, "hits never reach the warehouse, so never probe"
    assert executor.submitted == submitted


def test_the_workflow_key_sweep_is_intact_across_a_snapshot_advance(
    watch: GoldSnapshotWatch, clock: _Clock
) -> None:
    cache = GoldAggregateCache(now=clock, executor=_Inline())
    old = workflow_key("snapshot-keys-preview")
    cache.get_or_set(old, _Factory("counts"), ttl_s=900)
    bump_workflow_generation()
    assert old not in cache._entries, "the dead workflow generation is dropped"
    new = workflow_key("snapshot-keys-preview")
    assert new.split(":")[0] == "snapshot-keys-preview" and new != old


def test_e1_plain_soft_window_stale_carries_no_marker(watch: GoldSnapshotWatch, clock: _Clock) -> None:
    cache = GoldAggregateCache(now=clock, executor=_Inline())
    factory = _Factory("v1", "v2")
    cache.get_or_set("k", factory, ttl_s=60, hard_ttl_s=3600)
    clock.now += 61
    assert _served(lambda: cache.get_or_set("k", factory, ttl_s=60, hard_ttl_s=3600)) == ("v1", None)


# -- TTLCache -----------------------------------------------------------------


def test_registered_ttl_prefixes_carry_the_generation(watch: GoldSnapshotWatch, clock: _Clock) -> None:
    ttl = TTLCache(now=clock)
    for prefix in GOLD_VERSIONED_TTL_PREFIXES:
        ttl.set(f"{prefix}x", "old", 900)
    ttl.set("geo:state", "unversioned", 900)
    watch.probe_if_due()
    clock.now += 300
    watch.probe_if_due()
    assert watch.generation() == 1

    for prefix in GOLD_VERSIONED_TTL_PREFIXES:
        assert ttl.get(f"{prefix}x") is None, prefix
        assert ttl.get_stale(f"{prefix}x") == "old", "get_stale still serves it"
        assert ttl.get_or_set(f"{prefix}x", lambda: "new", ttl_s=900) == "new"
    assert ttl.get("geo:state") == "unversioned", "every other key is unaffected"


def test_a_versioned_ttl_leader_failure_serves_the_older_entry_with_the_marker(
    watch: GoldSnapshotWatch, clock: _Clock
) -> None:
    wall = [_WALL0]
    ttl = TTLCache(now=clock, wall=lambda: wall[0])
    ttl.set("lead_count:{}", 41, 900)
    watch.probe_if_due()
    clock.now += 300
    watch.probe_if_due()

    def fail() -> int:
        raise RuntimeError("flap")

    assert _served(lambda: ttl.get_or_set("lead_count:{}", fail, ttl_s=900, stale_if_error=True)) == (
        41,
        _iso(_WALL0),
    )


def test_every_registered_ttl_prefix_still_occurs_in_backend() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (REPO / "backend").rglob("*.py")
        if path.name != "resilience_cache.py"
    )
    for prefix in GOLD_VERSIONED_TTL_PREFIXES:
        family = prefix.removesuffix(":")
        assert prefix in sources or f'"{family}"' in sources, (
            f"{prefix!r} no longer names a backend cache key: update GOLD_VERSIONED_TTL_PREFIXES"
        )


# -- the real executor's ordering: the probe lands mid-read -------------------


@pytest.fixture
def follower_waiting(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    """Set once a single-flight follower starts waiting on its leader's event."""
    waiting = threading.Event()

    class _SignallingEvent(threading.Event):
        def wait(self, timeout: float | None = None) -> bool:
            waiting.set()
            return super().wait(timeout)

    monkeypatch.setattr(gold_cache, "Event", _SignallingEvent)
    monkeypatch.setattr(resilience_cache, "Event", _SignallingEvent)
    return waiting


class _HeldRead:
    """A factory that blocks inside the read until the test releases it."""

    def __init__(self, value: Any, *, fail: BaseException | None = None) -> None:
        self.value = value
        self.fail = fail
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self) -> Any:
        self.calls += 1
        self.started.set()
        assert self.release.wait(5), "the test never released the read"
        if self.fail is not None:
            raise self.fail
        return self.value


def _leader_and_follower(
    read: Callable[[], Any],
    held: _HeldRead,
    follower_waiting: threading.Event,
    mid_read: Callable[[], None],
) -> dict[str, tuple[Any, str | None]]:
    """Run ``read`` as a leader held in its factory, then as a follower.

    ``mid_read`` runs while the leader is inside its read and before the
    follower joins; the follower is known to be waiting before the release.
    """
    results: dict[str, tuple[Any, str | None]] = {}

    def run(name: str) -> None:
        results[name] = _served(read)

    leader = threading.Thread(target=run, args=("leader",))
    leader.start()
    assert held.started.wait(5)
    mid_read()
    follower = threading.Thread(target=run, args=("follower",))
    follower.start()
    assert follower_waiting.wait(5), "the follower never joined the flight"
    held.release.set()
    leader.join(5)
    follower.join(5)
    assert not leader.is_alive() and not follower.is_alive()
    return results


def _events(caplog: pytest.LogCaptureFixture, name: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "mip_event", None) == name]


def test_a_follower_of_a_successful_leader_takes_its_value_without_a_marker(
    watch: GoldSnapshotWatch,
    clock: _Clock,
    follower_waiting: threading.Event,
    caplog: pytest.LogCaptureFixture,
) -> None:
    watch.probe_if_due()  # learns the first snapshot, generation 0
    clock.now += 300  # the next probe is due
    executor = _Deferred()
    cache = GoldAggregateCache(now=clock, executor=executor, wall=lambda: _WALL0)
    held = _HeldRead("hero@read-1")

    def probe_lands_mid_read() -> None:
        assert len(executor.pending) == 1, "the inline miss scheduled the probe"
        executor.run_all()  # a new snapshot: generation 1, the leader still reading
        assert watch.generation() == 1

    with caplog.at_level(logging.DEBUG):
        results = _leader_and_follower(
            lambda: cache.get_or_set("hero", held, ttl_s=900, stale_if_error=True),
            held,
            follower_waiting,
            probe_lands_mid_read,
        )

    assert results["leader"] == ("hero@read-1", None)
    assert results["follower"] == ("hero@read-1", None), "a successful leader is no stale serve"
    assert held.calls == 1
    assert not [
        r
        for r in _events(caplog, "gold_cache_stale")
        if r.mip_extras.get("reason") == "factory_error"  # type: ignore[attr-defined]
    ]


def test_a_follower_of_a_failed_leader_after_an_advance_serves_last_good_with_the_marker(
    watch: GoldSnapshotWatch, clock: _Clock, follower_waiting: threading.Event
) -> None:
    wall = [_WALL0]
    cache = GoldAggregateCache(now=clock, executor=_Deferred(), wall=lambda: wall[0])
    watch.probe_if_due()  # generation 0, the next probe due in one TTL
    cache.get_or_set("k", _Factory("v1"), ttl_s=900, stale_if_error=True)
    clock.now += 300
    wall[0] += 300
    watch.probe_if_due()
    assert watch.generation() == 1
    held = _HeldRead("never", fail=RuntimeError("warehouse flap"))

    results = _leader_and_follower(
        lambda: cache.get_or_set("k", held, ttl_s=900, stale_if_error=True),
        held,
        follower_waiting,
        lambda: None,
    )

    assert results["leader"] == ("v1", _iso(_WALL0))
    assert results["follower"] == ("v1", _iso(_WALL0)), "only a failure yields the marker"
    assert held.calls == 1


def test_a_read_that_overlaps_an_advance_keeps_the_generation_it_began_in(
    watch: GoldSnapshotWatch, clock: _Clock
) -> None:
    watch.probe_if_due()
    clock.now += 300
    executor = _Deferred()
    cache = GoldAggregateCache(now=clock, executor=executor)
    calls = [0]

    def factory() -> str:
        calls[0] += 1
        executor.run_all()  # the scheduled probe completes while the read runs
        return f"v{calls[0]}"

    assert cache.get_or_set("k", factory, ttl_s=900) == "v1"
    assert watch.generation() == 1
    assert cache._entries["k"].generation == 0, "the read may predate the refresh"
    assert cache.get_or_set("k", factory, ttl_s=900) == "v2", "so it misses once more"
    assert cache.get_or_set("k", factory, ttl_s=900) == "v2" and calls[0] == 2


def test_a_versioned_ttl_follower_of_a_successful_leader_takes_its_value_without_a_marker(
    watch: GoldSnapshotWatch, clock: _Clock, follower_waiting: threading.Event
) -> None:
    watch.probe_if_due()
    ttl = TTLCache(now=clock, wall=lambda: _WALL0)
    held = _HeldRead(41)

    def advance_mid_read() -> None:
        clock.now += 300
        watch.probe_if_due()
        assert watch.generation() == 1

    results = _leader_and_follower(
        lambda: ttl.get_or_set("lead_count:{}", held, ttl_s=900, stale_if_error=True),
        held,
        follower_waiting,
        advance_mid_read,
    )

    assert results["leader"] == (41, None)
    assert results["follower"] == (41, None), "a successful leader is no stale serve"
    assert held.calls == 1

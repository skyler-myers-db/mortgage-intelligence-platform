"""Staleness scope + TTLCache marking (audit ``delivery-06``, decision record e1).

``run_in_staleness_scope`` carries the oldest last-good wall time reported
while one factory runs; ``report_stale`` records into that scope and into the
request's Server-Timing collector (``X-Data-Last-Good-At``). ``TTLCache`` uses
the same rules as ``GoldAggregateCache``: stored wall time, factories in the
scope, degraded propagation, and a marker on ``stale_if_error`` serves and on
hits of degraded entries.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pytest

from backend.services import server_timing
from backend.services.cache_staleness import report_stale, run_in_staleness_scope
from backend.services.resilience_cache import TTLCache

_WALL0 = 1_790_000_000.0


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


# ---------------------------------------------------------------------------
# The scope.
# ---------------------------------------------------------------------------


def test_a_scope_with_no_report_returns_none() -> None:
    assert run_in_staleness_scope(lambda: "v") == ("v", None)


def test_a_scope_keeps_the_oldest_report() -> None:
    def factory() -> str:
        report_stale(_WALL0 + 50)
        report_stale(_WALL0)
        report_stale(_WALL0 + 10)
        return "v"

    assert run_in_staleness_scope(factory) == ("v", _WALL0)


def test_an_inner_scope_does_not_leak_into_the_outer_one() -> None:
    def inner() -> str:
        report_stale(_WALL0)
        return "inner"

    def outer() -> tuple[str, float | None]:
        return run_in_staleness_scope(inner)

    (value, inner_since), outer_since = run_in_staleness_scope(outer)

    assert (value, inner_since) == ("inner", _WALL0)
    # Propagation is the cache's job (it re-reports what it serves).
    assert outer_since is None


def test_the_scope_is_reset_even_when_the_factory_raises() -> None:
    def failing() -> str:
        report_stale(_WALL0)
        raise RuntimeError("boom")

    def outer() -> str:
        with pytest.raises(RuntimeError):
            run_in_staleness_scope(failing)
        return "after"

    assert run_in_staleness_scope(outer) == ("after", None)


def test_report_stale_marks_the_request_and_is_a_no_op_without_one() -> None:
    report_stale(_WALL0)  # no scope, no collector: nothing to record into

    assert _served(lambda: report_stale(_WALL0))[1] == _iso(_WALL0)


# ---------------------------------------------------------------------------
# TTLCache rules.
# ---------------------------------------------------------------------------


class _Clocks:
    def __init__(self) -> None:
        self.mono = 1000.0
        self.wall = _WALL0

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.wall += seconds

    def cache(self) -> TTLCache:
        return TTLCache(now=lambda: self.mono, wall=lambda: self.wall)


def _flaky(values: list[Any]) -> Callable[[], Any]:
    """Pops the next value; an Exception value is raised instead."""

    def factory() -> Any:
        value = values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    return factory


def test_ttl_cache_stale_if_error_serve_marks_the_stored_wall_time() -> None:
    clocks = _Clocks()
    cache = clocks.cache()
    factory = _flaky(["v1", RuntimeError("down")])
    cache.get_or_set("k", factory, ttl_s=60)
    clocks.advance(61)

    served = _served(lambda: cache.get_or_set("k", factory, ttl_s=60, stale_if_error=True))

    assert served == ("v1", _iso(_WALL0))


def test_ttl_cache_fresh_hits_and_misses_carry_no_marker() -> None:
    clocks = _Clocks()
    cache = clocks.cache()
    factory = _flaky(["v1", "v2"])

    assert _served(lambda: cache.get_or_set("k", factory, ttl_s=60)) == ("v1", None)
    assert _served(lambda: cache.get_or_set("k", factory, ttl_s=60)) == ("v1", None)
    clocks.advance(61)
    assert _served(lambda: cache.get_or_set("k", factory, ttl_s=60, stale_if_error=True)) == (
        "v2",
        None,
    )


def test_ttl_cache_value_built_from_a_stale_read_is_degraded_on_every_hit() -> None:
    clocks = _Clocks()
    inner, outer = clocks.cache(), clocks.cache()
    source = _flaky(["rows", RuntimeError("down")])
    inner.get_or_set("inner", source, ttl_s=60)
    clocks.advance(61)

    def build() -> str:
        return "built:" + inner.get_or_set("inner", source, ttl_s=60, stale_if_error=True)

    assert _served(lambda: outer.get_or_set("outer", build, ttl_s=60)) == (
        "built:rows",
        _iso(_WALL0),
    )
    # A later hit of the degraded entry (get_or_set and plain get) still marks.
    assert _served(lambda: outer.get_or_set("outer", build, ttl_s=60)) == (
        "built:rows",
        _iso(_WALL0),
    )
    assert _served(lambda: outer.get("outer")) == ("built:rows", _iso(_WALL0))


def test_ttl_cache_nested_staleness_reaches_an_outer_scope() -> None:
    clocks = _Clocks()
    cache = clocks.cache()
    factory = _flaky(["v1", RuntimeError("down")])
    cache.get_or_set("k", factory, ttl_s=60)
    clocks.advance(61)

    _, since = run_in_staleness_scope(
        lambda: cache.get_or_set("k", factory, ttl_s=60, stale_if_error=True)
    )

    assert since == _WALL0


def test_ttl_cache_legacy_get_set_and_get_stale_never_mark() -> None:
    clocks = _Clocks()
    cache = clocks.cache()
    cache.set("k", "v", 60)

    assert _served(lambda: cache.get("k")) == ("v", None)
    clocks.advance(61)
    assert _served(lambda: cache.get_stale("k")) == ("v", None)

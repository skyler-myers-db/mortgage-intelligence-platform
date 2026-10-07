"""The RUM day-aggregate accumulator and its buckets (D-platform-process-d2).

``rum_buckets`` holds 24 fixed buckets per metric with the Core Web Vitals
thresholds as exact edges; ``rum_rollup`` folds validated events into
closed-vocabulary keys and flushes them in ONE multi-row upsert. Every test
injects the client and the clock and starts no flusher thread, except the
one that proves the flusher starts lazily (and stops it).
"""

from __future__ import annotations

import logging
import random
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.schemas.telemetry import RumEvent
from backend.services import rum_buckets, rum_rollup
from backend.services.resilience import _reset_breakers_for_tests, get_breaker

_DAY = date(2026, 10, 2)


class _Cursor:
    def __init__(self, client: _FakeClient) -> None:
        self.client = client

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        if self.client.error is not None:
            raise self.client.error
        self.client.statements.append((sql, dict(params or {})))


class _Conn:
    def __init__(self, client: _FakeClient) -> None:
        self.client = client

    def cursor(self) -> _Cursor:
        return _Cursor(self.client)


class _FakeClient:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.error: BaseException | None = None
        self.transactions = 0

    @contextmanager
    def transaction(self) -> Iterator[_Conn]:
        self.transactions += 1
        yield _Conn(self)

    def inserts(self) -> list[tuple[str, dict[str, Any]]]:
        return [entry for entry in self.statements if entry[0].startswith("INSERT")]

    def zeroings(self) -> list[tuple[str, dict[str, Any]]]:
        return [entry for entry in self.statements if entry[0].startswith("UPDATE")]


class _Clock:
    def __init__(self, day: date = _DAY) -> None:
        self.now = datetime(day.year, day.month, day.day, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    _reset_breakers_for_tests()
    rum_rollup._reset_for_tests()
    try:
        yield
    finally:
        rum_rollup._reset_for_tests()
        _reset_breakers_for_tests()


def _rollup(client: _FakeClient, clock: _Clock | None = None) -> rum_rollup.RumRollup:
    return rum_rollup.RumRollup(client_factory=lambda: client, clock=clock or _Clock(), autostart=False)


def _event(metric: str, value: float, route: str = "/lead-queue", **details: object) -> RumEvent:
    return RumEvent.model_validate(
        {"metric": metric, "value": value, "rating": "info", "route": route, "details": details}
    )


def _events(caplog: pytest.LogCaptureFixture, name: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if getattr(record, "mip_event", None) == name]


def _rows_of(client: _FakeClient) -> list[dict[str, Any]]:
    [(_, params)] = client.inserts()
    count = len([key for key in params if key.startswith("slot_")])
    return [
        {column: params[f"{column}_{index}"] for column in ("metric", "route", "facet", "rating", "sample_count",
                                                            "builds", "buckets", "value_min", "value_max")}
        for index in range(count)
    ]


# --- buckets -------------------------------------------------------------


def test_every_metric_has_24_buckets_with_increasing_edges() -> None:
    for metric, edges in rum_buckets.BUCKET_EDGES.items():
        assert len(edges) == rum_buckets.NUM_BUCKETS - 1, metric
        assert list(edges) == sorted(set(edges)), metric
    assert {
        "navigation_load", "route_change", "lcp", "cls", "inp", "inp_input_delay", "inp_processing",
        "inp_presentation", "api_call", "client_error",
    } == rum_buckets.ROLLUP_METRICS


@pytest.mark.parametrize(
    ("metric", "good", "poor"),
    [
        ("lcp", 2500, 4000),
        ("inp", 200, 500),
        ("inp_input_delay", 200, 500),
        ("inp_processing", 200, 500),
        ("inp_presentation", 200, 500),
        ("cls", 0.1, 0.25),
        ("navigation_load", 1000, 2500),
        ("route_change", 1000, 2500),
    ],
)
def test_every_threshold_is_an_exact_edge_and_rates_inclusively(metric: str, good: float, poor: float) -> None:
    edges = rum_buckets.BUCKET_EDGES[metric]
    assert good in edges and poor in edges
    assert edges[rum_buckets.bucket_index(metric, good)] == good
    assert edges[rum_buckets.bucket_index(metric, poor)] == poor
    assert rum_buckets.bucket_index(metric, good * 1.0001) == rum_buckets.bucket_index(metric, good) + 1
    assert rum_buckets.rating(metric, good) == "good"
    assert rum_buckets.rating(metric, good * 1.0001) == "needs_improvement"
    assert rum_buckets.rating(metric, poor) == "needs_improvement"
    assert rum_buckets.rating(metric, poor * 1.0001) == "poor"


def test_api_call_tops_out_at_the_schema_ceiling_and_client_errors_fall_in_bucket_zero() -> None:
    assert rum_buckets.BUCKET_EDGES["api_call"][-1] == 600_000
    assert rum_buckets.bucket_index("api_call", 600_000) == rum_buckets.NUM_BUCKETS - 2
    assert rum_buckets.bucket_index("lcp", 120_000) == rum_buckets.NUM_BUCKETS - 1, "overflow"
    assert rum_buckets.bucket_index("client_error", 1) == 0
    assert rum_buckets.rating("api_call", 9) == "info" and rum_buckets.rating("client_error", 1) == "info"


@pytest.mark.parametrize(
    ("metric", "low", "high"),
    [("lcp", 300, 9000), ("inp", 10, 900), ("cls", 0.0, 0.6), ("route_change", 20, 5000), ("api_call", 5, 4000)],
)
@pytest.mark.parametrize("seed", [3, 17, 41])
def test_p75_is_within_one_bucket_of_numpy_and_its_rating_band_is_exact(
    metric: str, low: float, high: float, seed: int
) -> None:
    numpy = pytest.importorskip("numpy")
    rng = random.Random(seed)
    samples = [low + (high - low) * rng.random() ** 2 for _ in range(4000)]
    buckets = rum_buckets.zero_buckets()
    for value in samples:
        buckets[rum_buckets.bucket_index(metric, value)] += 1

    estimate = rum_buckets.p75(metric, buckets)
    reference = float(numpy.percentile(samples, 75))

    assert estimate is not None
    assert abs(rum_buckets.bucket_index(metric, estimate) - rum_buckets.bucket_index(metric, reference)) <= 1
    edges = rum_buckets.BUCKET_EDGES[metric]
    if all(abs(reference - edge) > 1e-3 * max(edge, 1e-9) for edge in edges):
        assert rum_buckets.rating(metric, estimate) == rum_buckets.rating(metric, reference)


def test_p75_of_an_empty_histogram_is_none_and_a_wrong_length_is_refused() -> None:
    assert rum_buckets.p75("lcp", rum_buckets.zero_buckets()) is None
    with pytest.raises(ValueError):
        rum_buckets.p75("lcp", [1] * 16)


# --- accumulator ----------------------------------------------------------


def test_facets_are_built_only_from_closed_values() -> None:
    api = _event("api_call", 12, api_route="/api/leads", cache="hit")
    api_bare = _event("api_call", 12)
    error = _event("client_error", 1, error_name="TypeError", error_kind="render", error_source="caught")
    bounded = _event("client_error", 1, error_name="Other", error_kind="chunk", error_source="preload", boundary="drawer")
    inp = _event("inp", 120, interaction_target="lead-row")
    lcp = _event("lcp", 1800, lcp_element="img")

    assert rum_rollup.facet_for(api) == "/api/leads|hit"
    assert rum_rollup.facet_for(api_bare) == "-|-"
    assert rum_rollup.facet_for(error) == "TypeError|render|-"
    assert rum_rollup.facet_for(bounded) == "Other|chunk|drawer"
    assert rum_rollup.facet_for(inp) == "lead-row"
    assert rum_rollup.facet_for(_event("inp", 120)) == "other"
    assert rum_rollup.facet_for(lcp) == "img"
    assert rum_rollup.facet_for(_event("lcp", 1800)) == "other"
    assert rum_rollup.facet_for(_event("cls", 0.01)) == ""
    assert rum_rollup.facet_for(_event("navigation_load", 900)) == ""


def test_one_inp_event_feeds_four_rows_with_the_server_rating() -> None:
    client = _FakeClient()
    rollup = _rollup(client)
    rollup.add(_event("inp", 520, interaction_target="filter", input_delay_ms=30, processing_ms=260, presentation_ms=230))

    rollup.flush()

    rows = {row["metric"]: row for row in _rows_of(client)}
    assert set(rows) == {"inp", "inp_input_delay", "inp_processing", "inp_presentation"}
    assert {row["facet"] for row in rows.values()} == {"filter"}
    assert rows["inp"]["rating"] == "poor", "the browser said info; the stored rating is derived"
    assert rows["inp_input_delay"]["rating"] == "good"
    assert rows["inp_processing"]["rating"] == "needs_improvement"


def test_flush_is_one_multi_row_statement_with_bound_values() -> None:
    client = _FakeClient()
    rollup = _rollup(client)
    for value in (900, 1300, 5000):
        rollup.add(_event("lcp", value, lcp_element="h1"))
    rollup.add(_event("cls", 0.02, route="/"))
    rollup.add(_event("route_change", 40, route="/glossary"))

    rollup.flush()

    [(sql, params)] = client.inserts()
    assert client.transactions == 1
    assert sql.count("INSERT") == 1 and sql.count("ON CONFLICT") == 1
    assert sql.count("%(slot_") == len(_rows_of(client)) == 4
    assert "WHERE EXCLUDED.day >= r.day" in sql
    for value in ("/lead-queue", "/glossary", "h1"):
        assert value not in sql, "values are bound, never formatted into the SQL"
    assert all(len(params[key]) == rum_buckets.NUM_BUCKETS for key in params if key.startswith("buckets_"))
    assert {params[key] for key in params if key.startswith("slot_")} == {rum_rollup.ring_slot(_DAY)}
    assert rollup.pending_keys() == []


def test_the_module_source_holds_no_row_removal() -> None:
    source = Path(rum_rollup.__file__).read_text(encoding="utf-8")
    assert "DELETE" not in source.upper()
    assert "TRUNCATE" not in source.upper()


def test_a_failure_drops_the_batch_warns_once_and_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    client = _FakeClient()
    client.error = RuntimeError("connection to server at 10.0.0.1 failed: password=hunter2")
    rollup = _rollup(client)
    rollup.add(_event("lcp", 900))
    rollup.add(_event("lcp", 5000))

    with caplog.at_level(logging.INFO):
        rollup.flush()

    [warning] = _events(caplog, "rum_rollup_flush_failed")
    assert warning.levelno == logging.WARNING
    assert warning.mip_extras == {"rows": 2, "events": 2, "dropped": 0}  # type: ignore[attr-defined]
    assert "hunter2" not in caplog.text and "10.0.0.1" not in caplog.text
    assert _events(caplog, "rum_rollup_flushed") == []
    assert rollup.pending_keys() == [], "the batch is dropped, not retried"

    client.error = None
    rollup.flush()
    assert client.inserts() == [], "nothing re-sent"


def test_an_open_breaker_skips_the_flush_and_keeps_the_aggregates() -> None:
    client = _FakeClient()
    rollup = _rollup(client)
    rollup.add(_event("lcp", 900))
    breaker = get_breaker("lakebase")
    for _ in range(10):
        breaker.record_failure()
    assert breaker.state == breaker.OPEN

    rollup.flush()

    assert client.statements == [] and client.transactions == 0
    assert len(rollup.pending_keys()) == 1

    _reset_breakers_for_tests()
    rollup.flush()
    assert len(_rows_of(client)) == 1


def test_the_zeroing_update_runs_once_per_utc_day_per_process(caplog: pytest.LogCaptureFixture) -> None:
    client = _FakeClient()
    clock = _Clock()
    rollup = _rollup(client, clock)
    for _ in range(3):
        rollup.add(_event("lcp", 900))
        rollup.flush()
    assert len(client.zeroings()) == 1
    [(sql, params)] = client.zeroings()
    assert params == {"zeros": [0] * rum_buckets.NUM_BUCKETS, "cutoff": _DAY - timedelta(days=89)}
    assert "SET sample_count = 0" in sql and "WHERE day < %(cutoff)s::date" in sql
    order = [entry[0].split()[0] for entry in client.statements]
    assert order[:2] == ["UPDATE", "INSERT"], "zero first, in the same transaction"

    clock.now += timedelta(days=1)
    rollup.add(_event("lcp", 900))
    rollup.flush()
    assert len(client.zeroings()) == 2

    caplog.clear()
    with caplog.at_level(logging.INFO):
        rollup.flush()
    assert len(client.zeroings()) == 2 and _events(caplog, "rum_rollup_flushed") == [], "an empty flush is silent"


def test_a_failed_first_flush_retries_the_zeroing_on_the_next() -> None:
    client = _FakeClient()
    rollup = _rollup(client)
    client.error = RuntimeError("down")
    rollup.add(_event("lcp", 900))
    rollup.flush()
    client.error = None
    rollup.add(_event("lcp", 900))
    rollup.flush()
    assert len(client.zeroings()) == 1


def test_long_task_is_ignored() -> None:
    client = _FakeClient()
    rollup = _rollup(client)
    rollup.add(_event("long_task", 120))
    assert rollup.pending_keys() == []
    rollup.flush()
    assert client.statements == []


def test_builds_are_capped_at_eight(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient()
    rollup = _rollup(client)
    for index in range(10):
        monkeypatch.setattr(rum_rollup.settings, "mip_git_sha", f"{index:02d}" + "f" * 38)
        rollup.add(_event("cls", 0.01, route="/"))
    monkeypatch.setattr(rum_rollup.settings, "mip_git_sha", None)
    rollup.add(_event("cls", 0.01, route="/glossary"))

    rollup.flush()

    rows = {row["route"]: row for row in _rows_of(client)}
    assert rows["/"]["sample_count"] == 10
    assert rows["/"]["builds"] == [f"{index:02d}" + "f" * 10 for index in range(8)]
    assert rows["/glossary"]["builds"] == ["unversioned"]


def test_the_key_cap_counts_drops(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rum_rollup, "MAX_PENDING_KEYS", 3)
    client = _FakeClient()
    rollup = _rollup(client)
    for value in (100, 3000, 5000):  # good, needs improvement, poor: three keys
        rollup.add(_event("lcp", value))
    rollup.add(_event("cls", 0.01))  # a fourth key: dropped
    rollup.add(_event("lcp", 150))  # an existing key: kept

    with caplog.at_level(logging.INFO):
        rollup.flush()

    [line] = _events(caplog, "rum_rollup_flushed")
    assert line.mip_extras["rows"] == 3  # type: ignore[attr-defined]
    assert line.mip_extras["events"] == 4  # type: ignore[attr-defined]
    assert line.mip_extras["dropped"] == 1  # type: ignore[attr-defined]


def test_importing_the_module_starts_no_thread() -> None:
    code = (
        "import threading, backend.services.rum_rollup as r; "
        "assert not [t for t in threading.enumerate() if t.name == r.FLUSHER_THREAD_NAME]; "
        "print('ok')"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False, timeout=120)
    assert result.returncode == 0 and result.stdout.strip() == "ok", result.stderr


def test_the_flusher_starts_lazily_on_the_first_add_and_stops() -> None:
    def flushers() -> list[threading.Thread]:
        return [t for t in threading.enumerate() if t.name == rum_rollup.FLUSHER_THREAD_NAME]

    before = len(flushers())
    rollup = rum_rollup.RumRollup(client_factory=_FakeClient, clock=_Clock(), autostart=True)
    rollup.add(_event("long_task", 10))
    assert len(flushers()) == before, "an ignored event starts nothing"
    try:
        rollup.add(_event("lcp", 900))
        assert len(flushers()) == before + 1
        rollup.add(_event("lcp", 900))
        assert len(flushers()) == before + 1, "one flusher per process"
    finally:
        rollup.stop()
    assert len(flushers()) == before


def test_flush_on_shutdown_writes_once_and_is_idempotent() -> None:
    client = _FakeClient()
    rum_rollup._reset_for_tests(client_factory=lambda: client, clock=_Clock())
    rum_rollup.add(_event("lcp", 900))

    rum_rollup.flush_on_shutdown()
    rum_rollup.flush_on_shutdown()

    assert len(client.inserts()) == 1

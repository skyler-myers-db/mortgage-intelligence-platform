"""Real-PostgreSQL contract of the browser RUM day-aggregate ring (D-platform-process-d2).

The schema before the ``rum_daily`` block is applied, then the full schema
TWICE through ``lakebase_migrate._run_transaction`` (the second run executes
the executable-hook preflight over the new CHECKs), each wrapped in
``contract_as_of``. Then the real flush SQL of ``backend.services.rum_rollup``
runs against the table: a same-day conflict increments, a same-slot conflict
for a new day resets every field (builds included), a straggler for an
older day changes nothing, a 91-day-old row for a retired route is zeroed by
the next day's first flush, builds stay capped at 8, and the CHECKs refuse
an unknown metric or rating and slot 90. Skipped unless
``MIP_TEST_POSTGRES_DSN`` names a disposable database.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

from backend.schemas.telemetry import RumEvent
from backend.services import rum_rollup
from backend.services.rum_buckets import NUM_BUCKETS
from jobs import lakebase_migrate
from tests.fixtures.lakebase_contract_prefix import contract_as_of

pytestmark = pytest.mark.integration

#: Dated at the W5c merge: the fifth and last of the five W5c blocks.
RUM_DAILY_VERSION = "2026_10_07_rum_daily"
_MARKER = "-- Browser RUM day aggregates ---"
_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
_SEED = Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")
_DAY = date(2026, 10, 2)


class _Client:
    """The one method the rollup uses, on a real connection."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection[Any]]:
        with psycopg.connect(self.dsn) as conn:
            yield conn


def _apply(dsn: str, schema_sql: str) -> None:
    pre_seed, post_seed = lakebase_migrate._split_schema_sql(schema_sql)
    with contract_as_of(schema_sql):
        lakebase_migrate._run_transaction(
            (pre_seed, _SEED, post_seed),
            {"conninfo": dsn},
            app_role="lakebase-schema-upgrade-test-role",
            verify_outreach_integrity=True,
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    value = os.environ.get("MIP_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("MIP_TEST_POSTGRES_DSN is not configured")
    with psycopg.connect(value, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")
    _apply(value, _SCHEMA[: _SCHEMA.index(_MARKER)])
    _apply(value, _SCHEMA)
    # The re-run: the executable-hook preflight now inventories the new CHECKs.
    _apply(value, _SCHEMA)
    try:
        yield value
    finally:
        with psycopg.connect(value, autocommit=True) as conn:
            conn.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")


@pytest.fixture(autouse=True)
def _empty_table(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE mip_app.rum_daily")


def _rollup(dsn: str, day: date, *, build: str = "aaaaaaaaaaaa1",
            monkeypatch: pytest.MonkeyPatch) -> rum_rollup.RumRollup:
    monkeypatch.setattr(rum_rollup.settings, "mip_git_sha", build)
    moment = datetime(day.year, day.month, day.day, 12, tzinfo=UTC)
    return rum_rollup.RumRollup(client_factory=lambda: _Client(dsn), clock=lambda: moment, autostart=False)


def _lcp(value: float, route: str = "/lead-queue") -> RumEvent:
    return RumEvent(metric="lcp", value=value, rating="good", route=route, details={"lcp_element": "h1"})


def _rows(dsn: str) -> list[dict[str, Any]]:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        return conn.execute(
            "SELECT slot, day, metric, route, facet, rating, builds, sample_count, value_sum, "
            "value_min, value_max, buckets FROM mip_app.rum_daily ORDER BY slot, metric, route, facet, rating"
        ).fetchall()


def _flush(dsn: str, day: date, events: list[RumEvent], monkeypatch: pytest.MonkeyPatch,
           *, build: str = "aaaaaaaaaaaa1") -> rum_rollup.RumRollup:
    rollup = _rollup(dsn, day, build=build, monkeypatch=monkeypatch)
    for event in events:
        rollup.add(event)
    rollup.flush()
    return rollup


def test_the_migration_is_recorded_once_and_its_checks_are_in_the_catalog(dsn: str) -> None:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        versions = conn.execute(
            "SELECT count(*) AS n FROM mip_app.schema_migrations WHERE version = %s", (RUM_DAILY_VERSION,)
        ).fetchone()
        checks = conn.execute(
            "SELECT count(*) AS n FROM pg_constraint "
            "WHERE conrelid = 'mip_app.rum_daily'::regclass AND contype = 'c'"
        ).fetchone()
    assert versions == {"n": 1}
    assert checks == {"n": 5}, "applied twice, still one CHECK per constrained column"


def test_a_same_day_conflict_increments(dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _flush(dsn, _DAY, [_lcp(1200), _lcp(1300)], monkeypatch)
    _flush(dsn, _DAY, [_lcp(900)], monkeypatch)

    [row] = _rows(dsn)
    assert (row["slot"], row["day"], row["metric"], row["facet"], row["rating"]) == (
        rum_rollup.ring_slot(_DAY), _DAY, "lcp", "h1", "good",
    )
    assert (row["sample_count"], row["value_sum"], row["value_min"], row["value_max"]) == (3, 3400.0, 900.0, 1300.0)
    assert len(row["buckets"]) == NUM_BUCKETS and sum(row["buckets"]) == 3


def test_a_same_slot_new_day_resets_every_field_builds_included(dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _flush(dsn, _DAY, [_lcp(1200), _lcp(1300)], monkeypatch, build="aaaaaaaaaaaa1")
    later = _DAY + timedelta(days=rum_rollup.RING_DAYS)
    assert rum_rollup.ring_slot(later) == rum_rollup.ring_slot(_DAY)

    # The upsert's own reset branch, isolated: this process already ran
    # today's zeroing, so the row it conflicts with still holds _DAY's sums
    # (zeroing first would make an increment look like a reset).
    rollup = _rollup(dsn, later, build="bbbbbbbbbbbb2", monkeypatch=monkeypatch)
    rollup._zeroed_day = later
    rollup.add(_lcp(2000))
    rollup.flush()

    [row] = _rows(dsn)
    assert row["day"] == later
    assert (row["sample_count"], row["value_sum"], row["value_min"], row["value_max"]) == (1, 2000.0, 2000.0, 2000.0)
    assert sum(row["buckets"]) == 1
    assert row["builds"] == ["bbbbbbbbbbbb"]


def test_a_straggler_for_an_older_day_leaves_the_row_unchanged(dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    later = _DAY + timedelta(days=rum_rollup.RING_DAYS)
    _flush(dsn, later, [_lcp(2000)], monkeypatch)
    before = _rows(dsn)

    _flush(dsn, _DAY, [_lcp(1200), _lcp(1300)], monkeypatch)

    assert _rows(dsn) == before


def test_a_91_day_old_row_for_a_retired_route_is_zeroed_by_the_next_days_first_flush(
    dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _DAY - timedelta(days=91)
    _flush(dsn, old, [_lcp(1200, route="/outreach-composer")], monkeypatch)
    _flush(dsn, _DAY - timedelta(days=89), [_lcp(1500, route="/glossary")], monkeypatch)

    _flush(dsn, _DAY, [_lcp(900)], monkeypatch)

    by_route = {row["route"]: row for row in _rows(dsn)}
    retired = by_route["/outreach-composer"]
    assert (retired["sample_count"], retired["value_sum"], retired["value_min"], retired["value_max"]) == (0, 0.0, None, None)
    assert retired["buckets"] == [0] * NUM_BUCKETS and retired["builds"] == []
    assert retired["day"] == old, "zeroed in place, never removed"
    assert by_route["/glossary"]["sample_count"] == 1, "inside 90 days: untouched"
    assert by_route["/lead-queue"]["sample_count"] == 1


def test_builds_are_capped_at_eight(dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    for index in range(10):
        _flush(dsn, _DAY, [_lcp(1200)], monkeypatch, build=f"build{index:02d}xxxxxx")

    [row] = _rows(dsn)
    assert row["sample_count"] == 10
    assert row["builds"] == sorted(f"build{index:02d}xxxxx" for index in range(10))[:8]


@pytest.mark.parametrize(
    ("column", "value"),
    [("metric", "long_task"), ("rating", "excellent"), ("slot", 90)],
)
def test_the_checks_refuse_an_unknown_metric_or_rating_and_slot_90(dsn: str, column: str, value: object) -> None:
    row: dict[str, object] = {
        "slot": 1, "day": _DAY, "metric": "lcp", "route": "/", "facet": "", "rating": "good",
        "sample_count": 1, "value_sum": 1.0, "buckets": [0] * NUM_BUCKETS,
    }
    row[column] = value
    with pytest.raises(psycopg.errors.CheckViolation), psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO mip_app.rum_daily (slot, day, metric, route, facet, rating, sample_count, value_sum, buckets) "
            "VALUES (%(slot)s, %(day)s, %(metric)s, %(route)s, %(facet)s, %(rating)s, %(sample_count)s, "
            "%(value_sum)s, %(buckets)s)",
            row,
        )

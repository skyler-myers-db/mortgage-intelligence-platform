"""Integration-style tests for DatabricksGeoRepository.

The live repository issues three SQL statements against ``mip.gold.*``
tables. We can't hit a real warehouse in unit tests, so we drive the
repository through a fake ``DatabricksSqlClient`` that records every
query + parameter binding + returns a canned row shape. This catches
regressions in:

- SQL WHERE clauses (state normalisation, FIPS parameter binding).
- Column coercion (STRING vs INT, nullable top_segment_code).
- TTL cache keying (state, fips_5 both keyed separately).
- JOIN behaviour on the state rollup (funnel_snapshot_daily LEFT JOIN
  state_top_segment).

Slice: slice13-accuracy-validation.
"""
from __future__ import annotations

from typing import Any

from backend.schemas.geo import (
    CountyRollupResponse,
    StateRollupResponse,
    ZipRollupResponse,
)
from backend.services.repositories.databricks_repo import (
    DatabricksGeoRepository,
    DatabricksSegmentRepository,
)
from backend.services.resilience import TTLCache


class _FakeSqlClient:
    """Records every call + returns canned shapes keyed on a substring
    match in the issued SQL. Tests populate ``self.responses`` before
    running the repository."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses: list[tuple[str, list[dict[str, Any]]]] = []
        self.error: Exception | None = None

    def execute(
        self,
        statement: str,
        parameters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if self.error is not None:
            raise self.error
        self.calls.append((statement, parameters or {}))
        for marker, rows in self.responses:
            if marker in statement:
                return rows
        return []

    def execute_one(
        self,
        statement: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        rows = self.execute(statement, parameters)
        return rows[0] if rows else None


def _make_repo() -> tuple[DatabricksGeoRepository, _FakeSqlClient]:
    client = _FakeSqlClient()
    repo = DatabricksGeoRepository(client, cache_ttl_s=60.0)
    return repo, client


def test_segment_list_expired_cache_failure_propagates() -> None:
    """Segment list keeps fail-visible behavior when an expired refresh fails."""
    clock = [0.0]
    client = _FakeSqlClient()
    client.responses = [
        (
            ".gold.segment_population",
            [
                {
                    "segment_code": "itm",
                    "name": "In the money",
                    "count": 1,
                    "delta_vs_prior": "+0%",
                    "avg_score": 80,
                    "description": "Ready",
                    "color": "#000000",
                }
            ],
        )
    ]
    repo = DatabricksSegmentRepository(
        client,
        cache=TTLCache(now=lambda: clock[0]),
        cache_ttl_s=10.0,
    )

    assert repo.list(None)[0].code == "itm"
    clock[0] = 11.0
    client.error = RuntimeError("warehouse down")

    try:
        repo.list(None)
    except RuntimeError as exc:
        assert str(exc) == "warehouse down"
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("expired segment cache must not mask refresh failures")


def test_segment_filtered_rollup_counts_distinct_clips() -> None:
    """Selected segment cards must count borrowers, not exploded memberships.

    A borrower can belong to several segments, and a malformed upstream array
    must not let one CLIP inflate a selected-cohort card count.
    """
    client = _FakeSqlClient()
    repo = DatabricksSegmentRepository(client, cache_ttl_s=10.0)

    repo.list(None, segment_codes=["itm", "equity"], segment_mode="all")

    assert client.calls
    statement, params = client.calls[-1]
    normalized = " ".join(statement.split())
    assert "SELECT clip, segment_codes, opportunity_score" in normalized
    assert "SELECT DISTINCT clip, sc AS segment_code, opportunity_score" in normalized
    assert "COUNT(DISTINCT clip)" in normalized
    assert "array_contains(segment_codes, :segment_0) AND array_contains(segment_codes, :segment_1)" in normalized
    assert set(params.values()) == {"itm", "equity"}


# ---------------------------------------------------------------------------
# state_rollups
# ---------------------------------------------------------------------------


def test_state_rollups_reads_funnel_and_top_segment() -> None:
    """state_rollups issues one combined SELECT that reads
    funnel_snapshot_daily LEFT JOIN state_top_segment. The SQL text
    must reference both tables so gold changes don't silently drop
    the top_segment_code extension."""
    repo, client = _make_repo()
    client.responses = [
        (
            ".gold.funnel_snapshot_daily",
            [
                {
                    "state": "IL",
                    "addressable": 1860,
                    "in_the_money": 720,
                    "top_tier_opportunities": 420,
                    "avg_score": 84,
                    "snapshot_date": "2026-04-22",
                    "top_segment_code": "itm",
                },
                {
                    "state": "CA",
                    "addressable": 900,
                    "in_the_money": 420,
                    "top_tier_opportunities": 260,
                    "avg_score": 83,
                    "snapshot_date": "2026-04-22",
                    "top_segment_code": None,
                },
            ],
        ),
    ]
    result = repo.state_rollups()
    assert isinstance(result, StateRollupResponse)
    assert result.snapshot_date == "2026-04-22"
    assert len(result.rollups) == 2
    assert result.rollups[0].state == "IL"
    assert result.rollups[0].top_segment_code == "itm"
    assert result.rollups[1].top_segment_code is None

    assert len(client.calls) == 1
    sql, _ = client.calls[0]
    assert ".gold.funnel_snapshot_daily" in sql
    assert ".gold.state_top_segment" in sql


def test_state_rollups_caches_across_calls() -> None:
    repo, client = _make_repo()
    client.responses = [
        (
            ".gold.funnel_snapshot_daily",
            [
                {
                    "state": "IL",
                    "addressable": 1860,
                    "in_the_money": 720,
                    "top_tier_opportunities": 420,
                    "avg_score": 84,
                    "snapshot_date": "2026-04-22",
                    "top_segment_code": "itm",
                },
            ],
        ),
    ]
    repo.state_rollups()
    repo.state_rollups()
    # Second call served from cache -- SQL only issued once.
    assert len(client.calls) == 1


def test_state_rollups_filtered_all_mode_reads_borrower_360() -> None:
    repo, client = _make_repo()
    client.responses = [
        (
            ".gold.borrower_360",
            [
                {
                    "state": "FL",
                    "addressable": 308,
                    "top_tier_opportunities": 120,
                    "avg_score": 63,
                },
            ],
        ),
    ]

    result = repo.state_rollups(
        segment_codes=["itm", "equity", "investor", "retention"],
        segment_mode="all",
    )

    assert result.rollups[0].state == "FL"
    assert result.rollups[0].addressable == 308
    sql, params = client.calls[0]
    assert ".gold.borrower_360" in sql
    assert "array_contains(segment_codes, :segment_0)" in sql
    assert "array_contains(segment_codes, :segment_3)" in sql
    assert params == {
        "segment_0": "equity",
        "segment_1": "investor",
        "segment_2": "itm",
        "segment_3": "retention",
    }


# ---------------------------------------------------------------------------
# county_rollups
# ---------------------------------------------------------------------------


def test_county_rollups_reads_county_rollup_table_with_state_param() -> None:
    repo, client = _make_repo()
    client.responses = [
        (
            ".gold.county_rollup",
            [
                {
                    "fips_5": "17031",
                    "state": "IL",
                    "county_name": "Cook",
                    "addressable_borrowers": 620,
                    "in_the_money_borrowers": 310,
                    "high_opportunity_borrowers": 260,
                    "avg_opportunity_score": 86,
                    "top_segment_code": "itm",
                    "snapshot_date": "2026-04-22",
                },
                {
                    "fips_5": "17043",
                    "state": "IL",
                    "county_name": "DuPage",
                    "addressable_borrowers": 310,
                    "in_the_money_borrowers": 150,
                    "high_opportunity_borrowers": 120,
                    "avg_opportunity_score": 82,
                    "top_segment_code": "equity",
                    "snapshot_date": "2026-04-22",
                },
            ],
        ),
    ]
    result = repo.county_rollups("il")  # lowercase intentionally
    assert isinstance(result, CountyRollupResponse)
    assert result.state == "IL"
    assert len(result.rollups) == 2
    assert result.rollups[0].fips_5 == "17031"
    assert result.rollups[0].county_name == "Cook"
    assert result.rollups[0].top_segment_code == "itm"

    # Parameter binding: state must be uppercased before the warehouse call.
    # The repository may issue additional scope-discovery reads for the
    # data-driven coverage chip, so assert the county-rollup call itself.
    county_calls = [
        call for call in client.calls
        if "WHERE state = :state" in call[0]
    ]
    assert len(county_calls) == 1
    sql, params = county_calls[0]
    assert ".gold.county_rollup" in sql
    assert params == {"state": "IL"}
    assert result.scope_note == (
        "Cotality data coverage: 2 counties across 1 state; "
        "2 counties available in IL"
    )


def test_county_rollups_filters_out_malformed_fips() -> None:
    """A row whose fips_5 is NULL or not 5 chars is dropped; the rest
    still resolve."""
    repo, client = _make_repo()
    client.responses = [
        (
            ".gold.county_rollup",
            [
                {
                    "fips_5": "17031",
                    "state": "IL",
                    "county_name": "Cook",
                    "addressable_borrowers": 620,
                    "in_the_money_borrowers": 310,
                    "high_opportunity_borrowers": 260,
                    "avg_opportunity_score": 86,
                    "top_segment_code": "itm",
                    "snapshot_date": "2026-04-22",
                },
                {
                    "fips_5": None,
                    "state": "IL",
                    "county_name": "Missing",
                    "addressable_borrowers": 10,
                    "in_the_money_borrowers": 0,
                    "high_opportunity_borrowers": 0,
                    "avg_opportunity_score": 50,
                    "top_segment_code": None,
                    "snapshot_date": "2026-04-22",
                },
            ],
        ),
    ]
    result = repo.county_rollups("IL")
    assert len(result.rollups) == 1
    assert result.rollups[0].fips_5 == "17031"


def test_county_rollups_caches_per_state() -> None:
    repo, client = _make_repo()
    client.responses = [
        (
            ".gold.county_rollup",
            [
                {
                    "fips_5": "06037",
                    "state": "CA",
                    "county_name": "Los Angeles",
                    "addressable_borrowers": 720,
                    "in_the_money_borrowers": 340,
                    "high_opportunity_borrowers": 290,
                    "avg_opportunity_score": 85,
                    "top_segment_code": "equity",
                    "snapshot_date": "2026-04-22",
                }
            ],
        ),
    ]
    repo.county_rollups("CA")
    repo.county_rollups("CA")
    county_calls = [call for call in client.calls if "WHERE state = :state" in call[0]]
    assert len(county_calls) == 1  # second call cached
    repo.county_rollups("IL")  # different state key -> new call
    county_calls = [call for call in client.calls if "WHERE state = :state" in call[0]]
    assert len(county_calls) == 2


def test_county_rollups_filtered_all_mode_reads_borrower_360_with_state() -> None:
    repo, client = _make_repo()
    client.responses = [
        (
            ".gold.borrower_360",
            [
                {
                    "fips_5": "12011",
                    "state": "FL",
                    "county_name": None,
                    "addressable_borrowers": 308,
                    "in_the_money_borrowers": 308,
                    "high_opportunity_borrowers": 120,
                    "avg_opportunity_score": 63,
                    "top_segment_code": "equity",
                    "snapshot_date": None,
                },
            ],
        ),
    ]

    result = repo.county_rollups(
        "fl",
        segment_codes=["itm", "investor", "equity", "retention"],
        segment_mode="all",
    )

    assert result.state == "FL"
    assert result.rollups[0].fips_5 == "12011"
    assert result.rollups[0].addressable_borrowers == 308
    sql, params = client.calls[0]
    assert ".gold.borrower_360" in sql
    assert "state = :state" in sql
    assert "array_contains(segment_codes, :segment_0)" in sql
    assert "array_contains(segment_codes, :segment_3)" in sql
    assert params == {
        "segment_0": "equity",
        "segment_1": "investor",
        "segment_2": "itm",
        "segment_3": "retention",
        "state": "FL",
    }


# ---------------------------------------------------------------------------
# zip_rollups
# ---------------------------------------------------------------------------


def test_zip_rollups_reads_zip_rollup_table_with_fips_param() -> None:
    repo, client = _make_repo()
    client.responses = [
        (
            ".gold.zip_rollup",
            [
                {
                    "zip": "60611",
                    "state": "IL",
                    "county_fips_5": "17031",
                    "addressable_borrowers": 94,
                    "avg_opportunity_score": 94,
                    "top_segment_code": "itm",
                    "sample_borrower_id": "B-48291",
                    "snapshot_date": "2026-04-22",
                },
                {
                    "zip": "60647",
                    "state": "IL",
                    "county_fips_5": "17031",
                    "addressable_borrowers": 72,
                    "avg_opportunity_score": 82,
                    "top_segment_code": "equity",
                    "sample_borrower_id": "B-48294",
                    "snapshot_date": "2026-04-22",
                },
            ],
        ),
    ]
    result = repo.zip_rollups("17031")
    assert isinstance(result, ZipRollupResponse)
    assert result.fips_5 == "17031"
    assert len(result.rollups) == 2
    assert result.rollups[0].zip == "60611"
    assert result.rollups[0].sample_borrower_id == "B-48291"

    assert len(client.calls) == 1
    sql, params = client.calls[0]
    assert ".gold.zip_rollup" in sql
    assert params == {"fips_5": "17031"}


def test_zip_rollups_empty_when_no_rows() -> None:
    repo, client = _make_repo()
    client.responses = []  # no rows match
    result = repo.zip_rollups("99999")
    assert result.rollups == []
    assert result.snapshot_date is None


def test_zip_rollups_filtered_all_mode_reads_borrower_360_with_fips() -> None:
    repo, client = _make_repo()
    client.responses = [
        (
            ".gold.borrower_360",
            [
                {
                    "zip": "33311",
                    "state": "FL",
                    "county_fips_5": "12011",
                    "addressable_borrowers": 12,
                    "avg_opportunity_score": 70,
                    "top_segment_code": "itm",
                    "sample_borrower_id": "B-12011",
                    "snapshot_date": None,
                },
            ],
        ),
    ]

    result = repo.zip_rollups(
        "12011",
        segment_codes=["itm", "investor", "equity", "retention"],
        segment_mode="all",
    )

    assert result.fips_5 == "12011"
    assert result.rollups[0].zip == "33311"
    assert result.rollups[0].addressable_borrowers == 12
    sql, params = client.calls[0]
    assert ".gold.borrower_360" in sql
    assert "county_fips_5 = :fips_5" in sql
    assert "array_contains(segment_codes, :segment_0)" in sql
    assert "array_contains(segment_codes, :segment_3)" in sql
    assert params == {
        "segment_0": "equity",
        "segment_1": "investor",
        "segment_2": "itm",
        "segment_3": "retention",
        "fips_5": "12011",
    }


# ---------------------------------------------------------------------------
# Cache semantics: single-flight + stale-if-error (perf slice 2026-06-10).
# The geo rollups are read-only visualization aggregates, so they follow the
# portfolio-preview precedent (serve last-good on transient refresh failure)
# rather than the segment-list fail-visible precedent. A COLD cache still
# fails visibly -- no fabricated empty map, per the no-mock-fallback posture.
# ---------------------------------------------------------------------------


class _SlowFakeSqlClient(_FakeSqlClient):
    """Fake client whose first execute blocks long enough for concurrent
    callers to pile onto the same cache key, proving single-flight."""

    def __init__(self, delay_s: float = 0.1) -> None:
        super().__init__()
        self._delay_s = delay_s

    def execute(
        self,
        statement: str,
        parameters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        import time as _time

        _time.sleep(self._delay_s)
        return super().execute(statement, parameters)


_STATE_ROWS = [
    {
        "state": "IL",
        "addressable": 1860,
        "in_the_money": 720,
        "top_tier_opportunities": 420,
        "avg_score": 84,
        "snapshot_date": "2026-04-22",
        "top_segment_code": "itm",
    },
]

_COUNTY_ROWS = [
    {
        "fips_5": "17031",
        "state": "IL",
        "county_name": "Cook",
        "addressable_borrowers": 900,
        "in_the_money_borrowers": 400,
        "high_opportunity_borrowers": 200,
        "avg_opportunity_score": 82,
        "top_segment_code": "itm",
        "snapshot_date": "2026-04-22",
    },
]

_ZIP_ROWS = [
    {
        "zip": "60601",
        "state": "IL",
        "county_fips_5": "17031",
        "addressable_borrowers": 120,
        "avg_opportunity_score": 81,
        "top_segment_code": "itm",
        "sample_borrower_id": "B-0000000000001",
        "snapshot_date": "2026-04-22",
    },
]


def test_state_rollups_singleflight_coalesces_concurrent_misses() -> None:
    """A burst of concurrent callers on a cold key must issue ONE
    warehouse query; followers wait for the leader instead of stampeding."""
    import threading

    client = _SlowFakeSqlClient(delay_s=0.1)
    client.responses = [(".gold.funnel_snapshot_daily", list(_STATE_ROWS))]
    repo = DatabricksGeoRepository(client, cache_ttl_s=60.0)

    results: list[StateRollupResponse] = []
    errors: list[BaseException] = []

    def _call() -> None:
        try:
            results.append(repo.state_rollups())
        except BaseException as exc:  # noqa: BLE001 -- surfaced in assertion
            errors.append(exc)

    threads = [threading.Thread(target=_call) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert not errors, errors
    assert len(results) == 4
    assert all(r.rollups[0].state == "IL" for r in results)
    # The whole point: one SQL round-trip, not four.
    assert len(client.calls) == 1, [c[0][:60] for c in client.calls]


def test_state_rollups_serves_stale_on_expired_refresh_failure() -> None:
    """Expired key + warehouse flap => last-good frame, not a blank map."""
    clock = [0.0]
    client = _FakeSqlClient()
    client.responses = [(".gold.funnel_snapshot_daily", list(_STATE_ROWS))]
    repo = DatabricksGeoRepository(
        client,
        cache=TTLCache(now=lambda: clock[0]),
        cache_ttl_s=10.0,
    )

    first = repo.state_rollups()
    assert first.rollups[0].addressable == 1860

    clock[0] = 11.0  # past TTL
    client.error = RuntimeError("warehouse down")
    second = repo.state_rollups()
    assert second.rollups[0].addressable == 1860
    assert second.rollups[0].state == "IL"


def test_state_rollups_cold_cache_failure_propagates() -> None:
    """No prior value => the failure surfaces (503 path), never an
    empty fabricated response."""
    import pytest

    client = _FakeSqlClient()
    client.error = RuntimeError("warehouse down")
    repo = DatabricksGeoRepository(client, cache_ttl_s=60.0)

    with pytest.raises(RuntimeError, match="warehouse down"):
        repo.state_rollups()


def test_county_rollups_serve_stale_on_expired_refresh_failure() -> None:
    clock = [0.0]
    client = _FakeSqlClient()
    client.responses = [(".gold.county_rollup", list(_COUNTY_ROWS))]
    repo = DatabricksGeoRepository(
        client,
        cache=TTLCache(now=lambda: clock[0]),
        cache_ttl_s=10.0,
    )

    first = repo.county_rollups("il")
    assert first.rollups[0].fips_5 == "17031"

    clock[0] = 11.0
    client.error = RuntimeError("warehouse down")
    second = repo.county_rollups("il")
    assert second.rollups[0].fips_5 == "17031"
    assert second.rollups[0].addressable_borrowers == 900


def test_zip_rollups_serve_stale_on_expired_refresh_failure() -> None:
    clock = [0.0]
    client = _FakeSqlClient()
    client.responses = [(".gold.zip_rollup", list(_ZIP_ROWS))]
    repo = DatabricksGeoRepository(
        client,
        cache=TTLCache(now=lambda: clock[0]),
        cache_ttl_s=10.0,
    )

    first = repo.zip_rollups("17031")
    assert first.rollups[0].zip == "60601"

    clock[0] = 11.0
    client.error = RuntimeError("warehouse down")
    second = repo.zip_rollups("17031")
    assert second.rollups[0].zip == "60601"

"""Addressable-vs-contactable disclosure on segment cards and state tiles.

Every headline count these repositories serve is the FULL addressable
population; the Lead Queue each card and tile links to applies the
contact-eligibility predicate. Measured live 2026-08-11 against paychex
gold, only 216,403 of 5,156,184 ``mip.gold.borrower_360`` rows are
marketing-eligible (4.2%), which left every headline ~23x above the queue
it linked to:

    segment card Prime Refi (itm)  74,335 -> queue 3,217   (23x)
    segment card Listed for Sale   49,386 -> queue 2,104   (23x)
    segment card Home Equity    3,339,173 -> queue 144,147 (23x)
    segment card Investor       1,749,208 -> queue 75,011  (23x)
    map tile IL (addressable)   1,851,040 -> queue 76,711  (24x)

Both numbers were individually correct; nothing stated the relationship,
so a reader picked up one number and landed on another.

These tests pin the fix's two load-bearing properties:

1. The reported ``contactable`` is a strict subset of the addressable
   count on every path (including a clamp against snapshot skew on the
   two paths that join a precomputed rollup to a live aggregate).
2. The predicate text comes from ``eligibility.eligible_sql_predicate``,
   not from a restatement. A second copy of the six conditions is exactly
   what the eligibility module exists to prevent -- a consent-source swap
   has to be able to change contactability in ONE place.
"""

from __future__ import annotations

from typing import Any

from backend.services.eligibility import eligible_sql_predicate
from backend.services.repositories.databricks_repo import (
    DatabricksGeoRepository,
    DatabricksSegmentRepository,
)

# Markers unique to one statement each. The fake client dispatches on a
# substring of the issued SQL, and several of these statements now read
# borrower_360, so a loose marker would silently answer the wrong query.
_UNFILTERED_SEGMENTS = "contactable_by_segment"
_FILTERED_SEGMENTS = "AS is_contactable"
_UNFILTERED_STATES = ".gold.funnel_snapshot_daily"
_FILTERED_STATES = "LENGTH(zip) <> 5"


class _FakeSqlClient:
    """Returns canned rows keyed on a substring match in the issued SQL."""

    def __init__(self, responses: list[tuple[str, list[dict[str, Any]]]]) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses = responses

    def execute(
        self,
        statement: str,
        parameters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
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


def _normalize(sql: str) -> str:
    return " ".join(sql.split())


def _segment_row(code: str, count: int, contactable: int | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "segment_code": code,
        "name": code,
        "count": count,
        "delta_vs_prior": "+0%",
        "avg_score": 60,
        "description": "",
        "color": "#000000",
    }
    if contactable is not None:
        row["contactable"] = contactable
    return row


def _state_row(state: str, addressable: int, contactable: int | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "state": state,
        "addressable": addressable,
        "in_the_money": 0,
        "top_tier_opportunities": 0,
        "avg_score": 40,
        "zip_unassigned": 0,
        "snapshot_date": "2026-08-09",
    }
    if contactable is not None:
        row["contactable"] = contactable
    return row


# ---------------------------------------------------------------------------
# The predicate has ONE owner
# ---------------------------------------------------------------------------


def test_every_contactable_aggregate_uses_the_eligibility_module_predicate() -> None:
    """The four statements embed the Python predicate verbatim.

    If someone re-types the six conditions into one of these templates it
    drifts the moment ``eligible_sql_predicate`` changes -- and a consent-
    source swap is supposed to be a one-module edit. Pinning the exact text
    makes a fork fail here instead of in production counts.
    """
    predicate = _normalize(eligible_sql_predicate())
    statements = {
        "segments (unfiltered)": DatabricksSegmentRepository._LIST_SQL,
        "segments (filtered)": DatabricksSegmentRepository._LIST_FILTERED_SQL_TPL,
        "states (unfiltered)": DatabricksGeoRepository._STATE_SQL,
        "states (filtered)": DatabricksGeoRepository._STATE_FILTER_SQL_TPL,
    }
    for label, sql in statements.items():
        normalized = _normalize(sql)
        assert predicate in normalized, f"{label} restates the eligibility predicate"
        assert "contactable" in normalized, f"{label} reports no contactable aggregate"


def test_contactable_is_not_a_gold_column() -> None:
    """The gold CTAS files stay predicate-free; the repository computes it.

    ``gold_lead_population.sql`` deliberately carries only the RAW
    eligibility columns forward so the set-based semantics cannot fork.
    Adding a ``contactable`` column there would move the predicate out of
    the module that owns it -- and would also force a gold rebuild for
    every change to it.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "sql" / "transformations"
    for path in sorted(root.glob("gold_*.sql")):
        text = path.read_text(encoding="utf-8")
        assert " AS contactable" not in text, (
            f"{path.name} declares a contactable column -- the contactability "
            "predicate belongs to backend/services/eligibility.py only"
        )


# ---------------------------------------------------------------------------
# Segment cards
# ---------------------------------------------------------------------------


def test_unfiltered_segment_cards_report_contactable_within_addressable() -> None:
    client = _FakeSqlClient(
        [(_UNFILTERED_SEGMENTS, [_segment_row("itm", 74335, 3217)])],
    )
    repo = DatabricksSegmentRepository(client, cache_ttl_s=10.0)

    (itm,) = repo.list(None)
    assert itm.count == 74335
    assert itm.contactable == 3217
    assert itm.contactable <= itm.count


def test_filtered_segment_cards_report_contactable_within_addressable() -> None:
    client = _FakeSqlClient(
        [(_FILTERED_SEGMENTS, [_segment_row("itm", 74335, 3217)])],
    )
    repo = DatabricksSegmentRepository(client, cache_ttl_s=10.0)

    (itm,) = repo.list(None, segment_codes=["itm"])
    assert itm.count == 74335
    assert itm.contactable == 3217
    assert itm.contactable <= itm.count


def test_segment_card_contactable_is_none_when_not_reported() -> None:
    """A pre-change cached frame must read "not reported", not "zero".

    Zero would render as "0 contactable of 74,335" -- a confident, wrong
    claim that nobody in the segment can be contacted.
    """
    client = _FakeSqlClient(
        [(_UNFILTERED_SEGMENTS, [_segment_row("itm", 74335, None)])],
    )
    repo = DatabricksSegmentRepository(client, cache_ttl_s=10.0)

    (itm,) = repo.list(None)
    assert itm.contactable is None


def test_segment_card_contactable_never_exceeds_the_headline() -> None:
    """Clamp the precomputed-vs-live join.

    ``gold.segment_population`` is a snapshot; the contactable aggregate is
    live over ``borrower_360``. If the snapshot lags, the subset could
    out-count its superset and the card would read "N contactable of M"
    with N > M.
    """
    client = _FakeSqlClient(
        [(_UNFILTERED_SEGMENTS, [_segment_row("itm", 100, 140)])],
    )
    repo = DatabricksSegmentRepository(client, cache_ttl_s=10.0)

    (itm,) = repo.list(None)
    assert itm.contactable == 100


# ---------------------------------------------------------------------------
# Map state tiles
# ---------------------------------------------------------------------------


def test_unfiltered_state_tiles_report_contactable_within_addressable() -> None:
    client = _FakeSqlClient(
        [(_UNFILTERED_STATES, [_state_row("IL", 1851040, 76711)])],
    )
    repo = DatabricksGeoRepository(client, cache_ttl_s=60.0)

    (il,) = repo.state_rollups().rollups
    assert il.addressable == 1851040
    assert il.contactable == 76711
    assert il.contactable <= il.addressable


def test_filtered_state_tiles_report_contactable_within_addressable() -> None:
    client = _FakeSqlClient(
        [(_FILTERED_STATES, [_state_row("IL", 40456, 1766)])],
    )
    repo = DatabricksGeoRepository(client, cache_ttl_s=60.0)

    (il,) = repo.state_rollups(segment_codes=["itm"]).rollups
    assert il.addressable == 40456
    assert il.contactable == 1766
    assert il.contactable <= il.addressable


def test_state_tile_contactable_is_none_when_not_reported() -> None:
    client = _FakeSqlClient(
        [(_UNFILTERED_STATES, [_state_row("IL", 1851040, None)])],
    )
    repo = DatabricksGeoRepository(client, cache_ttl_s=60.0)

    (il,) = repo.state_rollups().rollups
    assert il.contactable is None


def test_state_tile_contactable_never_exceeds_the_headline() -> None:
    """Same clamp as the segment card: ``funnel_snapshot_daily`` is a
    snapshot and the contactable aggregate is live over ``borrower_360``."""
    client = _FakeSqlClient(
        [(_UNFILTERED_STATES, [_state_row("IL", 100, 140)])],
    )
    repo = DatabricksGeoRepository(client, cache_ttl_s=60.0)

    (il,) = repo.state_rollups().rollups
    assert il.contactable == 100

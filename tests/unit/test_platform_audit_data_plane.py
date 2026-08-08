"""Data-plane fixes from the 2026-08-07 platform audit (C1/C2/C4, H3-H5, M3/M4).

The SQL-side behavior lands at the next gold refresh; these pin the contracts
the Python layer now exposes: the dual-count lead-queue identity (C4) and the
stale-rate deploy guard's SQL shape (C1).
"""

from __future__ import annotations

from backend.services.repositories.databricks_lead_cohort_support import (
    LeadCohortQuerySupport,
)
from tools.databricks.verify_market_rate_alignment import _ALIGNMENT_SQL


def test_identity_aggregate_carries_both_populations() -> None:
    """Audit C4: a geo filter must never report a single count that reads as
    'matches' while silently switching universes. The identity row now
    carries the geography total AND the ranked subset in one query."""

    aggregate = LeadCohortQuerySupport._identity_aggregate_select("m")
    assert "COUNT(DISTINCT m.borrower_id) AS n" in aggregate
    assert "ranked_n" in aggregate
    assert f">= {LeadCohortQuerySupport.RANKED_SCORE_FLOOR}" in aggregate
    # The floor is pinned to the lead_population CTAS quality floor.
    assert LeadCohortQuerySupport.RANKED_SCORE_FLOOR == 50


def test_parse_identity_exposes_ranked_total_with_safe_fallback() -> None:
    digest = "a" * 64
    row = {"n": 1000, "ranked_n": 120, "cohort_digest": digest, "snapshot_id": "s"}
    identity = LeadCohortQuerySupport._parse_identity(row)
    assert identity["total"] == 1000
    assert identity["ranked_total"] == 120
    # lead_population rows are all ranked by construction: a missing key must
    # fall back to the total, never a misleading zero.
    legacy = LeadCohortQuerySupport._parse_identity(
        {"n": 500, "cohort_digest": digest, "snapshot_id": "s"}
    )
    assert legacy["ranked_total"] == 500


def test_market_rate_alignment_sql_compares_gold_to_silver_latest() -> None:
    sql = _ALIGNMENT_SQL.format(catalog="mip")
    assert "mip.gold.borrower_360" in sql
    assert "mip.silver.market_rates_weekly" in sql
    assert "is_latest = TRUE" in sql

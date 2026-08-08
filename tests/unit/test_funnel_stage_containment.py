"""The workflow funnel's later stages must be subsets of the earlier ones.

2026-08-07 platform audit F5: ``GET /api/v1/analytics/executive`` rendered a
strictly-ordered funnel ending ``Approved 159 -> Actioned 4``, but the live
lifecycle mirror held ``approved+actioned = 3`` and ``rejected+actioned = 1``.
A borrower who was explicitly REJECTED at the approval stage was therefore
counted in the stage below it -- broken containment, and a governance smell
in a funnel a lender shows an auditor.

These tests pin the SQL predicate, not a canned response row, because the
defect lives in the predicate: a fake SQL client can return whatever counts
the test asks for, so asserting on the returned numbers would pass against
the broken query. ``rejected``/``hold`` rows stay visible in the lifecycle
mirror and the audit ledger; they are simply not counted as throughput.
"""
from __future__ import annotations

import re

import pytest

from backend.services.repositories.databricks_analytics import (
    DatabricksAnalyticsRepository,
)
from backend.services.repositories.databricks_portfolio import (
    DatabricksPortfolioRepository,
)


def _projection_for(sql: str, alias: str) -> str:
    """Return the single SELECT-list expression aliased to ``alias``.

    Splits on the alias and walks backwards to the comma that starts the
    projection, so the assertion reads one column's predicate rather than
    the whole statement (where an unrelated ``= 'approved'`` on the
    approved-count column would satisfy a naive substring check).
    """
    normalized = " ".join(sql.split())
    match = re.search(rf"\bAS {re.escape(alias)}\b", normalized)
    assert match is not None, f"{alias} is not projected by this statement"
    head = normalized[: match.start()]
    # The approved-count projection ends at the comma preceding this one.
    start = head.rfind(", CAST(")
    assert start != -1, f"could not isolate the {alias} projection"
    return head[start:]


@pytest.mark.parametrize(
    ("sql", "alias"),
    [
        (DatabricksAnalyticsRepository._LIVE_FUNNEL_SQL, "actioned_borrowers"),
        (DatabricksAnalyticsRepository._LIVE_WORKFLOW_COUNTS_SQL, "actioned_borrowers"),
        (DatabricksPortfolioRepository._LIVE_WORKFLOW_COUNTS_SQL, "in_outreach_count"),
    ],
)
def test_actioned_stage_requires_an_approved_borrower(sql: str, alias: str) -> None:
    projection = _projection_for(sql, alias)
    assert "outreach_status, 'none') = 'actioned'" in projection
    assert "approval_status, 'pending') = 'approved'" in projection, (
        f"{alias} must be gated on approval_status='approved' so the stage "
        "stays a subset of Approved (2026-08-07 audit F5)"
    )


def test_approved_stage_is_not_gated_on_outreach() -> None:
    """Guard the inverse mistake: Approved must stay the wider stage."""
    projection = _projection_for(
        DatabricksAnalyticsRepository._LIVE_FUNNEL_SQL, "approved_borrowers"
    )
    assert "approval_status, 'pending') = 'approved'" in projection
    assert "actioned" not in projection


class _FunnelSqlClient:
    """Minimal fake covering only the two statements ``executive()`` runs."""

    def __init__(self, totals: dict[str, object]) -> None:
        self._totals = totals

    def execute(
        self, statement: str, _parameters: object | None = None
    ) -> list[dict[str, object]]:
        if "AS addressable_borrowers" in statement:
            return [self._totals]
        if "FLOOR(opportunity_score / 5)" in statement:
            return []
        raise AssertionError(statement)


def test_executive_renders_approved_immediately_above_actioned() -> None:
    """The funnel the UI draws puts Actioned directly below Approved.

    Stage ORDER is what makes containment a promise to the reader, so it is
    pinned alongside the predicate: if the two ever swapped, the corrected
    predicate would start looking like an inversion instead of a subset.
    """
    repo = DatabricksAnalyticsRepository(  # type: ignore[arg-type]
        _FunnelSqlClient(
            {
                "snapshot_date": "2026-08-06",
                "addressable_borrowers": 5_156_184,
                "in_the_money_borrowers": 88_806,
                "high_opportunity_borrowers": 3_503,
                "offer_recommended_borrowers": 4_632_352,
                "approved_borrowers": 159,
                "actioned_borrowers": 3,
            }
        )
    )

    stages = repo.executive().stages
    by_order = {stage.stage: stage.stage_order for stage in stages}
    assert by_order["Actioned"] == by_order["Approved"] + 1

    approved = next(s for s in stages if s.stage == "Approved")
    actioned = next(s for s in stages if s.stage == "Actioned")
    assert actioned.borrower_count <= approved.borrower_count

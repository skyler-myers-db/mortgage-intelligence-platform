"""Live S4 check: two visits against a real Lakebase produce a real delta.

Gated exactly like tests/integration/test_kpi_snapshots_live.py: set
``LAKEBASE_INTEGRATION=1`` plus either static ``LAKEBASE_*`` credentials
or Databricks workspace credentials.

Scenario, executed against the deployed instance's real tables:

1. Visit one — an authenticated visit 30 minutes ago (written through the
   production INSERT SQL with an explicit timestamp so the test does not
   sleep through the dedupe window).
2. A baseline headline snapshot anchored AT that visit (synthetic sentinel
   ``snapshot_date`` so the daily upsert job's real row is never touched).
3. Visit two — "now", written through the real ``VisitTracker``.
4. ``HomeSummaryService.summary_for_actor`` anchors on visit one (visit two
   is inside the current-session grace window), resolves the nearest
   snapshot, and reports the exact signed deltas.

The warehouse-side "current" reading is injected as a deterministic stub:
this test proves the Lakebase visit-anchoring + snapshot lookup + summary
composition against real rows; the live metric-view read already has its
own coverage (kpi_deltas current_metrics + the S3 live test).
"""
from __future__ import annotations

import contextlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from backend.services.home_summary import HomeSummaryService
from backend.services.kpi_deltas import KpiDeltaService
from backend.services.lakebase import (
    LakebaseClient,
    _reset_client_for_tests,
    get_lakebase_client,
)
from backend.services.visit_tracking import VisitTracker

_HAS_STATIC_CREDS = all(
    os.environ.get(k)
    for k in ("LAKEBASE_HOST", "LAKEBASE_USER", "LAKEBASE_PASSWORD")
)
_HAS_WORKSPACE_CREDS = all(
    os.environ.get(k)
    for k in ("DATABRICKS_HOST", "DATABRICKS_TOKEN")
)
_HAS_CREDS = os.environ.get("LAKEBASE_INTEGRATION") == "1" and (
    _HAS_STATIC_CREDS or _HAS_WORKSPACE_CREDS
)

pytestmark = pytest.mark.skipif(
    not _HAS_CREDS,
    reason="Set LAKEBASE_INTEGRATION=1 + LAKEBASE_HOST/USER/PASSWORD to run",
)

# Baseline persisted to the real kpi_snapshots table; the sentinel date keeps
# it disjoint from the daily job's per-date upsert rows.
_SENTINEL_SNAPSHOT_DATE = "1999-12-31"
_BASELINE = {
    "marketable_population": 5_000_000,
    "refi_economics_screen": 250_000,
    "high_opportunity": 80_000,
    "offers_available": 400_000,
    "offers_recommended": 300_000,
    "avg_opportunity_score": 60.0,
}
# Known offsets -> the exact deltas the summary must report.
_OFFSETS = {
    "marketable_population": 10_000,
    "refi_economics_screen": 2_250,
    "high_opportunity": 1_200,
    "offers_available": 4_120,
    "offers_recommended": 900,
}


class _StubHeadlineSql:
    """Deterministic 'current' reading = baseline + known offsets."""

    def execute_one(self, statement: str, parameters: Any = None) -> dict[str, Any]:
        row = {k: v + _OFFSETS[k] for k, v in _BASELINE.items() if k in _OFFSETS}
        row["avg_opportunity_score"] = 61.5
        return row


def _client_from_env() -> LakebaseClient:
    if not _HAS_STATIC_CREDS:
        _reset_client_for_tests()
        return get_lakebase_client()
    return LakebaseClient(
        host=os.environ["LAKEBASE_HOST"],
        port=int(os.environ.get("LAKEBASE_PORT", "5432")),
        database=os.environ.get("LAKEBASE_DATABASE") or "mip_app_state",
        user=os.environ["LAKEBASE_USER"],
        password=os.environ["LAKEBASE_PASSWORD"],
        sslmode=os.environ.get("LAKEBASE_SSLMODE", "require"),
    )


def _apply_schema(client: LakebaseClient) -> None:
    schema_path = Path(__file__).resolve().parents[2] / "lakebase" / "schema.sql"
    client.execute(schema_path.read_text(encoding="utf-8"))


def _cleanup(client: LakebaseClient, actor: str) -> None:
    with contextlib.suppress(Exception):
        client.execute(
            "DELETE FROM mip_app.user_visits WHERE actor_email = %(actor)s",
            {"actor": actor},
        )
    with contextlib.suppress(Exception):
        client.execute(
            "DELETE FROM mip_app.kpi_snapshots WHERE snapshot_date = %(d)s",
            {"d": _SENTINEL_SNAPSHOT_DATE},
        )


def test_two_visits_produce_a_real_last_login_delta() -> None:
    client = _client_from_env()
    _apply_schema(client)

    actor = f"integration-s4+{uuid4().hex[:12]}@entrada.ai"
    first_visit_at = datetime.now(UTC) - timedelta(minutes=30)
    _cleanup(client, actor)
    try:
        # Visit one: 30 minutes ago (older than the 15-minute dedupe/grace
        # window, so it is the "previous login" anchor).
        client.execute(
            "INSERT INTO mip_app.user_visits (actor_email, visited_at) "
            "VALUES (%(actor)s, %(ts)s)",
            {"actor": actor, "ts": first_visit_at},
        )
        # Baseline snapshot anchored exactly at visit one.
        client.execute(
            """
            INSERT INTO mip_app.kpi_snapshots (
                snapshot_date, snapshot_at, source_view,
                marketable_population, refi_economics_screen, high_opportunity,
                offers_available, offers_recommended, avg_opportunity_score
            ) VALUES (
                %(snapshot_date)s, %(snapshot_at)s, 'portfolio_headline_metric_view',
                %(marketable_population)s, %(refi_economics_screen)s, %(high_opportunity)s,
                %(offers_available)s, %(offers_recommended)s, %(avg_opportunity_score)s
            )
            """,
            {"snapshot_date": _SENTINEL_SNAPSHOT_DATE, "snapshot_at": first_visit_at, **_BASELINE},
        )
        # Visit two: now, through the production tracker (current session).
        tracker = VisitTracker(lambda: client, window_s=60.0)
        assert tracker.maybe_claim(actor) is True
        tracker.record_visit(actor)

        service = HomeSummaryService(
            delta_service=KpiDeltaService(
                lakebase_client=client, sql_client=_StubHeadlineSql()
            ),
            spawn=lambda work: None,  # summary must stay deterministic here
        )
        summary = service.summary_for_actor(actor)

        assert summary.status == "delta"
        assert summary.phrasing_source == "deterministic"
        assert summary.previous_visit_at is not None
        assert abs((summary.previous_visit_at - first_visit_at).total_seconds()) < 5
        assert summary.baseline_snapshot_at is not None
        assert abs((summary.baseline_snapshot_at - first_visit_at).total_seconds()) < 5

        assert summary.deltas is not None
        for measure, offset in _OFFSETS.items():
            assert getattr(summary.deltas, measure) == offset, measure

        # high_opportunity: +1,200 / 80,000 = +1.5%; counts render signed.
        assert [h.display for h in summary.highlights] == ["+1.5%", "+2,250", "+4,120"]
        assert summary.headline == (
            "Since your last login: +1.5% high-opportunity, "
            "+2,250 refi candidates, +4,120 offers available."
        )
    finally:
        _cleanup(client, actor)

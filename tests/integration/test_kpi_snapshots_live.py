"""Live S3 checks against a real Lakebase instance.

Gated exactly like tests/integration/test_lakebase_round_trip.py: set
``LAKEBASE_INTEGRATION=1`` plus either static ``LAKEBASE_*`` credentials
or Databricks workspace credentials.

Proves, against the deployed instance:

1. **Backfill** -- after ``./scripts/deploy.sh -t dev`` (step 9b runs the
   ``mip_kpi_snapshot`` job), ``mip_app.kpi_snapshots`` has at least one
   row, so S4 never renders against an empty snapshot table.
2. **Round-trip** -- the delta service's nearest-snapshot lookup reads the
   real row back with sane invariants, and a visit written through the
   real ``VisitTracker`` INSERT is visible to ``previous_visit``.
"""
from __future__ import annotations

import contextlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

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


def test_kpi_snapshots_backfilled_after_deploy() -> None:
    """Backfill proof: the deploy path leaves at least one snapshot row."""
    client = _client_from_env()
    _apply_schema(client)

    row = client.fetchone("SELECT COUNT(*) AS n FROM mip_app.kpi_snapshots")
    assert row is not None
    assert int(row["n"]) >= 1, (
        "mip_app.kpi_snapshots is empty -- deploy step 9b "
        "(databricks bundle run mip_kpi_snapshot) did not run or failed"
    )

    service = KpiDeltaService(lakebase_client=client)
    snapshot = service.nearest_snapshot(datetime.now(UTC))
    assert snapshot is not None
    assert snapshot.snapshot_at.tzinfo is not None
    assert snapshot.marketable_population >= 0
    assert snapshot.refi_economics_screen <= snapshot.marketable_population
    assert snapshot.high_opportunity <= snapshot.marketable_population
    assert snapshot.offers_recommended <= snapshot.offers_available
    assert snapshot.source_view == "portfolio_headline_metric_view"


def test_user_visit_write_and_previous_visit_round_trip() -> None:
    """Visit ledger round-trip through the production INSERT + lookup SQL."""
    client = _client_from_env()
    _apply_schema(client)

    actor = f"integration-s3+{uuid4().hex[:12]}@entrada.ai"
    tracker = VisitTracker(lambda: client, window_s=60.0)
    assert tracker.maybe_claim(actor) is True
    tracker.record_visit(actor)

    try:
        service = KpiDeltaService(lakebase_client=client)
        visited_at = service.previous_visit(
            actor, before=datetime.now(UTC) + timedelta(minutes=5)
        )
        assert visited_at is not None
        assert abs((datetime.now(UTC) - visited_at).total_seconds()) < 300

        # SQL-side window guard: an immediate second INSERT inside the
        # window must be a no-op even without the in-process throttle.
        tracker.record_visit(actor)
        rows = client.fetchall(
            "SELECT visited_at FROM mip_app.user_visits "
            "WHERE actor_email = %(actor)s",
            {"actor": actor},
        )
        assert len(rows) == 1
    finally:
        with contextlib.suppress(Exception):
            client.execute(
                "DELETE FROM mip_app.user_visits WHERE actor_email = %(actor)s",
                {"actor": actor},
            )

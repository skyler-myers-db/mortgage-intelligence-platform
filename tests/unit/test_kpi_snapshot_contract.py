"""S3 packaging + parity contracts for the KPI snapshot pipeline.

Grep-style lockstep pins (same family as test_score_threshold_guard.py):

1. The bundle declares the ``mip_kpi_snapshot`` job inline (no YAML
   mirrors) with the jobs/kpi_snapshot.py task source.
2. The deploy script backfills the table (zero-click contract: a fresh
   install never leaves ``mip_app.kpi_snapshots`` empty for S4).
3. The Lakebase migration declares the snapshot + visit tables with the
   per-day unique index the job's upsert keys on.
4. The job, the delta service, and the persisted schema agree on the S1
   headline measure vocabulary, and the job's write is a per-day upsert.
"""
from __future__ import annotations

import re
from pathlib import Path

from backend.schemas.kpi_deltas import HEADLINE_COUNT_MEASURES, HEADLINE_EVENT_MEASURES
from tests.fixtures.deploy_script import deploy_entrypoint_text

REPO = Path(__file__).resolve().parents[2]
BUNDLE = (REPO / "databricks.yml").read_text(encoding="utf-8")
DEPLOY = deploy_entrypoint_text()
SCHEMA = (REPO / "lakebase" / "schema.sql").read_text(encoding="utf-8")
JOB = (REPO / "jobs" / "kpi_snapshot.py").read_text(encoding="utf-8")
SERVICE = (REPO / "backend" / "services" / "kpi_deltas.py").read_text(encoding="utf-8")
METRIC_VIEW = (
    REPO / "sql" / "metric_views" / "portfolio_headline_metric_view.sql"
).read_text(encoding="utf-8")

_ALL_SNAPSHOT_MEASURES = (*HEADLINE_COUNT_MEASURES, "avg_opportunity_score")


def test_bundle_declares_kpi_snapshot_job_inline() -> None:
    job_block = re.search(
        r"(?ms)^    mip_kpi_snapshot:\n(.*?)(?=^    \w|\Z)", BUNDLE
    )
    assert job_block, "databricks.yml must declare the mip_kpi_snapshot job inline"
    block = job_block.group(1)
    assert "jobs/kpi_snapshot.py" in block
    assert "--catalog=${var.uc_catalog}" in block
    assert "psycopg[binary]" in block
    assert "max_concurrent_runs: 1" in block
    # Daily cadence declared; ships PAUSED per the bundle-wide cost posture
    # (freshness on dev comes from the deploy-script backfill run).
    assert "quartz_cron_expression" in block
    assert "pause_status: PAUSED" in block


def test_deploy_script_backfills_kpi_snapshots() -> None:
    assert "bundle run mip_kpi_snapshot" in DEPLOY, (
        "deploy.sh must run the snapshot job so a fresh install never has an "
        "empty mip_app.kpi_snapshots table"
    )
    assert re.search(
        r"run_job_with_retry databricks bundle run mip_kpi_snapshot", DEPLOY
    ), "the backfill must use the network-flap-tolerant job runner"
    # Ordering: backfill after the gold refresh (the metric view must exist
    # and be fresh) and before the deploy completes.
    assert DEPLOY.index("bundle run mip_refresh_scores") < DEPLOY.index(
        "bundle run mip_kpi_snapshot"
    )


def test_schema_declares_snapshot_table_with_per_day_upsert_key() -> None:
    assert "CREATE TABLE IF NOT EXISTS mip_app.kpi_snapshots" in SCHEMA
    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_kpi_snapshots_snapshot_date" in SCHEMA
    assert "idx_kpi_snapshots_snapshot_at" in SCHEMA  # nearest-timestamp lookup
    for measure in _ALL_SNAPSHOT_MEASURES:
        assert measure in SCHEMA, f"kpi_snapshots is missing column {measure}"
    assert "2026_07_10_s3_kpi_snapshots_user_visits" in SCHEMA


def test_schema_declares_user_visits_ledger() -> None:
    assert "CREATE TABLE IF NOT EXISTS mip_app.user_visits" in SCHEMA
    assert "idx_user_visits_actor_visited" in SCHEMA
    # PII posture: the ledger is actor email + timestamp ONLY.
    visits_block = SCHEMA.split("CREATE TABLE IF NOT EXISTS mip_app.user_visits", 1)[1]
    visits_block = visits_block.split(";", 1)[0]
    assert "actor_email" in visits_block
    assert "visited_at" in visits_block
    forbidden = ("borrower", "clip", "address", "phone", "ip_", "user_agent", "route")
    for token in forbidden:
        assert token not in visits_block.lower(), f"user_visits must not carry {token}"


def test_job_upsert_is_idempotent_per_day() -> None:
    assert "ON CONFLICT (snapshot_date) DO UPDATE" in JOB, (
        "re-running the snapshot job on the same day must upsert, not duplicate"
    )
    assert "semantics" in JOB and "portfolio_headline_metric_view" in JOB, (
        "the snapshot must aggregate the S1 headline metric view (real UC read)"
    )


def test_job_service_and_view_agree_on_measure_vocabulary() -> None:
    """The job's aggregate aliases, the delta service's live-read aliases,
    and the persisted snapshot columns are one vocabulary."""
    for measure in _ALL_SNAPSHOT_MEASURES:
        assert re.search(rf"AS {measure}\b", JOB), f"job missing alias {measure}"
        assert re.search(rf"AS {measure}\b", SERVICE), f"service missing alias {measure}"
    # And the aliases derive from the metric view's documented indicator
    # columns, so a view refactor that renames an indicator breaks here.
    for indicator in (
        "in_the_money",
        "is_high_opportunity",
        "offer_available",
        "offer_recommended",
        "opportunity_score",
    ):
        assert indicator in METRIC_VIEW
        assert indicator in JOB
        assert indicator in SERVICE


# -- flow-05 event measures (listed for sale, competitor liens) --------------

_EVENT_MARKER = "-- KPI snapshot event measures ---"


def test_event_measures_share_one_vocabulary_across_job_service_and_view() -> None:
    for measure in HEADLINE_EVENT_MEASURES:
        assert re.search(rf"AS {measure}\b", JOB), f"job missing alias {measure}"
        assert re.search(rf"AS {measure}\b", SERVICE), f"service missing alias {measure}"
        # The upsert writes and refreshes both columns.
        assert f"%({measure})s" in JOB
        assert re.search(rf"{measure}\s+= EXCLUDED\.{measure}", JOB)
    for indicator in ("listed_for_sale", "is_competitor_lien"):
        assert indicator in METRIC_VIEW
        assert indicator in JOB
        assert indicator in SERVICE
    assert "SUM(listed_for_sale)" in METRIC_VIEW
    assert "SUM(is_competitor_lien)" in METRIC_VIEW


def test_event_measures_are_one_additive_nullable_block() -> None:
    assert SCHEMA.count(_EVENT_MARKER) == 1
    block = SCHEMA[SCHEMA.index(_EVENT_MARKER) :]
    block = block[: block.index("ON CONFLICT (version) DO NOTHING;")]
    for measure in HEADLINE_EVENT_MEASURES:
        assert f"ADD COLUMN IF NOT EXISTS {measure} BIGINT;" in block
        assert f"DROP CONSTRAINT IF EXISTS kpi_snapshots_{measure}_chk" in block
        assert f"CHECK ({measure} IS NULL OR {measure} >= 0)" in block
        assert f"COMMENT ON COLUMN mip_app.kpi_snapshots.{measure}" in block
    assert "_kpi_snapshot_event_measures'" in block
    # Never a backfill, never a new object or privilege, never a function call.
    statements = [s.strip() for s in re.sub(r"--[^\n]*", "", block).split(";") if s.strip()]
    assert not [s for s in statements if s.upper().startswith(("UPDATE", "GRANT", "CREATE"))]
    assert "NOT NULL" not in re.sub(r"--[^\n]*", "", block)
    # The CREATE TABLE stays as it was: the columns arrive only through the block.
    table = SCHEMA.split("CREATE TABLE IF NOT EXISTS mip_app.kpi_snapshots", 1)[1].split(";", 1)[0]
    for measure in HEADLINE_EVENT_MEASURES:
        assert measure not in table

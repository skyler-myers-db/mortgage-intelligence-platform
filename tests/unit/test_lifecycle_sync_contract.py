from __future__ import annotations

import importlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
JOBS_DIR = REPO / "jobs"

if str(JOBS_DIR) not in sys.path:
    sys.path.insert(0, str(JOBS_DIR))

sync_lifecycle = importlib.import_module("sync_lifecycle_state")


def _one_event() -> list[dict[str, Any]]:
    return [
        {
            "borrower_id": "B-0000000000001",
            "approval_status": "approved",
            "outreach_status": "queued",
            "offer_code": "refi",
            "approved_at": datetime(2026, 7, 14, 12, 30, tzinfo=UTC),
            "outreach_at": None,
        }
    ]


def test_one_lifecycle_event_builds_sparse_changed_row_merge() -> None:
    sql = sync_lifecycle._build_lifecycle_merge(_one_event(), catalog="mip")
    normalized = " ".join(sql.split()).upper()

    assert "MERGE INTO `MIP`.`GOLD`.`BORROWER_LIFECYCLE_STATE` AS TARGET" in normalized
    assert "'B-0000000000001'" in sql
    assert "INNER JOIN `mip`.`gold`.`borrower_360` AS b" in sql
    assert "WHEN MATCHED AND NOT" in sql
    assert "target.approval_status <=> source.approval_status" in sql
    assert "WHEN NOT MATCHED THEN INSERT" in sql

    # The source is the one durable lifecycle event. It must never synthesize
    # default rows from borrower_360 or replace the 5.16M-row target.
    assert "INSERT OVERWRITE" not in normalized
    assert "CREATE OR REPLACE TABLE" not in normalized
    assert "LEFT ANTI" not in normalized
    assert "UNION ALL" not in normalized
    assert "'PENDING'" not in normalized


def test_legacy_prune_can_only_remove_untouched_default_rows() -> None:
    sql = sync_lifecycle._build_legacy_default_prune(catalog="customer_catalog")
    normalized = " ".join(sql.split()).upper()

    assert "DELETE FROM `CUSTOMER_CATALOG`.`GOLD`.`BORROWER_LIFECYCLE_STATE`" in normalized
    assert "APPROVAL_STATUS = 'PENDING'" in normalized
    assert "OUTREACH_STATUS = 'NONE'" in normalized
    assert "OFFER_CODE IS NULL" in normalized
    assert "APPROVED_AT IS NULL" in normalized
    assert "OUTREACH_AT IS NULL" in normalized


def test_warehouse_event_path_executes_only_merge_not_population_snapshot(
    monkeypatch,
) -> None:
    from backend.services import lifecycle_sync

    class CapturingClient:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, statement: str) -> list[dict[str, Any]]:
            self.statements.append(statement)
            return []

        def execute_one(self, statement: str) -> dict[str, Any] | None:
            raise AssertionError(f"event path issued an aggregate query: {statement}")

    client = CapturingClient()
    monkeypatch.setattr(lifecycle_sync, "_resolve_connection", lambda: {"fake": True})
    monkeypatch.setattr(lifecycle_sync, "_fetch_lakebase_rows", lambda _: _one_event())

    result = lifecycle_sync.sync_lifecycle_state_via_warehouse(
        sql_client=client,  # type: ignore[arg-type]
        record_funnel_snapshot=False,
        prune_legacy_defaults=False,
    )

    assert result.lakebase_rows == 1
    assert result.mirrored_rows is None
    assert result.funnel_snapshot_rows is None
    assert len(client.statements) == 1
    assert "MERGE INTO" in client.statements[0]
    assert "funnel_snapshot_daily" not in client.statements[0]


def test_spark_repair_prunes_defaults_then_uses_same_canonical_merge(monkeypatch) -> None:
    class FakeSpark:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def sql(self, statement: str) -> None:
            self.statements.append(statement)

    spark = FakeSpark()
    monkeypatch.setattr(sync_lifecycle, "_get_spark", lambda: spark)

    sync_lifecycle._write_gold(_one_event(), catalog="customer_catalog")

    assert spark.statements == [
        sync_lifecycle._build_legacy_default_prune(catalog="customer_catalog"),
        sync_lifecycle._build_lifecycle_merge(_one_event(), catalog="customer_catalog"),
    ]


def test_lifecycle_sync_mirrors_call_disposition_actioned_semantics() -> None:
    query = sync_lifecycle._LAKEBASE_QUERY

    assert "FROM mip_app.call_dispositions d" in query
    assert "ORDER BY d.borrower_id, d.occurred_at DESC, d.created_at DESC" in query
    assert "WHEN d.outreach_at IS NOT NULL          THEN 'actioned'" in query
    assert "FULL OUTER JOIN latest_dispositions d USING (borrower_id)" in query
    assert "event_type LIKE 'OUTREACH_%'" not in query


def test_lifecycle_sync_qualifies_tables_with_configured_catalog() -> None:
    assert (
        sync_lifecycle._qualified_uc_table("customer_catalog", "gold", "borrower_lifecycle_state")
        == "`customer_catalog`.`gold`.`borrower_lifecycle_state`"
    )


def test_lifecycle_sync_rejects_unsafe_catalog_identifier() -> None:
    try:
        sync_lifecycle._qualified_uc_table("mip;DROP", "gold", "borrower_360")
    except SystemExit as exc:
        assert exc.code == 3
    else:  # pragma: no cover - defensive assertion style
        raise AssertionError("unsafe catalog identifier was accepted")


def test_bundle_recovery_is_queued_retriable_and_has_no_full_seed_task() -> None:
    bundle = yaml.safe_load((REPO / "databricks.yml").read_text(encoding="utf-8"))
    job = bundle["resources"]["jobs"]["mip_sync_lifecycle_state"]
    tasks = {task["task_key"]: task for task in job["tasks"]}

    assert job["queue"]["enabled"] is True
    assert "seed_default_state" not in tasks
    assert tasks["sync_from_lakebase"]["max_retries"] == 2
    assert tasks["sync_from_lakebase"]["retry_on_timeout"] is True
    assert tasks["sync_from_lakebase"]["min_retry_interval_millis"] == 10_000
    assert tasks["record_funnel_snapshot"]["depends_on"] == [{"task_key": "sync_from_lakebase"}]

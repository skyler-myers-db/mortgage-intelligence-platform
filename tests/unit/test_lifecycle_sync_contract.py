from __future__ import annotations

import importlib
import sqlite3
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


def _sqlite_value(value: Any) -> Any:
    if not isinstance(value, datetime):
        return value
    normalized = value.astimezone(UTC) if value.tzinfo else value
    return normalized.replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")


def _guard_allows(
    fields: tuple[str, ...],
    source: dict[str, Any],
    target: dict[str, Any],
) -> bool:
    """Execute the production SQL guard, rather than reimplementing it."""
    guard = sync_lifecycle._build_total_order_guard(fields)
    source_columns = ", ".join(f":source_{field} AS {field}" for field in fields)
    target_columns = ", ".join(f":target_{field} AS {field}" for field in fields)
    params = {
        **{f"source_{field}": _sqlite_value(source.get(field)) for field in fields},
        **{f"target_{field}": _sqlite_value(target.get(field)) for field in fields},
    }
    query = f"""
    SELECT CASE WHEN {guard} THEN 1 ELSE 0 END
    FROM (SELECT {source_columns}) AS source
    CROSS JOIN (SELECT {target_columns}) AS target
    """
    with sqlite3.connect(":memory:") as connection:
        row = connection.execute(query, params).fetchone()
    return bool(row and row[0])


def _apply_if_newer(
    fields: tuple[str, ...],
    target: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    return source.copy() if _guard_allows(fields, source, target) else target.copy()


def _schema_rows() -> list[dict[str, str]]:
    return [
        {"column_name": column}
        for column in sync_lifecycle._LIFECYCLE_SCHEMA_MIGRATIONS
    ]


def _one_event() -> list[dict[str, Any]]:
    return [
        {
            "borrower_id": "B-0000000000001",
            "approval_status": "approved",
            "outreach_status": "queued",
            "offer_code": "refi",
            "approved_at": datetime(2026, 7, 14, 12, 30, tzinfo=UTC),
            "approval_decided_at": datetime(2026, 7, 14, 12, 30, tzinfo=UTC),
            "approval_event_id": "00000000-0000-4000-8000-000000000001",
            "outreach_at": None,
            "outreach_created_at": None,
            "outreach_event_id": None,
        }
    ]


def test_one_lifecycle_event_builds_sparse_changed_row_merge() -> None:
    sql = sync_lifecycle._build_lifecycle_merge(_one_event(), catalog="mip")
    normalized = " ".join(sql.split()).upper()

    assert "MERGE INTO `MIP`.`GOLD`.`BORROWER_LIFECYCLE_STATE` AS TARGET" in normalized
    assert "'B-0000000000001'" in sql
    assert "INNER JOIN `mip`.`gold`.`borrower_360` AS b" in sql
    assert "source.approval_decided_at > target.approval_decided_at" in sql
    assert "source.outreach_at > target.outreach_at" in sql
    assert "approval_status = CASE" in sql
    assert "outreach_status = CASE" in sql
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
    assert "APPROVAL_DECIDED_AT IS NULL" in normalized
    assert "APPROVAL_EVENT_ID IS NULL" in normalized
    assert "OUTREACH_AT IS NULL" in normalized
    assert "OUTREACH_CREATED_AT IS NULL" in normalized
    assert "OUTREACH_EVENT_ID IS NULL" in normalized


def test_equal_timestamp_reversed_approval_snapshots_keep_newer_event_id() -> None:
    decided_at = datetime(2026, 7, 14, 12, 30, tzinfo=UTC)
    stale = {
        "approval_status": "approved",
        "approval_decided_at": decided_at,
        "approval_event_id": "00000000-0000-4000-8000-000000000001",
    }
    selected_newer = {
        "approval_status": "rejected",
        "approval_decided_at": decided_at,
        "approval_event_id": "00000000-0000-4000-8000-000000000002",
    }

    target = _apply_if_newer(sync_lifecycle._APPROVAL_VERSION_FIELDS, {}, stale)
    target = _apply_if_newer(
        sync_lifecycle._APPROVAL_VERSION_FIELDS,
        target,
        selected_newer,
    )
    target = _apply_if_newer(sync_lifecycle._APPROVAL_VERSION_FIELDS, target, stale)

    assert target == selected_newer


def test_equal_occurred_at_reversed_outreach_snapshots_keep_total_order_winner() -> None:
    occurred_at = datetime(2026, 7, 14, 12, 30, tzinfo=UTC)
    stale = {
        "outreach_status": "actioned",
        "event_marker": "stale",
        "outreach_at": occurred_at,
        "outreach_created_at": datetime(2026, 7, 14, 12, 31, tzinfo=UTC),
        "outreach_event_id": "00000000-0000-4000-8000-000000000099",
    }
    newer_created_at = {
        "outreach_status": "actioned",
        "event_marker": "newer-created-at",
        "outreach_at": occurred_at,
        "outreach_created_at": datetime(2026, 7, 14, 12, 32, tzinfo=UTC),
        "outreach_event_id": "00000000-0000-4000-8000-000000000001",
    }
    selected_newer = newer_created_at | {
        "event_marker": "selected-newer-id",
        "outreach_event_id": "00000000-0000-4000-8000-000000000002",
    }

    fields = sync_lifecycle._OUTREACH_VERSION_FIELDS
    target = _apply_if_newer(fields, {}, stale)
    target = _apply_if_newer(fields, target, newer_created_at)
    target = _apply_if_newer(fields, target, selected_newer)
    target = _apply_if_newer(fields, target, newer_created_at)
    target = _apply_if_newer(fields, target, stale)

    assert target == selected_newer


def test_schema_migration_is_idempotent_across_repeated_syncs() -> None:
    installed: set[str] = set()
    statements: list[str] = []

    def execute(statement: str) -> list[dict[str, Any]]:
        statements.append(statement)
        if "information_schema" in statement:
            return [{"column_name": column} for column in sorted(installed)]
        if "ALTER TABLE" in statement:
            installed.update(sync_lifecycle._LIFECYCLE_SCHEMA_MIGRATIONS)
            return []
        raise AssertionError(f"unexpected migration statement: {statement}")

    assert sync_lifecycle._ensure_lifecycle_schema(execute, catalog="mip") is True
    assert sync_lifecycle._ensure_lifecycle_schema(execute, catalog="mip") is False
    assert sum("ALTER TABLE" in statement for statement in statements) == 1
    assert installed == set(sync_lifecycle._LIFECYCLE_SCHEMA_MIGRATIONS)


def test_schema_migration_upgrades_partial_timestamp_only_target() -> None:
    installed = {"approval_decided_at"}
    statements: list[str] = []

    def execute(statement: str) -> list[dict[str, Any]]:
        statements.append(statement)
        if "information_schema" in statement:
            return [{"column_name": column} for column in sorted(installed)]
        if "ALTER TABLE" in statement:
            installed.update(
                column
                for column in sync_lifecycle._LIFECYCLE_SCHEMA_MIGRATIONS
                if f"{column} " in statement
            )
            return []
        raise AssertionError(f"unexpected migration statement: {statement}")

    assert sync_lifecycle._ensure_lifecycle_schema(execute, catalog="mip") is True
    migration = next(statement for statement in statements if "ALTER TABLE" in statement)

    assert "approval_decided_at TIMESTAMP" not in migration
    assert "approval_event_id STRING" in migration
    assert "outreach_created_at TIMESTAMP" in migration
    assert "outreach_event_id STRING" in migration
    assert installed == set(sync_lifecycle._LIFECYCLE_SCHEMA_MIGRATIONS)


def test_warehouse_event_path_executes_only_merge_not_population_snapshot(
    monkeypatch,
) -> None:
    from backend.services import lifecycle_sync

    class CapturingClient:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, statement: str) -> list[dict[str, Any]]:
            self.statements.append(statement)
            if "information_schema" in statement:
                return _schema_rows()
            return []

        def execute_one(self, statement: str) -> dict[str, Any] | None:
            raise AssertionError(f"event path issued an aggregate query: {statement}")

    client = CapturingClient()
    monkeypatch.setattr(lifecycle_sync, "_resolve_connection", lambda: {"fake": True})
    monkeypatch.setattr(lifecycle_sync, "_fetch_lakebase_rows", lambda _: _one_event())

    result = lifecycle_sync.sync_lifecycle_state_via_warehouse(
        sql_client=client,  # type: ignore[arg-type]
        record_funnel_snapshot=False,
    )

    assert result.lakebase_rows == 1
    assert result.mirrored_rows is None
    assert result.funnel_snapshot_rows is None
    assert len(client.statements) == 2
    assert "information_schema" in client.statements[0]
    assert "MERGE INTO" in client.statements[1]
    assert "DELETE FROM" not in "\n".join(client.statements)
    assert "funnel_snapshot_daily" not in client.statements[1]


def test_warehouse_prune_requires_explicit_operator_request(monkeypatch) -> None:
    from backend.services import lifecycle_sync

    class CapturingClient:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, statement: str) -> list[dict[str, Any]]:
            self.statements.append(statement)
            if "information_schema" in statement:
                return _schema_rows()
            return []

        def execute_one(self, statement: str) -> dict[str, Any] | None:
            raise AssertionError(f"explicit prune issued an aggregate query: {statement}")

    client = CapturingClient()
    monkeypatch.setattr(lifecycle_sync, "_resolve_connection", lambda: {"fake": True})
    monkeypatch.setattr(lifecycle_sync, "_fetch_lakebase_rows", lambda _: _one_event())

    lifecycle_sync.sync_lifecycle_state_via_warehouse(
        sql_client=client,  # type: ignore[arg-type]
        record_funnel_snapshot=False,
        prune_legacy_defaults=True,
    )

    assert client.statements == [
        sync_lifecycle._build_lifecycle_schema_probe(catalog="mip"),
        sync_lifecycle._build_legacy_default_prune(catalog="mip"),
        sync_lifecycle._build_lifecycle_merge(_one_event(), catalog="mip"),
    ]


def test_spark_repair_does_not_prune_defaults_without_explicit_flag(monkeypatch) -> None:
    class FakeRow:
        def __init__(self, column: str) -> None:
            self.column = column

        def asDict(self, *, recursive: bool) -> dict[str, Any]:
            assert recursive is True
            return {"column_name": self.column}

    class FakeResult:
        def collect(self) -> list[FakeRow]:
            return [FakeRow(column) for column in sync_lifecycle._LIFECYCLE_SCHEMA_MIGRATIONS]

    class FakeSpark:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def sql(self, statement: str) -> FakeResult:
            self.statements.append(statement)
            return FakeResult()

    spark = FakeSpark()
    monkeypatch.setattr(sync_lifecycle, "_get_spark", lambda: spark)

    sync_lifecycle._write_gold(_one_event(), catalog="customer_catalog")

    assert spark.statements == [
        sync_lifecycle._build_lifecycle_schema_probe(catalog="customer_catalog"),
        sync_lifecycle._build_lifecycle_merge(_one_event(), catalog="customer_catalog"),
    ]


def test_spark_repair_prunes_defaults_only_with_explicit_flag(monkeypatch) -> None:
    class FakeRow:
        def __init__(self, column: str) -> None:
            self.column = column

        def asDict(self, *, recursive: bool) -> dict[str, Any]:
            assert recursive is True
            return {"column_name": self.column}

    class FakeResult:
        def collect(self) -> list[FakeRow]:
            return [FakeRow(column) for column in sync_lifecycle._LIFECYCLE_SCHEMA_MIGRATIONS]

    class FakeSpark:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def sql(self, statement: str) -> FakeResult:
            self.statements.append(statement)
            return FakeResult()

    spark = FakeSpark()
    monkeypatch.setattr(sync_lifecycle, "_get_spark", lambda: spark)

    sync_lifecycle._write_gold(
        _one_event(),
        catalog="customer_catalog",
        prune_legacy_defaults=True,
    )

    assert spark.statements == [
        sync_lifecycle._build_lifecycle_schema_probe(catalog="customer_catalog"),
        sync_lifecycle._build_legacy_default_prune(catalog="customer_catalog"),
        sync_lifecycle._build_lifecycle_merge(_one_event(), catalog="customer_catalog"),
    ]


def test_prune_cli_flag_is_explicit_and_defaults_off() -> None:
    assert sync_lifecycle.build_parser().parse_args([]).prune_legacy_defaults is False
    assert (
        sync_lifecycle.build_parser()
        .parse_args(["--prune-legacy-defaults"])
        .prune_legacy_defaults
        is True
    )


def test_lifecycle_sync_mirrors_call_disposition_actioned_semantics() -> None:
    query = sync_lifecycle._LAKEBASE_QUERY
    normalized = " ".join(query.split()).upper()

    assert "FROM mip_app.call_dispositions d" in query
    assert (
        "ORDER BY A.BORROWER_ID, A.DECIDED_AT DESC, A.APPROVAL_ID::TEXT DESC"
        in normalized
    )
    assert (
        "ORDER BY D.BORROWER_ID, D.OCCURRED_AT DESC, D.CREATED_AT DESC, "
        "D.DISPOSITION_ID::TEXT DESC"
        in normalized
    )
    assert "a.decided_at                                         AS approval_decided_at" in query
    assert "a.approval_event_id                                  AS approval_event_id" in query
    assert "d.outreach_created_at                                AS outreach_created_at" in query
    assert "d.outreach_event_id                                  AS outreach_event_id" in query
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

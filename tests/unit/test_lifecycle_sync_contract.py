from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
JOBS_DIR = REPO / "jobs"

if str(JOBS_DIR) not in sys.path:
    sys.path.insert(0, str(JOBS_DIR))

sync_lifecycle = importlib.import_module("sync_lifecycle_state")


def test_lifecycle_sync_filters_lakebase_rows_to_current_borrowers() -> None:
    text = (REPO / "jobs" / "sync_lifecycle_state.py").read_text(encoding="utf-8")

    assert "CREATE OR REPLACE TEMP VIEW _mip_lifecycle_valid" in text
    assert "INNER JOIN {borrower_table} AS b" in text
    assert "FROM _mip_lifecycle_valid" in text
    assert "LEFT ANTI JOIN _mip_lifecycle_valid AS l" in text
    assert "FROM _mip_lifecycle_lakebase\n        UNION ALL" not in text


def test_lifecycle_sync_writes_non_null_refreshed_at_boundary() -> None:
    text = (REPO / "jobs" / "sync_lifecycle_state.py").read_text(encoding="utf-8")

    assert "sync_anchor AS" in text
    assert "CURRENT_TIMESTAMP() AS mirror_refreshed_at" in text
    assert "AS refreshed_at" in text
    assert "AS synced_at" in text


def test_lifecycle_sync_mirrors_call_disposition_actioned_semantics() -> None:
    text = (REPO / "jobs" / "sync_lifecycle_state.py").read_text(encoding="utf-8")

    assert "FROM mip_app.call_dispositions d" in text
    assert "ORDER BY d.borrower_id, d.occurred_at DESC, d.created_at DESC" in text
    assert "WHEN d.outreach_at IS NOT NULL          THEN 'actioned'" in text
    assert "FULL OUTER JOIN latest_dispositions d USING (borrower_id)" in text
    assert "event_type LIKE 'OUTREACH_%'" not in text


def test_lifecycle_sync_qualifies_tables_with_configured_catalog() -> None:
    assert (
        sync_lifecycle._qualified_uc_table(
            "customer_catalog", "gold", "borrower_lifecycle_state"
        )
        == "`customer_catalog`.`gold`.`borrower_lifecycle_state`"
    )


def test_lifecycle_sync_rejects_unsafe_catalog_identifier() -> None:
    try:
        sync_lifecycle._qualified_uc_table("mip;DROP", "gold", "borrower_360")
    except SystemExit as exc:
        assert exc.code == 3
    else:  # pragma: no cover - defensive assertion style
        raise AssertionError("unsafe catalog identifier was accepted")


def test_warehouse_lifecycle_sync_keeps_same_filter_contract() -> None:
    from backend.services.lifecycle_sync import _build_lifecycle_insert_overwrite

    sql = _build_lifecycle_insert_overwrite(
        [
            {
                "borrower_id": "B-123",
                "approval_status": "approved",
                "outreach_status": "queued",
                "offer_code": "refi",
                "approved_at": None,
                "outreach_at": None,
            }
        ],
        catalog="mip",
    )

    assert "INNER JOIN `mip`.`gold`.`borrower_360` AS b" in sql
    assert "FROM lifecycle_valid AS l" in sql
    assert "LEFT ANTI JOIN lifecycle_valid AS l" in sql
    assert "CURRENT_TIMESTAMP() AS mirror_refreshed_at" in sql
    assert "AS refreshed_at" in sql

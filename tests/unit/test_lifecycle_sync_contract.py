from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_lifecycle_sync_filters_lakebase_rows_to_current_borrowers() -> None:
    text = (REPO / "jobs" / "sync_lifecycle_state.py").read_text(encoding="utf-8")

    assert "CREATE OR REPLACE TEMP VIEW _mip_lifecycle_valid" in text
    assert "INNER JOIN mip.gold.borrower_360 AS b" in text
    assert "FROM _mip_lifecycle_valid" in text
    assert "LEFT ANTI JOIN _mip_lifecycle_valid AS l" in text
    assert "FROM _mip_lifecycle_lakebase\n        UNION ALL" not in text


def test_lifecycle_sync_writes_non_null_refreshed_at_boundary() -> None:
    text = (REPO / "jobs" / "sync_lifecycle_state.py").read_text(encoding="utf-8")

    assert "sync_anchor AS" in text
    assert "CURRENT_TIMESTAMP() AS mirror_refreshed_at" in text
    assert "AS refreshed_at" in text
    assert "AS synced_at" in text

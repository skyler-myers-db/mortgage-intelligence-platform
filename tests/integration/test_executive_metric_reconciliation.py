"""Live reconciliation gates for executive dashboard headline numbers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.integration.test_dashboard_widgets_resolve import _creds, _run_sql

REPO = Path(__file__).resolve().parents[2]
EXECUTIVE_DASHBOARD = REPO / "dashboards" / "executive_dashboard.lvdash.json"

EXECUTIVE_FIELDS = [
    "addressable_borrowers",
    "in_the_money_borrowers",
    "high_opportunity_borrowers",
    "offer_recommended_borrowers",
    "approved_borrowers",
    "actioned_borrowers",
]


@pytest.fixture(scope="module")
def warehouse() -> tuple[str, str, str]:
    creds = _creds()
    if creds is None:
        pytest.skip(
            "Executive reconciliation skipped: set DATABRICKS_HOST, "
            "DATABRICKS_TOKEN, and DATABRICKS_WAREHOUSE_ID."
        )
    return creds


def _dataset_sql(dataset_name: str) -> str:
    spec = json.loads(EXECUTIVE_DASHBOARD.read_text(encoding="utf-8"))
    for ds in spec["datasets"]:
        if ds["name"] == dataset_name:
            return "\n".join(ds["queryLines"])
    raise AssertionError(f"missing dataset {dataset_name!r}")


def _one_row_dict(
    warehouse: tuple[str, str, str],
    statement: str,
) -> dict[str, Any]:
    columns, rows = _run_sql(*warehouse, statement)
    assert len(rows) == 1, f"expected one row, got {len(rows)} for SQL:\n{statement}"
    return dict(zip(columns, rows[0], strict=True))


def _as_ints(row: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for field in EXECUTIVE_FIELDS:
        value = row[field]
        out[field] = int(value)
    return out


def test_executive_funnel_totals_match_latest_canonical_snapshot(
    warehouse: tuple[str, str, str],
) -> None:
    dashboard = _as_ints(_one_row_dict(warehouse, _dataset_sql("ds_funnel_totals")))
    canonical_sql = """
    SELECT addressable_borrowers,
           in_the_money_borrowers,
           high_opportunity_borrowers,
           offer_recommended_borrowers,
           approved_borrowers,
           actioned_borrowers
    FROM mip.gold.funnel_snapshot_daily
    WHERE state = '_ALL' AND segment_code = '_ALL'
    ORDER BY snapshot_date DESC, snapshot_at DESC
    LIMIT 1
    """.strip()
    canonical = _as_ints(_one_row_dict(warehouse, canonical_sql))
    assert dashboard == canonical


def test_executive_funnel_stages_match_canonical_totals(
    warehouse: tuple[str, str, str],
) -> None:
    columns, rows = _run_sql(*warehouse, _dataset_sql("ds_funnel_stages"))
    assert columns == ["stage", "stage_order", "borrower_count"]
    stages = {str(row[0]): int(row[2]) for row in rows}
    canonical = _as_ints(
        _one_row_dict(
            warehouse,
            """
            SELECT addressable_borrowers,
                   in_the_money_borrowers,
                   high_opportunity_borrowers,
                   offer_recommended_borrowers,
                   approved_borrowers,
                   actioned_borrowers
            FROM mip.gold.funnel_snapshot_daily
            WHERE state = '_ALL' AND segment_code = '_ALL'
            ORDER BY snapshot_date DESC, snapshot_at DESC
            LIMIT 1
            """.strip(),
        )
    )
    assert stages == {
        "Addressable": canonical["addressable_borrowers"],
        "In the Money": canonical["in_the_money_borrowers"],
        "High Opportunity (>= 75)": canonical["high_opportunity_borrowers"],
        "Offer Recommended": canonical["offer_recommended_borrowers"],
        "Approved": canonical["approved_borrowers"],
        "Actioned": canonical["actioned_borrowers"],
    }

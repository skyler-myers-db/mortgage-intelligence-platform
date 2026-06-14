"""Live source-readiness completeness checks for Module 0.

The Admin source panel is part of the data-trust story. A smoke test that only
finds "some live source" is not enough; this gate verifies expected rows,
allowed status classes, freshness, and synthetic first-party disclosure.
"""
from __future__ import annotations

import pytest

from tests.integration.test_gold_data_truth import _creds, _run_sql_rows


@pytest.fixture(scope="module")
def warehouse() -> tuple[str, str, str]:
    creds = _creds()
    if creds is None:
        pytest.skip("SQL integration test SKIPPED: set Databricks SQL env vars.")
    return creds


EXPECTED_SOURCE_NAMES = {
    "Cotality Public Records",
    "Voluntary Lien",
    "MMA Mortgage Analytics",
    "CLIP",
    "Owner Link",
    "AVM",
    "FRED Market Rates",
    "First-party LOS / Applications",
    "First-party Servicing Portfolio",
    "First-party CRM / Campaigns",
    "First-party Customer Interactions",
    "First-party Product Balances",
    "MLS Listings",
    "Cotality HELOC Propensity",
    "Cotality Refi Propensity",
    "Building Permits",
    "UC Gold Borrower 360",
    "UC Gold Lead Scores",
    "UC Gold Lead Population",
    "UC Gold Segment Population",
    "UC Gold Borrower Dossier",
}

CORE_LIVE_SOURCES = {
    "Cotality Public Records",
    "Voluntary Lien",
    "MMA Mortgage Analytics",
    "CLIP",
    "Owner Link",
    "AVM",
    "FRED Market Rates",
    "MLS Listings",
    "Cotality HELOC Propensity",
    "Cotality Refi Propensity",
    "UC Gold Borrower 360",
    "UC Gold Lead Scores",
    "UC Gold Lead Population",
    "UC Gold Segment Population",
    "UC Gold Borrower Dossier",
}

FIRST_PARTY_SOURCES = {
    "First-party LOS / Applications",
    "First-party Servicing Portfolio",
    "First-party CRM / Campaigns",
    "First-party Customer Interactions",
    "First-party Product Balances",
}

ROADMAP_SOURCES = {"Building Permits"}

ALLOWED_STATUSES = {
    "live",
    "demo_synthetic",
    "configured_empty",
    "not_configured",
    "roadmap",
    "error",
}


def _boolish(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1"}
    return bool(value)


def test_source_readiness_rows_are_complete_and_statused(
    warehouse: tuple[str, str, str],
) -> None:
    host, token, wid = warehouse
    rows = _run_sql_rows(
        host,
        token,
        wid,
        """
        SELECT source_name, status, row_count, last_updated, synthetic_demo, checked_at
        FROM mip.gold.source_readiness
        """,
    )

    assert rows, "source_readiness returned no rows"
    by_name = {str(row[0]): row for row in rows}
    missing_sources = EXPECTED_SOURCE_NAMES - set(by_name)
    assert not missing_sources

    for source_name, status, row_count, last_updated, synthetic_demo, checked_at in rows:
        assert status in ALLOWED_STATUSES, source_name
        assert checked_at is not None, source_name
        if _boolish(synthetic_demo):
            assert status == "demo_synthetic", source_name
        if status == "roadmap":
            assert source_name in ROADMAP_SOURCES

        if source_name in CORE_LIVE_SOURCES:
            assert status == "live", source_name
            assert row_count is not None and int(row_count) > 0, source_name
            assert last_updated is not None, source_name
        elif source_name in FIRST_PARTY_SOURCES:
            assert status in {"live", "demo_synthetic"}, source_name
            assert row_count is not None and int(row_count) > 0, source_name
            assert last_updated is not None, source_name
        elif source_name in ROADMAP_SOURCES:
            assert status == "roadmap", source_name


def test_source_readiness_checked_at_is_current(
    warehouse: tuple[str, str, str],
) -> None:
    host, token, wid = warehouse
    rows = _run_sql_rows(
        host,
        token,
        wid,
        """
        SELECT COUNT(*) AS stale_rows
        FROM mip.gold.source_readiness
        WHERE checked_at IS NULL
           OR checked_at < current_timestamp() - INTERVAL 3 DAYS
        """,
    )

    assert rows, "source-readiness freshness query returned no rows"
    assert int(rows[0][0]) == 0


def test_source_readiness_distinguishes_synthetic_first_party_from_live(
    warehouse: tuple[str, str, str],
) -> None:
    host, token, wid = warehouse
    rows = _run_sql_rows(
        host,
        token,
        wid,
        """
        SELECT COUNT(*) AS mislabeled
        FROM mip.gold.source_readiness
        WHERE source_name LIKE 'First-party%'
          AND synthetic_demo = TRUE
          AND status <> 'demo_synthetic'
        """,
    )

    assert rows, "synthetic first-party readiness query returned no rows"
    assert int(rows[0][0]) == 0

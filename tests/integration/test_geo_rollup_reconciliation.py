"""Live geography rollup reconciliation for Module 0 maps.

These tests keep the map drill-down honest as the Cotality footprint changes.
They reconcile the materialized geography rollups and the filtered drill-down
semantics back to ``mip.gold.borrower_360`` instead of accepting UI-only smoke.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas.portfolio import PortfolioCriteria
from backend.services.audit_store import get_audit_store
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.repositories import (
    get_geo_repository,
    get_lead_repository,
    get_portfolio_repository,
)
from backend.services.repositories.databricks_repo import (
    DatabricksGeoRepository,
    DatabricksLeadRepository,
    DatabricksPortfolioRepository,
)
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore
from tests.integration.test_gold_data_truth import _creds, _run_sql_rows


@pytest.fixture(scope="module")
def warehouse() -> tuple[str, str, str]:
    creds = _creds()
    if creds is None:
        pytest.skip("SQL integration test SKIPPED: set Databricks SQL env vars.")
    return creds


@contextmanager
def live_api_client(warehouse: tuple[str, str, str]) -> Iterator[TestClient]:
    """Route through FastAPI while using live Databricks repositories.

    tests/conftest.py installs in-process repository overrides globally so
    ordinary unit tests never touch the warehouse. This integration fixture
    temporarily swaps only the data-read repositories to Databricks-backed
    implementations and keeps audit writes in-memory, giving us production
    endpoint routing plus live SQL truth without Lakebase side effects.
    """
    host, token, warehouse_id = warehouse
    sql_client = DatabricksSqlClient(host, token, warehouse_id)
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_geo_repository] = lambda: DatabricksGeoRepository(sql_client)
    app.dependency_overrides[get_lead_repository] = lambda: DatabricksLeadRepository(sql_client)
    app.dependency_overrides[get_portfolio_repository] = lambda: DatabricksPortfolioRepository(sql_client)
    app.dependency_overrides[get_audit_store] = lambda: InMemoryAuditStore()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


SEGMENT_ALL = ("itm", "investor", "equity", "retention")


def _segment_all_clause(alias: str = "b") -> str:
    return " AND ".join(
        f"array_contains({alias}.segment_codes, '{code}')" for code in SEGMENT_ALL
    )


def test_county_and_zip_rollups_reconcile_to_borrower_360(
    warehouse: tuple[str, str, str],
) -> None:
    host, token, wid = warehouse
    rows = _run_sql_rows(
        host,
        token,
        wid,
        """
        WITH borrower_counties AS (
          SELECT county_fips_5 AS fips_5, COUNT(*) AS borrowers
          FROM mip.gold.borrower_360
          WHERE county_fips_5 IS NOT NULL AND LENGTH(county_fips_5) = 5
          GROUP BY county_fips_5
        ),
        county_diff AS (
          SELECT COUNT(*) AS mismatched_counties
          FROM borrower_counties AS b
          FULL OUTER JOIN mip.gold.county_rollup AS c
            ON c.fips_5 = b.fips_5
          WHERE COALESCE(b.borrowers, -1) <> COALESCE(c.addressable_borrowers, -1)
        ),
        borrower_zips AS (
          SELECT state, county_fips_5, zip, COUNT(*) AS borrowers
          FROM mip.gold.borrower_360
          WHERE zip IS NOT NULL AND LENGTH(zip) = 5
          GROUP BY state, county_fips_5, zip
        ),
        zip_diff AS (
          SELECT COUNT(*) AS mismatched_zips
          FROM borrower_zips AS b
          FULL OUTER JOIN mip.gold.zip_rollup AS z
            ON z.state = b.state
           AND COALESCE(z.county_fips_5, '') = COALESCE(b.county_fips_5, '')
           AND z.zip = b.zip
          WHERE COALESCE(b.borrowers, -1) <> COALESCE(z.addressable_borrowers, -1)
        )
        SELECT
          (SELECT mismatched_counties FROM county_diff) AS mismatched_counties,
          (SELECT mismatched_zips FROM zip_diff) AS mismatched_zips
        """,
    )

    assert rows, "geo reconciliation query returned no rows"
    mismatched_counties, mismatched_zips = map(int, rows[0])
    assert mismatched_counties == 0
    assert mismatched_zips == 0


def test_multisegment_geo_drilldown_counts_are_same_filter_grain(
    warehouse: tuple[str, str, str],
) -> None:
    host, token, wid = warehouse
    rows = _run_sql_rows(
        host,
        token,
        wid,
        """
        WITH filtered AS (
          SELECT state, county_fips_5, zip
          FROM mip.gold.borrower_360
          WHERE array_contains(segment_codes, 'itm')
            AND array_contains(segment_codes, 'investor')
            AND array_contains(segment_codes, 'equity')
            AND array_contains(segment_codes, 'retention')
        ),
        geo_filtered AS (
          SELECT state, county_fips_5, zip
          FROM filtered
          WHERE county_fips_5 IS NOT NULL AND zip IS NOT NULL
        ),
        state_counts AS (
          SELECT state, COUNT(*) AS borrowers
          FROM geo_filtered
          GROUP BY state
        ),
        county_counts AS (
          SELECT state, county_fips_5, COUNT(*) AS borrowers
          FROM geo_filtered
          GROUP BY state, county_fips_5
        ),
        zip_counts AS (
          SELECT county_fips_5, zip, COUNT(*) AS borrowers
          FROM geo_filtered
          GROUP BY county_fips_5, zip
        ),
        county_to_state AS (
          SELECT state, SUM(borrowers) AS borrowers
          FROM county_counts
          GROUP BY state
        ),
        zip_to_county AS (
          SELECT county_fips_5, SUM(borrowers) AS borrowers
          FROM zip_counts
          GROUP BY county_fips_5
        )
        SELECT
          (SELECT COUNT(*) FROM filtered) AS filtered_borrowers,
          (SELECT COUNT(*) FROM state_counts AS s
            JOIN county_to_state AS c USING (state)
            WHERE s.borrowers <> c.borrowers) AS state_count_mismatches,
          (SELECT COUNT(*) FROM county_counts AS c
            JOIN zip_to_county AS z USING (county_fips_5)
            WHERE c.borrowers <> z.borrowers) AS county_count_mismatches
        """,
    )

    assert rows, "filtered geo reconciliation query returned no rows"
    filtered_borrowers, state_mismatches, county_mismatches = map(int, rows[0])
    assert filtered_borrowers > 0
    assert state_mismatches == 0
    assert county_mismatches == 0


def test_live_geo_api_matches_independent_sql_for_multisegment_filters(
    warehouse: tuple[str, str, str],
) -> None:
    host, token, wid = warehouse
    segment_clause = _segment_all_clause("b")
    rows = _run_sql_rows(
        host,
        token,
        wid,
        f"""
        WITH filtered AS (
          SELECT b.state, b.county_fips_5, b.zip, b.marketing_eligible
          FROM mip.gold.borrower_360 AS b
          WHERE {segment_clause}
            AND b.state IS NOT NULL
            AND b.county_fips_5 IS NOT NULL
            AND b.zip IS NOT NULL
        ),
        top_state AS (
          SELECT state, COUNT(*) AS borrowers
          FROM filtered
          GROUP BY state
          ORDER BY borrowers DESC, state ASC
          LIMIT 1
        ),
        top_county AS (
          SELECT county_fips_5, COUNT(*) AS borrowers
          FROM filtered
          WHERE state = (SELECT state FROM top_state)
          GROUP BY county_fips_5
          ORDER BY borrowers DESC, county_fips_5 ASC
          LIMIT 1
        )
        SELECT
          (SELECT state FROM top_state) AS state,
          (SELECT borrowers FROM top_state) AS state_borrowers,
          (SELECT county_fips_5 FROM top_county) AS county_fips_5,
          (SELECT borrowers FROM top_county) AS county_borrowers,
          (SELECT COUNT(*)
           FROM filtered
           WHERE state = (SELECT state FROM top_state)
             AND marketing_eligible = TRUE) AS eligible_state_borrowers
        """,
    )

    assert rows, "multisegment fixture query returned no rows"
    state, state_count, county_fips, county_count, eligible_state_count = rows[0]
    assert state and county_fips
    assert int(state_count) > 0
    assert int(county_count) > 0
    assert int(eligible_state_count) >= 0

    qs = "segment_codes=itm,investor,equity,retention&segment_mode=all"
    with live_api_client(warehouse) as client:
        state_resp = client.get(f"/api/geo/state-rollups?{qs}")
        assert state_resp.status_code == 200, state_resp.text
        state_rollup = next(
            row for row in state_resp.json()["rollups"] if row["state"] == state
        )
        assert state_rollup["addressable"] == int(state_count)

        county_resp = client.get(f"/api/geo/county-rollups?state={state}&{qs}")
        assert county_resp.status_code == 200, county_resp.text
        county_rollup = next(
            row for row in county_resp.json()["rollups"] if row["fips_5"] == county_fips
        )
        assert county_rollup["addressable_borrowers"] == int(county_count)

        zip_resp = client.get(f"/api/geo/zip-rollups?county_fips={county_fips}&{qs}")
        assert zip_resp.status_code == 200, zip_resp.text
        zip_total = sum(row["addressable_borrowers"] for row in zip_resp.json()["rollups"])
        assert zip_total == int(county_count)

        leads_resp = client.get(
            f"/api/leads?states={state}&{qs}&limit=5000"
        )
        assert leads_resp.status_code == 200, leads_resp.text
        leads = leads_resp.json()
        # `/api/leads` is an actionable queue and therefore defaults to
        # marketing-eligible rows only; geo rollups remain analytic counts.
        assert len(leads) == min(int(eligible_state_count), 5000)
        assert all(row["state"] == state for row in leads)
        assert all(set(SEGMENT_ALL).issubset(set(row["segment_codes"])) for row in leads)


def test_live_geo_api_matches_independent_sql_for_any_segment_filter(
    warehouse: tuple[str, str, str],
) -> None:
    host, token, wid = warehouse
    rows = _run_sql_rows(
        host,
        token,
        wid,
        """
        SELECT state, COUNT(*) AS borrowers
        FROM mip.gold.borrower_360
        WHERE state IS NOT NULL
          AND (
            array_contains(segment_codes, 'itm')
            OR array_contains(segment_codes, 'retention')
          )
        GROUP BY state
        ORDER BY borrowers DESC, state ASC
        LIMIT 1
        """,
    )

    assert rows, "any-segment fixture query returned no rows"
    state, expected = rows[0]
    assert state and int(expected) > 0

    with live_api_client(warehouse) as client:
        resp = client.get(
            "/api/geo/state-rollups?segment_codes=itm,retention&segment_mode=any"
        )
        assert resp.status_code == 200, resp.text
        actual = next(row for row in resp.json()["rollups"] if row["state"] == state)
        assert actual["addressable"] == int(expected)


def test_live_portfolio_preview_and_lead_queue_match_independent_sql(
    warehouse: tuple[str, str, str],
) -> None:
    host, token, wid = warehouse
    rows = _run_sql_rows(
        host,
        token,
        wid,
        """
        SELECT COUNT(*) AS borrowers
        FROM mip.gold.borrower_360
        WHERE is_owner_occupied = TRUE
          AND equity_pct >= 25
          AND marketing_eligible = TRUE
        """,
    )

    assert rows, "portfolio preview reference query returned no rows"
    expected = int(rows[0][0])
    assert expected > 0

    criteria = {
        "occupancy": "Owner-occupied",
        "min_equity_pct_label": "≥ 25%",
    }
    with live_api_client(warehouse) as client:
        preview_resp = client.post(
            "/api/portfolio/preview",
            json={"criteria": criteria},
        )
        assert preview_resp.status_code == 200, preview_resp.text
        assert preview_resp.json()["marketable_population"] == expected

        query = "&".join(f"{quote(k)}={quote(v)}" for k, v in criteria.items())
        leads_resp = client.get(f"/api/leads?{query}&limit=5000")
        assert leads_resp.status_code == 200, leads_resp.text
        leads = leads_resp.json()
        assert len(leads) == min(expected, 5000)
        assert all(row["is_owner_occupied"] is True for row in leads)
        assert all(row["equity_estimate"] >= 0 for row in leads)


def test_repository_preview_criteria_sql_matches_endpoint_criteria() -> None:
    """Guard against route/schema drift in the portfolio criteria object."""
    where, params = DatabricksPortfolioRepository._build_preview_predicates(
        PortfolioCriteria(occupancy="Owner-occupied", min_equity_pct_label="≥ 25%")
    )

    assert "is_owner_occupied = TRUE" in where
    assert "equity_pct >= :equity_floor" in where
    assert params == {"equity_floor": 25}

"""Live proof of the S9 assigned-vs-unattended geography overlay.

Requires BOTH live dependencies (skips otherwise):

* Databricks SQL warehouse creds (``DATABRICKS_HOST``/``DATABRICKS_TOKEN``/
  ``DATABRICKS_WAREHOUSE_ID``) — the lead side of the subtraction.
* ``LAKEBASE_INTEGRATION=1`` plus Lakebase creds (static ``LAKEBASE_*``
  or workspace identity) — the assignment + coverage side.

The test cross-checks the service's unattended math against INDEPENDENT
direct queries (its own SQL against ``borrower_360``, its own Lakebase
reads of ``lead_assignments``), then proves a real assignment moves the
numbers at every drill level (state → county → ZIP). It only assigns a
borrower who currently has NO active assignment, and deletes its own
assignment rows afterwards (audit rows persist by design — the audit
table is append-only).
"""
from __future__ import annotations

import os
from collections import Counter

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.geo_assignment_overlay import (
    GeoAssignmentOverlayService,
    get_geo_assignment_overlay_service,
)
from backend.services.lakebase import (
    LakebaseClient,
    _reset_client_for_tests,
    get_lakebase_client,
)
from backend.services.loan_officer_state import LoanOfficerStateStore
from tests.integration.test_gold_data_truth import _creds

_HAS_STATIC_LAKEBASE = all(
    os.environ.get(k) for k in ("LAKEBASE_HOST", "LAKEBASE_USER", "LAKEBASE_PASSWORD")
)
_HAS_WORKSPACE_CREDS = all(
    os.environ.get(k) for k in ("DATABRICKS_HOST", "DATABRICKS_TOKEN")
)
_HAS_LAKEBASE = os.environ.get("LAKEBASE_INTEGRATION") == "1" and (
    _HAS_STATIC_LAKEBASE or _HAS_WORKSPACE_CREDS
)

pytestmark = pytest.mark.skipif(
    not (_HAS_LAKEBASE and _creds() is not None),
    reason=(
        "Set DATABRICKS_HOST/TOKEN/WAREHOUSE_ID and LAKEBASE_INTEGRATION=1 "
        "(+ LAKEBASE_* or workspace identity) to run the live overlay proof."
    ),
)

_SEEDED_LO_01 = "55555555-5555-4555-8555-555555555501"
_ACTOR = "skyler@entrada.ai"


def _lakebase_client() -> LakebaseClient:
    if not _HAS_STATIC_LAKEBASE:
        _reset_client_for_tests()
        return get_lakebase_client()
    return LakebaseClient(
        host=os.environ["LAKEBASE_HOST"],
        port=int(os.environ.get("LAKEBASE_PORT", "5432")),
        database=os.environ.get("LAKEBASE_DATABASE", "mip_app_state"),
        user=os.environ["LAKEBASE_USER"],
        password=os.environ["LAKEBASE_PASSWORD"],
    )


def _sql_client() -> DatabricksSqlClient:
    host, token, warehouse_id = _creds()  # type: ignore[misc]
    return DatabricksSqlClient(host, token, warehouse_id)


def _active_assignment_borrowers(lakebase: LakebaseClient) -> set[str]:
    rows = lakebase.fetchall(
        "SELECT DISTINCT borrower_id FROM mip_app.lead_assignments "
        "WHERE released_at IS NULL",
        limit=100_000,
    )
    return {str(r["borrower_id"]) for r in rows if r.get("borrower_id")}


def _direct_assigned_count_for_unit(
    sql: DatabricksSqlClient,
    borrower_ids: set[str],
    *,
    where: str,
    params: dict[str, object],
) -> int:
    """Independent re-derivation of the assigned side of the subtraction.

    Deliberately NOT the service's chunking code: one IN list per 100
    ids, summed, so a service-side bucketing bug can't self-confirm.
    """
    ids = sorted(borrower_ids)
    total = 0
    for start in range(0, len(ids), 100):
        chunk = ids[start : start + 100]
        placeholders = ", ".join(f":x{i}" for i in range(len(chunk)))
        chunk_params: dict[str, object] = {f"x{i}": b for i, b in enumerate(chunk)}
        chunk_params.update(params)
        rows = sql.execute(
            "SELECT COUNT(*) AS n FROM mip.gold.borrower_360 "
            f"WHERE marketing_eligible = TRUE AND {where} "
            f"AND borrower_id IN ({placeholders})",
            chunk_params,
        )
        total += int(rows[0]["n"]) if rows else 0
    return total


def test_overlay_unattended_math_reconciles_and_reacts_to_assignment() -> None:
    sql = _sql_client()
    lakebase = _lakebase_client()
    store = LoanOfficerStateStore(lakebase)

    # Pick a marketing-eligible borrower with full geo and NO active
    # assignment, so assign_lead cannot release someone else's work.
    already_assigned = _active_assignment_borrowers(lakebase)
    candidates = sql.execute(
        "SELECT borrower_id, state, county_fips_5, zip "
        "FROM mip.gold.borrower_360 "
        "WHERE marketing_eligible = TRUE "
        "  AND state IS NOT NULL AND LENGTH(state) = 2 "
        "  AND county_fips_5 IS NOT NULL AND LENGTH(county_fips_5) = 5 "
        "  AND zip IS NOT NULL AND LENGTH(zip) = 5 "
        "ORDER BY opportunity_score DESC, borrower_id ASC "
        "LIMIT 50",
    )
    target = next(
        (r for r in candidates if str(r["borrower_id"]) not in already_assigned),
        None,
    )
    assert target is not None, "no unassigned marketing-eligible borrower available"
    borrower_id = str(target["borrower_id"])
    state = str(target["state"]).upper()
    fips = str(target["county_fips_5"])
    zip5 = str(target["zip"])

    def unit(level: str, **kwargs: str) -> dict[str, int]:
        response = GeoAssignmentOverlayService(sql, lakebase).overlay(level, **kwargs)  # type: ignore[arg-type]
        key = {"state": state, "county": fips, "zip": zip5}[level]
        matched = next((u for u in response.units if u.unit_id == key), None)
        assert matched is not None, f"unit {key} missing at level={level}"
        return {
            "lead": matched.lead_count,
            "assigned": matched.assigned_count,
            "unattended": matched.unattended_count,
        }

    # ---- Independent lead counts (direct SQL, no service code). ----
    direct_leads = {
        "state": int(
            sql.execute(
                "SELECT COUNT(*) AS n FROM mip.gold.borrower_360 "
                "WHERE marketing_eligible = TRUE AND state = :state",
                {"state": state},
            )[0]["n"]
        ),
        "county": int(
            sql.execute(
                "SELECT COUNT(*) AS n FROM mip.gold.borrower_360 "
                "WHERE marketing_eligible = TRUE AND county_fips_5 = :fips",
                {"fips": fips},
            )[0]["n"]
        ),
        "zip": int(
            sql.execute(
                "SELECT COUNT(*) AS n FROM mip.gold.borrower_360 "
                "WHERE marketing_eligible = TRUE AND county_fips_5 = :fips "
                "AND zip = :zip",
                {"fips": fips, "zip": zip5},
            )[0]["n"]
        ),
    }

    try:
        # ---- Baseline: service counts == independent derivation. ----
        before = {
            "state": unit("state"),
            "county": unit("county", state=state),
            "zip": unit("zip", county_fips=fips),
        }
        baseline_assigned = {
            "state": _direct_assigned_count_for_unit(
                sql, already_assigned, where="state = :state", params={"state": state}
            ),
            "county": _direct_assigned_count_for_unit(
                sql, already_assigned, where="county_fips_5 = :fips", params={"fips": fips}
            ),
            "zip": _direct_assigned_count_for_unit(
                sql,
                already_assigned,
                where="county_fips_5 = :fips AND zip = :zip",
                params={"fips": fips, "zip": zip5},
            ),
        }
        for level in ("state", "county", "zip"):
            assert before[level]["lead"] == direct_leads[level], level
            assert before[level]["assigned"] == baseline_assigned[level], level
            assert (
                before[level]["unattended"]
                == direct_leads[level] - baseline_assigned[level]
            ), level

        # ---- Assign the borrower; every level must move by exactly 1. ----
        _assignment, _audit_id = store.assign_lead(
            borrower_id=borrower_id,
            loan_officer_id=_SEEDED_LO_01,
            assigned_by=_ACTOR,
        )
        after = {
            "state": unit("state"),
            "county": unit("county", state=state),
            "zip": unit("zip", county_fips=fips),
        }
        for level in ("state", "county", "zip"):
            assert after[level]["lead"] == before[level]["lead"], level
            assert after[level]["assigned"] == before[level]["assigned"] + 1, level
            assert after[level]["unattended"] == before[level]["unattended"] - 1, level

        # ---- Same truth through the FastAPI route (live wiring). ----
        previous = dict(app.dependency_overrides)
        app.dependency_overrides[get_geo_assignment_overlay_service] = (
            lambda: GeoAssignmentOverlayService(sql, lakebase)
        )
        try:
            with TestClient(app) as client:
                for level, query in (
                    ("state", "level=state"),
                    ("county", f"level=county&state={state}"),
                    ("zip", f"level=zip&county_fips={fips}"),
                ):
                    resp = client.get(f"/api/geo/assignment-overlay?{query}")
                    assert resp.status_code == 200, resp.text
                    body = resp.json()
                    key = {"state": state, "county": fips, "zip": zip5}[level]
                    row = next(u for u in body["units"] if u["unit_id"] == key)
                    assert row["lead_count"] == after[level]["lead"]
                    assert row["assigned_count"] == after[level]["assigned"]
                    assert row["unattended_count"] == after[level]["unattended"]
                    assert body["total_unattended"] == sum(
                        u["unattended_count"] for u in body["units"]
                    )
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)
    finally:
        # Remove only rows this test created; audit trail stays.
        lakebase.execute(
            "DELETE FROM mip_app.lead_assignments "
            "WHERE borrower_id = %(borrower_id)s AND assigned_by = %(actor)s",
            {"borrower_id": borrower_id, "actor": _ACTOR},
        )


def test_overlay_drill_hierarchy_counts_reconcile() -> None:
    """State → county → ZIP conservation on the overlay's lead side.

    The sum of county lead counts inside a state, and ZIP lead counts
    inside a county, must equal the parent unit's count computed over
    rows that carry valid child geography — same conservation the
    rollup reconciliation suite pins for the borrower map, applied to
    the overlay's marketing-eligible lead definition.
    """
    sql = _sql_client()
    lakebase = _lakebase_client()
    service = GeoAssignmentOverlayService(sql, lakebase)

    states = service.overlay("state")
    assert states.units, "overlay returned no states"
    top_state = states.units[0].unit_id

    counties = service.overlay("county", state=top_state)
    assert counties.units, f"no county overlay units for {top_state}"
    county_sum = sum(u.lead_count for u in counties.units)
    direct_state_with_county = int(
        sql.execute(
            "SELECT COUNT(*) AS n FROM mip.gold.borrower_360 "
            "WHERE marketing_eligible = TRUE AND state = :state "
            "AND county_fips_5 IS NOT NULL AND LENGTH(county_fips_5) = 5",
            {"state": top_state},
        )[0]["n"]
    )
    assert county_sum == direct_state_with_county

    top_county = counties.units[0].unit_id
    zips = service.overlay("zip", county_fips=top_county)
    assert zips.units, f"no zip overlay units for {top_county}"
    zip_sum = sum(u.lead_count for u in zips.units)
    direct_county_with_zip = int(
        sql.execute(
            "SELECT COUNT(*) AS n FROM mip.gold.borrower_360 "
            "WHERE marketing_eligible = TRUE AND county_fips_5 = :fips "
            "AND zip IS NOT NULL AND LENGTH(zip) = 5",
            {"fips": top_county},
        )[0]["n"]
    )
    assert zip_sum == direct_county_with_zip

    # Assignment conservation: per-level assigned totals must equal an
    # independent bucketing of the active assignment set.
    active = _active_assignment_borrowers(lakebase)
    if active:
        ids = sorted(active)
        bucket: Counter[str] = Counter()
        for start in range(0, len(ids), 100):
            chunk = ids[start : start + 100]
            placeholders = ", ".join(f":x{i}" for i in range(len(chunk)))
            rows = sql.execute(
                "SELECT state FROM mip.gold.borrower_360 "
                "WHERE marketing_eligible = TRUE AND state IS NOT NULL "
                f"AND LENGTH(state) = 2 AND borrower_id IN ({placeholders})",
                {f"x{i}": b for i, b in enumerate(chunk)},
            )
            for row in rows:
                bucket[str(row["state"]).upper()] += 1
        by_state = {u.unit_id: u.assigned_count for u in service.overlay("state").units}
        for state_code, expected in bucket.items():
            assert by_state.get(state_code, 0) == expected, state_code

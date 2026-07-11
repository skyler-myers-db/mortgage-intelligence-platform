"""Unit tests for the S9 assigned-vs-unattended geography overlay.

Covers the three pieces the integration suite can't pin cheaply:

* the pure array-membership coverage join (state implies county/ZIP,
  county FIPS membership, dedupe, no-match),
* the unattended subtraction (keyed by the lead side, never negative),
* the service orchestration + router wiring through fake clients.
"""
from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from backend.main import app
from backend.services.geo_assignment_overlay import (
    CoverageOfficer,
    GeoAssignmentOverlayService,
    build_overlay_units,
    covering_officers,
)
from backend.services.resilience import TTLCache

_LO_IL = CoverageOfficer(
    display_name="Summit LO 01",
    coverage_states=("IL", "IN", "WI"),
    coverage_counties=("17031", "17043"),
)
_LO_CA = CoverageOfficer(
    display_name="Summit LO 02",
    coverage_states=("CA", "NV"),
    coverage_counties=("06037",),
)
_LO_COUNTY_ONLY = CoverageOfficer(
    display_name="Summit LO 03",
    coverage_states=(),
    coverage_counties=("48201",),
)


class TestCoveringOfficers:
    def test_state_unit_uses_state_membership_only(self) -> None:
        names = covering_officers([_LO_IL, _LO_CA, _LO_COUNTY_ONLY], state="IL")
        assert names == ["Summit LO 01"]

    def test_county_unit_matches_explicit_county_fips(self) -> None:
        names = covering_officers([_LO_CA, _LO_COUNTY_ONLY], state="TX", county_fips="48201")
        assert names == ["Summit LO 03"]

    def test_state_coverage_implies_every_county_in_the_state(self) -> None:
        # 17097 (Lake, IL) is NOT in LO 01's coverage_counties, but IL is
        # in coverage_states — state coverage implies its counties.
        names = covering_officers([_LO_IL], state="IL", county_fips="17097")
        assert names == ["Summit LO 01"]

    def test_zip_units_inherit_parent_county_coverage(self) -> None:
        # A ZIP has no coverage array of its own; it is covered exactly
        # when its parent county is.
        assert covering_officers([_LO_COUNTY_ONLY], state="TX", county_fips="48201") == [
            "Summit LO 03"
        ]
        assert covering_officers([_LO_COUNTY_ONLY], state="TX", county_fips="48113") == []

    def test_no_coverage_returns_empty(self) -> None:
        assert covering_officers([_LO_CA], state="FL") == []
        assert covering_officers([], state="IL", county_fips="17031") == []

    def test_case_normalisation_and_dedupe(self) -> None:
        duplicate = CoverageOfficer(
            display_name="Summit LO 01",
            coverage_states=("IL",),
            coverage_counties=(),
        )
        names = covering_officers([_LO_IL, duplicate], state="il")
        assert names == ["Summit LO 01"]


class TestBuildOverlayUnits:
    def test_unattended_is_leads_minus_assigned(self) -> None:
        units = build_overlay_units(
            {"IL": 100, "CA": 40},
            {"IL": 30},
            {"IL": ["Summit LO 01"], "CA": []},
        )
        by_id = {u.unit_id: u for u in units}
        assert by_id["IL"].assigned_count == 30
        assert by_id["IL"].unattended_count == 70
        assert by_id["CA"].assigned_count == 0
        assert by_id["CA"].unattended_count == 40
        assert by_id["IL"].covering_officer_count == 1

    def test_assignment_only_units_do_not_appear(self) -> None:
        # An active assignment whose borrower is no longer marketing-
        # eligible resolves to no lead-side unit; it must not create a
        # phantom unit or a negative unattended count.
        units = build_overlay_units({"IL": 5}, {"IL": 2, "CA": 9}, {})
        assert [u.unit_id for u in units] == ["IL"]
        assert units[0].unattended_count == 3

    def test_assigned_capped_at_lead_count(self) -> None:
        units = build_overlay_units({"60611": 4}, {"60611": 9}, {})
        assert units[0].assigned_count == 4
        assert units[0].unattended_count == 0

    def test_sorted_by_lead_count_then_unit_id(self) -> None:
        units = build_overlay_units({"B": 10, "A": 10, "C": 90}, {}, {})
        assert [u.unit_id for u in units] == ["C", "A", "B"]


class _FakeSqlClient:
    def __init__(self, responses: list[tuple[str, list[dict[str, Any]]]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(
        self, statement: str, parameters: Any | None = None
    ) -> list[dict[str, Any]]:
        self.calls.append((statement, dict(parameters or {})))
        for marker, rows in self.responses:
            if marker in statement:
                return rows
        return []


class _FakeLakebase:
    def __init__(
        self,
        assignments: list[dict[str, Any]],
        officers: list[dict[str, Any]],
    ) -> None:
        self.assignments = assignments
        self.officers = officers

    def fetchall(
        self, sql: str, params: Any | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        if "lead_assignments" in sql:
            return self.assignments
        if "loan_officers" in sql:
            return self.officers
        return []


def _service(
    sql_rows: list[tuple[str, list[dict[str, Any]]]],
    assignments: list[dict[str, Any]],
    officers: list[dict[str, Any]],
) -> tuple[GeoAssignmentOverlayService, _FakeSqlClient]:
    sql = _FakeSqlClient(sql_rows)
    service = GeoAssignmentOverlayService(
        sql,
        _FakeLakebase(assignments, officers),  # type: ignore[arg-type]
        cache=TTLCache(),
    )
    return service, sql


_OFFICER_ROWS = [
    {
        "display_name": "Summit LO 01",
        "coverage_states": ["IL"],
        "coverage_counties": ["17031"],
    },
]


class TestOverlayService:
    def test_state_level_end_to_end(self) -> None:
        service, sql = _service(
            sql_rows=[
                ("GROUP BY state", [
                    {"unit_id": "IL", "lead_count": 10},
                    {"unit_id": "CA", "lead_count": 4},
                ]),
                ("borrower_id IN", [
                    {"borrower_id": "B-0000000000001", "state": "IL", "county_fips_5": "17031", "zip": "60611"},
                    {"borrower_id": "B-0000000000002", "state": "IL", "county_fips_5": "17043", "zip": "60101"},
                ]),
            ],
            assignments=[
                {"borrower_id": "B-0000000000001"},
                {"borrower_id": "B-0000000000002"},
            ],
            officers=_OFFICER_ROWS,
        )
        result = service.overlay("state")
        assert result.level == "state"
        by_id = {u.unit_id: u for u in result.units}
        assert by_id["IL"].lead_count == 10
        assert by_id["IL"].assigned_count == 2
        assert by_id["IL"].unattended_count == 8
        assert by_id["IL"].covering_officers == ["Summit LO 01"]
        assert by_id["CA"].assigned_count == 0
        assert result.total_leads == 14
        assert result.total_assigned == 2
        assert result.total_unattended == 12

    def test_zip_level_derives_state_and_scopes_to_county(self) -> None:
        service, sql = _service(
            sql_rows=[
                ("GROUP BY zip", [
                    {"unit_id": "60611", "lead_count": 6, "state": "IL"},
                    {"unit_id": "60613", "lead_count": 3, "state": "IL"},
                ]),
                ("borrower_id IN", [
                    # In-county assignment counts; out-of-county doesn't.
                    {"borrower_id": "B-0000000000001", "state": "IL", "county_fips_5": "17031", "zip": "60611"},
                    {"borrower_id": "B-0000000000003", "state": "IL", "county_fips_5": "17043", "zip": "60101"},
                ]),
            ],
            assignments=[
                {"borrower_id": "B-0000000000001"},
                {"borrower_id": "B-0000000000003"},
            ],
            officers=_OFFICER_ROWS,
        )
        result = service.overlay("zip", county_fips="17031")
        assert result.state == "IL"
        assert result.county_fips == "17031"
        by_id = {u.unit_id: u for u in result.units}
        assert by_id["60611"].assigned_count == 1
        assert by_id["60611"].unattended_count == 5
        assert by_id["60613"].assigned_count == 0
        # ZIP inherits the parent county's coverage (17031 explicit).
        assert by_id["60611"].covering_officers == ["Summit LO 01"]

    def test_no_active_assignments_short_circuits_geo_resolution(self) -> None:
        service, sql = _service(
            sql_rows=[("GROUP BY state", [{"unit_id": "IL", "lead_count": 7}])],
            assignments=[],
            officers=_OFFICER_ROWS,
        )
        result = service.overlay("state")
        assert result.units[0].assigned_count == 0
        assert result.units[0].unattended_count == 7
        assert all("borrower_id IN" not in stmt for stmt, _ in sql.calls)

    def test_assignment_chunking_batches_parameters(self) -> None:
        ids = [f"B-{i:013d}" for i in range(401)]
        service, sql = _service(
            sql_rows=[
                ("GROUP BY state", [{"unit_id": "IL", "lead_count": 500}]),
                ("borrower_id IN", []),
            ],
            assignments=[{"borrower_id": b} for b in ids],
            officers=[],
        )
        service.overlay("state")
        chunk_calls = [c for c in sql.calls if "borrower_id IN" in c[0]]
        assert len(chunk_calls) == 3  # 200 + 200 + 1
        assert len(chunk_calls[0][1]) == 200
        assert len(chunk_calls[2][1]) == 1


class TestOverlayEndpoint:
    def test_state_overlay_route_returns_fixture_units(self) -> None:
        with TestClient(app) as client:
            resp = client.get("/api/geo/assignment-overlay?level=state")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["level"] == "state"
        assert body["units"], "expected fixture overlay units"
        first = body["units"][0]
        assert first["unattended_count"] == first["lead_count"] - first["assigned_count"]
        assert body["lead_definition"]

    def test_county_overlay_requires_state(self) -> None:
        with TestClient(app) as client:
            resp = client.get("/api/geo/assignment-overlay?level=county")
        assert resp.status_code == 422

    def test_zip_overlay_requires_county_fips(self) -> None:
        with TestClient(app) as client:
            resp = client.get("/api/geo/assignment-overlay?level=zip")
        assert resp.status_code == 422

    def test_zip_overlay_drill_matches_fixture_math(self) -> None:
        with TestClient(app) as client:
            resp = client.get(
                "/api/geo/assignment-overlay?level=zip&county_fips=17031"
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["county_fips"] == "17031"
        total = sum(u["unattended_count"] for u in body["units"])
        assert body["total_unattended"] == total

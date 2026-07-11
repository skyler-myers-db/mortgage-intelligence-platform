"""Assigned-vs-unattended geography overlay service (S9).

Combines the two live systems of record the platform already trusts:

* Unity Catalog ``mip.gold.borrower_360`` for lead counts per geography
  unit (marketing-eligible rows — the same definition the geo-drilled
  Lead Queue serves, see FIX β in ``databricks_leads.py``).
* Lakebase ``mip_app.lead_assignments`` for active assignments
  (``released_at IS NULL``), resolved to geography by joining
  ``borrower_id`` back to ``borrower_360`` in bounded chunks.
* Lakebase ``mip_app.loan_officers`` coverage arrays for the
  "who covers this unit" layer — a pure array-membership join
  (county FIPS in ``coverage_counties``, or the unit's state in
  ``coverage_states``); no geometry.

``unattended = lead_count - assigned_count`` is non-negative by
construction: the assigned side only counts borrowers that are ALSO in
the unit's marketing-eligible lead set, so an assignment held by a
borrower who has since dropped out of eligibility simply stops counting
against the unit instead of driving the difference negative.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

from fastapi import Depends

from backend.schemas.geo_overlay import (
    GeoAssignmentOverlayResponse,
    GeoAssignmentOverlayUnit,
    GeoOverlayLevel,
)
from backend.services.databricks_sql_helpers import qualify
from backend.services.lakebase import LakebaseClient, get_lakebase_client
from backend.services.observability import emit
from backend.services.resilience import TTLCache

log = logging.getLogger("backend.services.geo_assignment_overlay")

# Statement Execution named-parameter chunk size for the borrower_id IN
# (...) geography resolution. Active assignments are a manually-curated
# set (hundreds, not millions), so a few bounded round-trips is cheaper
# and simpler than a temp-table dance.
_ASSIGNED_CHUNK_SIZE = 200


class _SqlClientLike(Protocol):
    def execute(
        self, statement: str, parameters: Any | None = None
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class CoverageOfficer:
    """Minimal roster row for the array-membership coverage join."""

    display_name: str
    coverage_states: tuple[str, ...]
    coverage_counties: tuple[str, ...]


def covering_officers(
    officers: list[CoverageOfficer],
    *,
    state: str | None,
    county_fips: str | None = None,
) -> list[str]:
    """Array-membership coverage join for one geography unit.

    * County/ZIP units: an officer covers the unit when the county FIPS
      is in ``coverage_counties`` OR the unit's state is in
      ``coverage_states`` (state coverage implies every county and ZIP
      inside it — ZIPs inherit their parent county's coverage because
      no ZIP-level coverage array exists).
    * State units: state membership only.

    Returns display names in roster order, deduplicated.
    """
    state_uc = (state or "").strip().upper()
    fips = (county_fips or "").strip()
    out: list[str] = []
    for officer in officers:
        covers = bool(fips) and fips in officer.coverage_counties
        if not covers and state_uc:
            covers = state_uc in officer.coverage_states
        if covers and officer.display_name not in out:
            out.append(officer.display_name)
    return out


def build_overlay_units(
    lead_counts: dict[str, int],
    assigned_counts: dict[str, int],
    officer_names_by_unit: dict[str, list[str]],
) -> list[GeoAssignmentOverlayUnit]:
    """Assemble units from per-unit tallies.

    Units are keyed by the lead-count side — an assignment whose
    borrower has no marketing-eligible row cannot create a unit (and
    cannot make ``unattended`` negative). Sorted by lead_count DESC then
    unit_id, matching the rollup endpoints' ordering contract.
    """
    units = [
        GeoAssignmentOverlayUnit(
            unit_id=unit_id,
            lead_count=leads,
            assigned_count=min(assigned_counts.get(unit_id, 0), leads),
            unattended_count=max(leads - assigned_counts.get(unit_id, 0), 0),
            covering_officer_count=len(officer_names_by_unit.get(unit_id, [])),
            covering_officers=officer_names_by_unit.get(unit_id, []),
        )
        for unit_id, leads in lead_counts.items()
    ]
    units.sort(key=lambda u: (-u.lead_count, u.unit_id))
    return units


class GeoAssignmentOverlayService:
    """Read-only overlay reads with a short-TTL single-flight cache.

    15s TTL: assignments change interactively (a manager assigning from
    the Lead Queue expects the map to catch up on the next glance), so
    the cache only exists to absorb hover/drill bursts, not to make the
    data stale.
    """

    def __init__(
        self,
        sql_client: _SqlClientLike,
        lakebase_client: LakebaseClient,
        *,
        cache: TTLCache | None = None,
        cache_ttl_s: float = 15.0,
    ) -> None:
        self._sql = sql_client
        self._lakebase = lakebase_client
        self._cache = cache if cache is not None else TTLCache()
        self._cache_ttl_s = cache_ttl_s

    _LEAD_STATE_SQL = (
        "SELECT state AS unit_id, CAST(COUNT(*) AS INT) AS lead_count "
        f"FROM {qualify('gold', 'borrower_360')} "
        "WHERE marketing_eligible = TRUE "
        "  AND state IS NOT NULL AND LENGTH(state) = 2 "
        "GROUP BY state"
    )

    _LEAD_COUNTY_SQL = (
        "SELECT county_fips_5 AS unit_id, CAST(COUNT(*) AS INT) AS lead_count "
        f"FROM {qualify('gold', 'borrower_360')} "
        "WHERE marketing_eligible = TRUE "
        "  AND state = :state "
        "  AND county_fips_5 IS NOT NULL AND LENGTH(county_fips_5) = 5 "
        "GROUP BY county_fips_5"
    )

    _LEAD_ZIP_SQL = (
        "SELECT zip AS unit_id, CAST(COUNT(*) AS INT) AS lead_count, "
        "  ANY_VALUE(state) AS state "
        f"FROM {qualify('gold', 'borrower_360')} "
        "WHERE marketing_eligible = TRUE "
        "  AND county_fips_5 = :fips_5 "
        "  AND zip IS NOT NULL AND LENGTH(zip) = 5 "
        "GROUP BY zip"
    )

    # Active assignments only: released rows are history, not workload.
    _ASSIGNED_BORROWERS_SQL = (
        "SELECT DISTINCT borrower_id FROM mip_app.lead_assignments "
        "WHERE released_at IS NULL"
    )

    _OFFICERS_SQL = (
        "SELECT display_name, coverage_states, coverage_counties "
        "FROM mip_app.loan_officers WHERE active = true "
        "ORDER BY display_name ASC"
    )

    def overlay(
        self,
        level: GeoOverlayLevel,
        *,
        state: str | None = None,
        county_fips: str | None = None,
    ) -> GeoAssignmentOverlayResponse:
        state_uc = str(state or "").strip().upper()[:2] or None
        fips = str(county_fips or "").strip()[:5] or None
        cache_key = f"geo.assignment_overlay:{level}:{state_uc or '_'}:{fips or '_'}"
        return self._cache.get_or_set(
            cache_key,
            lambda: self._build(level, state=state_uc, county_fips=fips),
            ttl_s=self._cache_ttl_s,
            stale_if_error=True,
        )

    def _build(
        self,
        level: GeoOverlayLevel,
        *,
        state: str | None,
        county_fips: str | None,
    ) -> GeoAssignmentOverlayResponse:
        lead_counts, zip_state = self._lead_counts(level, state=state, county_fips=county_fips)
        resolved_state = state or zip_state
        assigned_counts = self._assigned_counts(level, state=resolved_state, county_fips=county_fips)
        officers = self._active_officers()
        officer_names_by_unit = {
            unit_id: covering_officers(
                officers,
                state=unit_id if level == "state" else resolved_state,
                county_fips=(
                    unit_id if level == "county" else county_fips if level == "zip" else None
                ),
            )
            for unit_id in lead_counts
        }
        units = build_overlay_units(lead_counts, assigned_counts, officer_names_by_unit)
        emit(
            log,
            "geo_assignment_overlay_built",
            level=logging.INFO,
            overlay_level=level,
            unit_count=len(units),
            total_assigned=sum(u.assigned_count for u in units),
        )
        return GeoAssignmentOverlayResponse(
            level=level,
            state=resolved_state,
            county_fips=county_fips if level == "zip" else None,
            units=units,
            total_leads=sum(u.lead_count for u in units),
            total_assigned=sum(u.assigned_count for u in units),
            total_unattended=sum(u.unattended_count for u in units),
        )

    def _lead_counts(
        self,
        level: GeoOverlayLevel,
        *,
        state: str | None,
        county_fips: str | None,
    ) -> tuple[dict[str, int], str | None]:
        """Per-unit marketing-eligible lead counts, plus the parent
        state derived from the ZIP query (the zip endpoint's caller only
        knows the county FIPS)."""
        if level == "state":
            rows = self._sql.execute(self._LEAD_STATE_SQL) or []
        elif level == "county":
            rows = self._sql.execute(self._LEAD_COUNTY_SQL, {"state": state}) or []
        else:
            rows = self._sql.execute(self._LEAD_ZIP_SQL, {"fips_5": county_fips}) or []
        counts: dict[str, int] = {}
        zip_state: str | None = None
        for row in rows:
            unit_id = str(row.get("unit_id") or "").strip()
            if not unit_id:
                continue
            counts[unit_id] = int(row.get("lead_count") or 0)
            if level == "zip" and zip_state is None and row.get("state"):
                zip_state = str(row["state"]).strip().upper()[:2]
        return counts, zip_state

    def _assigned_counts(
        self,
        level: GeoOverlayLevel,
        *,
        state: str | None,
        county_fips: str | None,
    ) -> dict[str, int]:
        """Resolve active assignments to geography units via borrower_360.

        Chunked ``borrower_id IN (...)`` lookups keep each statement's
        parameter list bounded; the eligible-row filter mirrors the lead
        side so both sides of the subtraction share one definition.
        """
        assignment_rows = self._lakebase.fetchall(
            self._ASSIGNED_BORROWERS_SQL,
            limit=100_000,
        )
        borrower_ids = sorted(
            {str(r.get("borrower_id") or "").strip() for r in assignment_rows}
            - {""}
        )
        if not borrower_ids:
            return {}
        counts: Counter[str] = Counter()
        for start in range(0, len(borrower_ids), _ASSIGNED_CHUNK_SIZE):
            chunk = borrower_ids[start : start + _ASSIGNED_CHUNK_SIZE]
            placeholders = ", ".join(f":b{i}" for i in range(len(chunk)))
            params: dict[str, Any] = {f"b{i}": borrower_id for i, borrower_id in enumerate(chunk)}
            sql = (
                "SELECT borrower_id, state, county_fips_5, zip "
                f"FROM {qualify('gold', 'borrower_360')} "
                "WHERE marketing_eligible = TRUE "
                f"  AND borrower_id IN ({placeholders})"
            )
            for row in self._sql.execute(sql, params) or []:
                unit_id = self._unit_for_row(row, level, state=state, county_fips=county_fips)
                if unit_id is not None:
                    counts[unit_id] += 1
        return dict(counts)

    @staticmethod
    def _unit_for_row(
        row: dict[str, Any],
        level: GeoOverlayLevel,
        *,
        state: str | None,
        county_fips: str | None,
    ) -> str | None:
        row_state = str(row.get("state") or "").strip().upper()[:2]
        row_fips = str(row.get("county_fips_5") or "").strip()
        row_zip = str(row.get("zip") or "").strip()
        if level == "state":
            return row_state if len(row_state) == 2 else None
        if level == "county":
            if row_state != (state or "") or len(row_fips) != 5:
                return None
            return row_fips
        if row_fips != (county_fips or "") or len(row_zip) != 5:
            return None
        return row_zip

    def _active_officers(self) -> list[CoverageOfficer]:
        rows = self._lakebase.fetchall(self._OFFICERS_SQL, limit=1_000)
        return [
            CoverageOfficer(
                display_name=str(r.get("display_name") or "").strip(),
                coverage_states=tuple(
                    str(s).strip().upper() for s in (r.get("coverage_states") or [])
                ),
                coverage_counties=tuple(
                    str(c).strip() for c in (r.get("coverage_counties") or [])
                ),
            )
            for r in rows
            if str(r.get("display_name") or "").strip()
        ]


LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]

# Module-level cache so per-request service instances share hits.
_OVERLAY_CACHE = TTLCache()


def get_geo_assignment_overlay_service(
    lakebase: LakebaseDep,
) -> GeoAssignmentOverlayService:
    """Per-request assembly over the process-singleton clients.

    The SQL client import stays inside the function so unit tests that
    override this dependency never touch warehouse credential resolution.
    """
    from backend.services.databricks_sql import get_sql_client

    return GeoAssignmentOverlayService(get_sql_client(), lakebase, cache=_OVERLAY_CACHE)

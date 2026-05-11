"""Data-driven geography coverage discovery.

The app should not know in code whether the active Cotality share contains
one county, six counties, or a full national feed. This module reads the gold
geography rollups and returns a compact coverage contract that API routes and
UI copy can reuse.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.services.county_names import county_name_for_fips
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.databricks_sql_helpers import qualify


@dataclass(frozen=True)
class GeographyScopeCounty:
    state: str
    fips_5: str
    county_name: str | None
    addressable_borrowers: int


@dataclass(frozen=True)
class GeographyScope:
    state_count: int
    county_count: int
    zip_count: int | None
    snapshot_date: str | None
    counties: tuple[GeographyScopeCounty, ...]
    source_table: str

    @property
    def scope_label(self) -> str:
        if self.county_count <= 0 or self.state_count <= 0:
            return "Cotality geography coverage unavailable until gold rollups refresh"
        county_word = "county" if self.county_count == 1 else "counties"
        state_word = "state" if self.state_count == 1 else "states"
        return (
            f"Cotality data coverage: {self.county_count:,} {county_word} "
            f"across {self.state_count:,} {state_word}"
        )

    def state_county_count(self, state: str) -> int:
        state_uc = str(state or "").upper()[:2]
        return sum(1 for county in self.counties if county.state == state_uc)

    def state_scope_label(self, state: str, *, returned_count: int | None = None) -> str | None:
        state_uc = str(state or "").upper()[:2]
        available = self.state_county_count(state_uc)
        if available <= 0:
            if returned_count is None or returned_count <= 0:
                return None
            available = returned_count
        county_word = "county" if available == 1 else "counties"
        return f"{self.scope_label}; {available:,} {county_word} available in {state_uc}"

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "state_count": self.state_count,
            "county_count": self.county_count,
            "zip_count": self.zip_count,
            "snapshot_date": self.snapshot_date,
            "source_table": self.source_table,
            "scope_label": self.scope_label,
            "counties": [
                {
                    "state": county.state,
                    "fips_5": county.fips_5,
                    "county_name": county.county_name,
                    "addressable_borrowers": county.addressable_borrowers,
                }
                for county in self.counties
            ],
        }


COUNTY_SCOPE_SQL = (
    "SELECT "
    "  state, "
    "  fips_5, "
    "  county_name, "
    "  addressable_borrowers, "
    "  snapshot_date "
    f"FROM {qualify('gold', 'county_rollup')} "
    f"WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM {qualify('gold', 'county_rollup')}) "
    "ORDER BY state ASC, addressable_borrowers DESC, fips_5 ASC"
)

ZIP_SCOPE_SQL = (
    "SELECT CAST(COUNT(DISTINCT zip) AS INT) AS zip_count "
    f"FROM {qualify('gold', 'zip_rollup')} "
    f"WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM {qualify('gold', 'zip_rollup')}) "
    "  AND zip IS NOT NULL"
)


def build_geography_scope(
    county_rows: list[dict[str, Any]],
    *,
    zip_count: int | None,
    source_table: str | None = None,
) -> GeographyScope:
    counties: list[GeographyScopeCounty] = []
    snapshot_date: str | None = None
    for row in county_rows:
        fips = str(row.get("fips_5") or "")[:5]
        state = str(row.get("state") or "").upper()[:2]
        if len(fips) != 5 or len(state) != 2:
            continue
        if snapshot_date is None and row.get("snapshot_date") is not None:
            snapshot_date = str(row.get("snapshot_date"))
        counties.append(
            GeographyScopeCounty(
                state=state,
                fips_5=fips,
                county_name=(
                    str(row["county_name"]) if row.get("county_name") else None
                )
                or county_name_for_fips(fips),
                addressable_borrowers=int(row.get("addressable_borrowers") or 0),
            )
        )
    states = {county.state for county in counties}
    return GeographyScope(
        state_count=len(states),
        county_count=len(counties),
        zip_count=zip_count,
        snapshot_date=snapshot_date,
        counties=tuple(counties),
        source_table=source_table or qualify("gold", "county_rollup"),
    )


def load_geography_scope(client: DatabricksSqlClient) -> GeographyScope:
    county_rows = client.execute(COUNTY_SCOPE_SQL) or []
    zip_row = client.execute_one(ZIP_SCOPE_SQL) or {}
    zip_count: int | None = None
    if zip_row.get("zip_count") is not None:
        zip_count = int(zip_row["zip_count"])
    return build_geography_scope(
        county_rows,
        zip_count=zip_count,
        source_table=qualify("gold", "county_rollup"),
    )

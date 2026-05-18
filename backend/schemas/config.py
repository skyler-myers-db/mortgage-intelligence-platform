"""Response schemas for configuration options and footprint metadata."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GeographyScopeCounty(BaseModel):
    state: str
    fips_5: str
    county_name: str | None = None
    addressable_borrowers: int


class GeographyScopePayload(BaseModel):
    state_count: int
    county_count: int
    zip_count: int | None = None
    snapshot_date: str | None = None
    source_table: str | None = None
    scope_label: str
    counties: list[GeographyScopeCounty] = Field(default_factory=list)


class ConfigOptionsResponse(BaseModel):
    lender_name: str
    geographies: list[str]
    geographies_status: str
    geography_scope: GeographyScopePayload | None = None
    occupancy: list[str]
    lien_status: list[str]
    lender_relationships: list[str]
    products: list[str]
    equity_thresholds: list[str]
    target_lender_refs: list[str] = Field(default_factory=list)
    target_lender_refs_status: str = "unavailable"


class FootprintState(BaseModel):
    state_code: str
    state_name: str
    display_order: int
    is_default_state: bool


class ConfigFootprintResponse(BaseModel):
    states: list[FootprintState]
    geography_scope: GeographyScopePayload | None = None
    using_fallback: bool = False

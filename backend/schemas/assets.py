"""Governed Unity Catalog asset metadata response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from backend.schemas.data_estate import DataEstateStatus

AssetFreshness = Literal["fresh", "aging", "stale", "unavailable"]
AssetObjectType = Literal["table", "view", "function"]
AssetLineageDirection = Literal["upstream", "downstream", "observed"]


class AssetColumn(BaseModel):
    name: str
    data_type: str | None = None
    comment: str | None = None
    ordinal_position: int | None = None
    redacted: bool = False


class AssetTag(BaseModel):
    name: str
    value: str | None = None


class AssetProperty(BaseModel):
    name: str
    value: str | None = None


class AssetLineageNode(BaseModel):
    direction: AssetLineageDirection
    asset_path: str
    label: str
    object_type: str | None = None
    event_time: str | None = None
    source: str = "system.access.table_lineage"


class AssetMetadataResponse(BaseModel):
    asset_path: str
    title: str
    description: str
    object_type: AssetObjectType
    status: DataEstateStatus | Literal["unknown"] = "unknown"
    freshness: AssetFreshness = "unavailable"
    catalog: str
    schema_name: str
    object_name: str
    uc_object: str
    generated_at: datetime
    last_updated: str | None = None
    delta_last_modified: str | None = None
    row_count: int | None = None
    row_count_source: Literal["source_readiness", "delta_stats", "count", "unavailable"] = "unavailable"
    num_files: int | None = None
    size_in_bytes: int | None = None
    size_label: str | None = None
    catalog_explorer_url: str | None = None
    source_note: str | None = None
    checked_at: str | None = None
    tags: list[AssetTag] = Field(default_factory=list)
    properties: list[AssetProperty] = Field(default_factory=list)
    columns: list[AssetColumn] = Field(default_factory=list)
    ddl: str | None = None
    ddl_redacted_lines: int = 0
    lineage: list[AssetLineageNode] = Field(default_factory=list)
    known_data_gaps: list[str] = Field(default_factory=list)

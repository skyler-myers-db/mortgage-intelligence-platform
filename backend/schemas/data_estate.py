from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

DataEstateStatus = Literal[
    "live",
    "demo_synthetic",
    "configured_empty",
    "not_configured",
    "roadmap",
    "permission_denied",
    "error",
]


class DataEstateAsset(BaseModel):
    name: str
    label: str
    status: DataEstateStatus
    uc_object: str | None = None
    row_count: int | None = None
    last_updated: str | None = None
    note: str = ""
    synthetic_demo: bool = False


class DataEstateLane(BaseModel):
    id: str
    title: str
    description: str
    status: DataEstateStatus
    assets: list[DataEstateAsset] = Field(default_factory=list)


class DataEstateResponse(BaseModel):
    generated_at: datetime
    lender_name: str
    public_demo_masking: bool
    lanes: list[DataEstateLane]
    known_data_gaps: list[str] = Field(default_factory=list)
    proof_assets: list[str] = Field(default_factory=list)

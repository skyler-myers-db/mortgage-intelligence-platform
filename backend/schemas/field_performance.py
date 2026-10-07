"""Admin Field performance response (D-platform-process-d2 step 11).

p75 values read from the browser RUM day aggregates (mip_app.rum_daily): no
borrower data and no identifier of any kind. A cell below the sample floor
carries no p75 and no rating, only its sample count.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

FieldRating = Literal["good", "needs_improvement", "poor"]


class FieldPerformanceCell(BaseModel):
    p75: float | None = Field(description="75th percentile; null below the sample floor.")
    rating: FieldRating | None = Field(description="Core Web Vitals band of the p75; null below the sample floor.")
    samples: int
    floor_applied: bool = Field(description="True when the cell has too few samples to show a p75.")


class FieldPerformanceVitalsRow(BaseModel):
    route: str = Field(description="Route registry template.")
    lcp: FieldPerformanceCell
    inp: FieldPerformanceCell
    cls: FieldPerformanceCell


class FieldPerformanceInteractionRow(BaseModel):
    route: str = Field(description="Route registry template.")
    interaction_target: str = Field(description="Closed data-rum-target value.")
    samples: int
    inp: FieldPerformanceCell
    input_delay: FieldPerformanceCell
    processing: FieldPerformanceCell
    presentation: FieldPerformanceCell


class FieldPerformanceClientErrorRow(BaseModel):
    route: str = Field(description="Route registry template.")
    error_name: str
    error_kind: str
    boundary: str | None
    count: int


class FieldPerformanceResponse(BaseModel):
    days: Literal[7, 28]
    since: date = Field(description="First UTC day included.")
    browser_telemetry: Literal["on", "off"] = Field(description="The effective RUM setting of this App process.")
    builds: list[str] = Field(description="Contributing build ids, sorted.")
    vitals: list[FieldPerformanceVitalsRow]
    interactions: list[FieldPerformanceInteractionRow]
    client_errors: list[FieldPerformanceClientErrorRow]


__all__ = [
    "FieldPerformanceCell",
    "FieldPerformanceClientErrorRow",
    "FieldPerformanceInteractionRow",
    "FieldPerformanceResponse",
    "FieldPerformanceVitalsRow",
    "FieldRating",
]

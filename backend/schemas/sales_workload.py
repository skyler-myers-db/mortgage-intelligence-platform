"""Per-officer workload for the Sales ops manager view (audit 2026-09-21 critic-10 item 6c).

``GET /api/sales/workload`` answers, for every active loan officer the
manager may see, how many leads they hold, how many have gone stale, and how
many were assigned and worked in the chosen period and the same span before
it. Lakebase app state only; audit-free.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from backend.schemas.common import validate_internal_staff_email


class SalesWorkloadRow(BaseModel):
    """One loan officer's load."""

    #: The roster key; the Lead Queue's assigned_to filter. Never shown as text.
    email: str
    display_label: str
    region: str | None = None
    capacity_per_day: int = Field(ge=0, le=250)
    #: Active (unreleased) assignments now.
    open_count: int = Field(ge=0)
    #: Open assignments older than stale_days with no disposition since they were assigned.
    stale_count: int = Field(ge=0)
    #: Assignments made inside the period.
    assigned_count: int = Field(ge=0)
    #: Distinct borrowers with a disposition by this officer inside the period.
    worked_count: int = Field(ge=0)
    previous_assigned_count: int = Field(ge=0)
    previous_worked_count: int = Field(ge=0)

    @field_validator("email")
    @classmethod
    def _staff_email(cls, value: str) -> str:
        return validate_internal_staff_email(value)


class SalesWorkloadResponse(BaseModel):
    """Every in-scope loan officer, for a period and the same span before it."""

    from_date: str
    to_date: str
    previous_from_date: str
    previous_to_date: str
    stale_days: int = Field(ge=1, le=30)
    rows: list[SalesWorkloadRow]

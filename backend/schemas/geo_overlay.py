"""Assigned-vs-unattended geography overlay contracts (S9).

The overlay answers "where are leads nobody is working?" per geography
unit. It is the difference of two live systems of record:

* **Leads** — marketing-eligible borrowers in ``mip.gold.borrower_360``,
  the exact population the geo-drilled Lead Queue serves (FIX β kept the
  map and the queue on the same definition; this overlay inherits it).
* **Assignments** — active rows (``released_at IS NULL``) in the
  Lakebase ``mip_app.lead_assignments`` table, resolved to geography by
  joining ``borrower_id`` back to ``borrower_360``.

Loan-officer coverage is an array-membership join against the
``coverage_states`` / ``coverage_counties`` arrays on
``mip_app.loan_officers`` — no geometry is involved.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

GeoOverlayLevel = Literal["state", "county", "zip"]

# Honest, UI-facing definition of the "lead" side of the subtraction.
# Surfaced in the API response so the map legend can cite it verbatim.
LEAD_DEFINITION = (
    "Marketing-eligible borrowers in the live lead queue "
    "(mip.gold.borrower_360, marketing_eligible = TRUE)"
)


class GeoAssignmentOverlayUnit(BaseModel):
    """One geography unit's assigned-vs-unattended breakdown."""

    unit_id: str = Field(
        description=(
            "State USPS code at level=state, 5-char county FIPS at "
            "level=county, 5-digit ZIP at level=zip."
        ),
    )
    lead_count: int = Field(ge=0, description="Live marketing-eligible lead count in the unit.")
    assigned_count: int = Field(
        ge=0,
        description=(
            "Leads in the unit currently held by an active (unreleased) "
            "loan-officer assignment."
        ),
    )
    unattended_count: int = Field(
        ge=0,
        description="lead_count - assigned_count; leads with no active assignment.",
    )
    covering_officer_count: int = Field(
        ge=0,
        description=(
            "Active loan officers whose coverage arrays include this unit "
            "(county FIPS membership, or state membership which implies its "
            "counties and ZIPs)."
        ),
    )
    covering_officers: list[str] = Field(
        default_factory=list,
        description="Display names of the covering officers (roster order).",
    )


class GeoAssignmentOverlayResponse(BaseModel):
    """Assigned-vs-unattended overlay for one drill level."""

    level: GeoOverlayLevel
    state: str | None = Field(
        default=None,
        description="Parent state (echoed at level=county; derived at level=zip).",
    )
    county_fips: str | None = Field(
        default=None,
        description="Parent county FIPS (echoed at level=zip).",
    )
    units: list[GeoAssignmentOverlayUnit]
    total_leads: int = Field(ge=0)
    total_assigned: int = Field(ge=0)
    total_unattended: int = Field(ge=0)
    lead_definition: str = Field(
        default=LEAD_DEFINITION,
        description="Copy-ready definition of what counts as a lead here.",
    )

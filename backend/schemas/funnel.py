"""S6 approval-funnel analytics contracts.

The funnel spans BOTH data planes on purpose:

* Population stages (population, high_opportunity) aggregate over the S1
  headline metric view ``mip.semantics.portfolio_headline_metric_view`` —
  the high-opportunity predicate is the view's ``is_high_opportunity``
  indicator (canonical ``fn_high_opportunity`` threshold; no literal here).
* Workflow stages (approved, actioned, outcome_recorded) read live
  Lakebase app state: the S2 ``lead_assignments`` lifecycle plus the
  ``approvals`` human-decision ledger and the S6 outcome rows on
  ``feedback``.

Every count carries its ``source`` so the UI's EvidenceDrawer can cite
exactly where the number came from.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from backend.schemas.loan_officer import LoanOfficerAssignment

ApprovalFunnelStageName = Literal[
    "population",
    "high_opportunity",
    "approved",
    "actioned",
    "outcome_recorded",
]

APPROVAL_FUNNEL_STAGES: tuple[ApprovalFunnelStageName, ...] = (
    "population",
    "high_opportunity",
    "approved",
    "actioned",
    "outcome_recorded",
)


class FunnelPopulation(BaseModel):
    """UC side of the funnel: borrower population + high-opportunity count."""

    population: int = Field(default=0, ge=0)
    high_opportunity: int = Field(default=0, ge=0)
    source: str = "mip.semantics.portfolio_headline_metric_view"


class ApprovalFunnelStage(BaseModel):
    stage: ApprovalFunnelStageName
    stage_order: int = Field(ge=1, le=5)
    label: str
    borrower_count: int = Field(ge=0)
    source: str


class ApproverActivityRow(BaseModel):
    """Who approved what — one row per human approve decision."""

    approval_id: str
    borrower_id: str
    offer_code: str | None = None
    actor_email: str
    assigned_to_email: str | None = None
    decided_at: datetime


class AssignmentOutcomeCounts(BaseModel):
    success: int = Field(default=0, ge=0)
    no_response: int = Field(default=0, ge=0)
    declined: int = Field(default=0, ge=0)


class LoanOfficerFunnelRow(BaseModel):
    """Per-officer active-assignment counts across the S2 lifecycle."""

    loan_officer_id: str
    display_name: str
    email: str
    assigned: int = Field(default=0, ge=0)
    contact_drafted: int = Field(default=0, ge=0)
    approved: int = Field(default=0, ge=0)
    actioned: int = Field(default=0, ge=0)
    outcome_recorded: int = Field(default=0, ge=0)
    total_active: int = Field(default=0, ge=0)


class ApprovalFunnelResponse(BaseModel):
    generated_at: datetime
    stages: list[ApprovalFunnelStage]
    approvals: list[ApproverActivityRow]
    loan_officers: list[LoanOfficerFunnelRow]


class LoanOfficerFunnelDetailResponse(BaseModel):
    officer: LoanOfficerFunnelRow
    assignments: list[LoanOfficerAssignment]
    outcome_counts: AssignmentOutcomeCounts

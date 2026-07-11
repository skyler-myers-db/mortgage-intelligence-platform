"""Loan-officer entity and assignment-lifecycle contracts (S2)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.schemas.common import (
    validate_internal_staff_email,
    validate_public_borrower_id,
    validate_public_opaque_id,
)

AssignmentLifecycleStatus = Literal[
    "assigned",
    "contact_drafted",
    "approved",
    "actioned",
    "outcome_recorded",
]

# Reviewed lifecycle, in order. Transitions are strictly one step forward;
# backend/services/loan_officer_state.py enforces it and the S6 approval
# funnel reads it.
ASSIGNMENT_LIFECYCLE: tuple[AssignmentLifecycleStatus, ...] = (
    "assigned",
    "contact_drafted",
    "approved",
    "actioned",
    "outcome_recorded",
)

# S6 recorded outcome for an actioned assignment. This is the reviewed
# terminal-verdict vocabulary; it is intentionally DISTINCT from the call
# disposition outcomes (connected / callback_scheduled / ...) and from the
# customer-system lead outcome types (closed_funded / ...).
AssignmentOutcome = Literal["success", "no_response", "declined"]

ASSIGNMENT_OUTCOMES: tuple[AssignmentOutcome, ...] = (
    "success",
    "no_response",
    "declined",
)

# Outcomes reuse the existing mip_app.feedback row shape; the outcome value
# is encoded in feedback.event_type so per-outcome counts remain a GROUP BY
# on the indexed event_type column. Keep in sync with the partial-unique
# index predicate in lakebase/schema.sql (S6 appendix).
ASSIGNMENT_OUTCOME_EVENT_PREFIX = "assignment_outcome_"


def assignment_outcome_event_type(outcome: AssignmentOutcome) -> str:
    """Return the feedback.event_type token for a recorded outcome."""
    return f"{ASSIGNMENT_OUTCOME_EVENT_PREFIX}{outcome}"

_STATE_CODE_PATTERN = re.compile(r"^[A-Z]{2}$")
_COUNTY_FIPS_PATTERN = re.compile(r"^\d{5}$")


class LoanOfficer(BaseModel):
    loan_officer_id: str
    email: str
    display_name: str
    coverage_states: list[str] = Field(default_factory=list, max_length=60)
    coverage_counties: list[str] = Field(default_factory=list, max_length=500)
    active: bool = True

    @field_validator("loan_officer_id")
    @classmethod
    def _loan_officer_id(cls, value: str) -> str:
        return validate_public_opaque_id(value)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return validate_internal_staff_email(value)

    @field_validator("coverage_states")
    @classmethod
    def _coverage_states(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        for value in values:
            state = str(value).strip().upper()
            if not _STATE_CODE_PATTERN.fullmatch(state):
                raise ValueError("coverage_states entries must be two-letter state codes")
            if state not in out:
                out.append(state)
        return out

    @field_validator("coverage_counties")
    @classmethod
    def _coverage_counties(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        for value in values:
            county = str(value).strip()
            if not _COUNTY_FIPS_PATTERN.fullmatch(county):
                raise ValueError("coverage_counties entries must be 5-digit county FIPS codes")
            if county not in out:
                out.append(county)
        return out


class LoanOfficerAssignment(BaseModel):
    assignment_id: str
    borrower_id: str
    loan_officer_id: str | None = None
    loan_officer_email: str
    loan_officer_name: str | None = None
    status: AssignmentLifecycleStatus = "assigned"
    assigned_by: str
    assigned_at: datetime
    status_updated_at: datetime | None = None
    released_at: datetime | None = None


class AssignLoanOfficerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    borrower_id: str
    loan_officer_id: str
    request_id: str | None = None

    @field_validator("borrower_id")
    @classmethod
    def _borrower_id(cls, value: str) -> str:
        return validate_public_borrower_id(value)

    @field_validator("loan_officer_id", "request_id")
    @classmethod
    def _opaque_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_public_opaque_id(value)


class AssignmentStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AssignmentLifecycleStatus
    request_id: str | None = None

    @field_validator("request_id")
    @classmethod
    def _request_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_public_opaque_id(value)


class LoanOfficerAssignmentResponse(BaseModel):
    assignment: LoanOfficerAssignment
    audit_event_id: str | None = None


class AssignmentOutcomeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: AssignmentOutcome
    request_id: str | None = None

    @field_validator("request_id")
    @classmethod
    def _request_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_public_opaque_id(value)


class AssignmentOutcomeResponse(BaseModel):
    assignment: LoanOfficerAssignment
    outcome: AssignmentOutcome
    feedback_id: str
    audit_event_id: str | None = None

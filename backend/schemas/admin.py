"""Admin rules, source readiness, operations, and health response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from backend.schemas.common import validate_public_opaque_id


class AdminRuleThreshold(BaseModel):
    key: str
    value: float
    unit: str | None = None
    label: str | None = None
    description: str | None = None
    sort_order: int | None = None
    last_updated: str | None = None


class AdminRulesResponse(BaseModel):
    offer_rules_version: str
    rules_edited_at: str | None = None
    thresholds: list[AdminRuleThreshold] = Field(default_factory=list)


class AdminRulesUpdateResponse(BaseModel):
    accepted: bool = False


class AdminSourceResponse(BaseModel):
    name: str
    status: str
    rows: int | None = None
    last_updated: str | None = None
    checked_at: str | None = None
    note: str
    synthetic_demo: bool = False


class AdminSettingsResponse(BaseModel):
    app_env: str
    lender_name: str
    catalog: str
    gold_schema: str
    lakebase_schema: str
    warehouse_id: str | None = None


AdminOperationJobKey = Literal[
    "fred_rates",
    "silver_refresh",
    "gold_refresh",
    "lifecycle_sync",
]


class AdminOperationRun(BaseModel):
    run_id: int | None = None
    life_cycle_state: str | None = None
    result_state: str | None = None
    state_message: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    run_page_url: str | None = None
    active: bool = False


class AdminOperationJobStatus(BaseModel):
    key: AdminOperationJobKey
    label: str
    job_name: str
    job_id: int | None = None
    configured: bool
    description: str
    run_order: int
    cooldown_remaining_s: int = 0
    latest_run: AdminOperationRun | None = None


class AdminOperationsResponse(BaseModel):
    jobs: list[AdminOperationJobStatus] = Field(default_factory=list)


class AdminOperationRunRequest(BaseModel):
    job_key: AdminOperationJobKey
    confirm: bool = False
    reason: Literal[
        "operator_refresh",
        "release_validation",
        "source_update",
        "support_triage",
    ] | None = None
    request_id: str | None = Field(default=None, max_length=120)

    @field_validator("request_id")
    @classmethod
    def _request_id_is_public_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_public_opaque_id(value)


class AdminOperationRunResponse(BaseModel):
    accepted: bool
    key: AdminOperationJobKey
    label: str
    job_name: str
    job_id: int
    run_id: int | None = None
    run_page_url: str | None = None
    audit_event_id: str | None = None

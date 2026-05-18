"""Admin rules, source readiness, and health response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


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
    warehouse_id: str

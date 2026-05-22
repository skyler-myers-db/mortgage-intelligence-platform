"""Health-check response schemas for public and admin diagnostics."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    mode: str
    dependencies: dict[str, str] = Field(default_factory=dict)
    circuit_breakers: dict[str, str] = Field(default_factory=dict)
    actor_cache_key: str | None = None


class BoundaryWarning(BaseModel):
    code: str
    severity: str = "warning"
    message: str
    recommended_action: str
    docs_ref: str


class AdminHealthResponse(HealthResponse):
    app_env: str | None = None
    warehouse_id: str | None = None
    breaker_state_changes_last_hour: int = 0
    recent_errors_count: int = 0
    counters_persistence: str = "process-local"
    log_export: str = "stdout-only"
    fallback_identity_fallbacks_process_total: int = 0
    fallback_identity_fallbacks_total: int = 0
    boundary_warning: BoundaryWarning | None = None

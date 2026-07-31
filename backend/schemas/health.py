"""Health-check response schemas for public and admin diagnostics."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ForcedDegradedInfo(BaseModel):
    active: bool
    dependency: str
    source: str
    expires_in_s: int


class HealthResponse(BaseModel):
    status: str
    mode: str
    git_sha: str | None = None
    deployment_lease_id: str | None = None
    agent_gateway_binding_sha256: str | None = None
    dependencies: dict[str, str] = Field(default_factory=dict)
    circuit_breakers: dict[str, str] = Field(default_factory=dict)
    actor_cache_key: str | None = None
    forced_degraded: ForcedDegradedInfo | None = None
    # Deploy-promotion marker state: "enabled" after a governed
    # scripts/deploy.sh snapshot promotion, "disabled_baseline_deploy" when
    # the process came from a bare deploy (treatment writes 503, status
    # reports degraded). Absent on the anonymous liveness body.
    campaign_treatment_runtime: str | None = None


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

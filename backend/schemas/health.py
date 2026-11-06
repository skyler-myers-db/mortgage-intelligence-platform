"""Health-check response schemas for public and admin diagnostics."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

#: Closed failure classes for a Genie model turn. The recorder in
#: backend/services/genie_turn_outcome.py maps every upstream failure onto
#: this set (UNKNOWN when nothing matches), so free text cannot reach health.
GenieTurnErrorClass = Literal[
    "BUDGET_EXCEEDED",
    "QUOTA_EXCEEDED",
    "RATE_LIMITED",
    "TIMEOUT",
    "AUTH",
    "NOT_FOUND",
    "UPSTREAM_ERROR",
    "NETWORK",
    "CANCELED",
    "EXPIRED",
    "FAILED",
    "MALFORMED_RESPONSE",
    "UNKNOWN",
]


class ForcedDegradedInfo(BaseModel):
    active: bool
    dependency: str
    source: str
    expires_in_s: int


class GenieLastTurn(BaseModel):
    """Outcome of the most recent real Genie model turn in this process.

    Closed fields only: never question text, SQL, rows, ids or upstream
    error text (the class is decided where the error is raised).
    """

    outcome: Literal["success", "failed"]
    error_class: GenieTurnErrorClass | None = Field(
        default=None,
        description="Sanitized failure class; null when the turn succeeded.",
    )
    at: datetime = Field(description="When the outcome was recorded (UTC).")


class HealthResponse(BaseModel):
    status: str
    mode: str
    git_sha: str | None = None
    deployment_lease_id: str | None = None
    agent_gateway_binding_sha256: str | None = None
    # A plain dict on the wire on purpose: a Literal value type would turn any
    # stray probe value into a 500 on /health (audit delivery-01).
    dependencies: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Per-dependency state (warehouse, lakebase, genie): up | down | resuming | "
            "degraded. 'resuming' (warehouse only) means the serverless warehouse is "
            "starting from auto-stop. 'degraded' (genie only) means the space answers "
            "its ping but the most recent model turn failed on a budget or quota class "
            "recently (see dependency_reasons); asking Genie stays enabled. Consumers "
            "treat any value other than 'down' as not an outage."
        ),
    )
    dependency_reasons: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Why a dependency reads 'degraded', keyed like dependencies: a closed "
            "failure class such as BUDGET_EXCEEDED. Absent when nothing is degraded."
        ),
    )
    circuit_breakers: dict[str, str] = Field(default_factory=dict)
    actor_cache_key: str | None = None
    actor_cache_key_aliases: list[str] = Field(
        default_factory=list,
        description=(
            "The same actor's cache key under the previous rotation secret, "
            "while one is configured (D-identity-review-a4): an open tab holding "
            "it re-stamps instead of resetting, and requests bound to it are "
            "still accepted. Empty outside a rotation. Authenticated body only."
        ),
    )
    # Validated workspace origin for Catalog Explorer deep links from cited
    # UC assets. Authenticated health body only.
    workspace_host: str | None = None
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
    auditor_role_overlap: int = Field(
        default=0,
        description=(
            "Identities configured as auditor that are also configured as "
            "administrator or approver (segregation-of-duties signal). A count only."
        ),
    )
    presenter_mode: bool = False
    genie_last_turn: GenieLastTurn | None = Field(
        default=None,
        description=(
            "Outcome of the most recent real Genie model turn in this App process "
            "(process-local, reset on restart); null when no turn has run since boot. "
            "Health never runs a turn itself."
        ),
    )
    browser_telemetry: Literal["on", "off"] = Field(
        default="off",
        description="The effective browser RUM setting of this App process (D-platform-process-d1).",
    )

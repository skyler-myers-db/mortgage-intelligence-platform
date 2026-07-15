"""Ownership policy for audit event types emitted by governed server routes."""

from __future__ import annotations

SERVER_OWNED_AUDIT_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "ACTIVATION_STAGE",
        "APPROVE",
        "CALL_DISPOSITION",
        "CAMPAIGN_STATUS_UPDATE",
        "DELETE_DRAFT",
        "DRAFT_OUTREACH",
        "FORCE_DEGRADED",
        "GENIE_FEEDBACK",
        "GENIE_FEEDBACK_INTENT",
        "GROWTH_AGENT_COMPOSE",
        "GROWTH_AGENT_NOTIFICATION_DRAFT",
        "GROWTH_AGENT_PLAN_STEP",
        "GROWTH_AGENT_RUN",
        "LEAD_ASSIGN",
        "LEAD_ASSIGNMENT_STATUS",
        "LEAD_DISTRIBUTE",
        "LEAD_OUTCOME",
        "LEAD_OUTCOME_RECORDED",
        "OUTREACH_APPROVE",
        "OUTREACH_REJECT",
        "PORTFOLIO_CREATE",
        "PROPERTY_LOOKUP",
        "RECOMMEND_OFFER",
        "RUN_GENIE",
        "SAVE_DRAFT",
        "SAVE_LEAD",
        "UNSAVE_LEAD",
        "VIEW_BORROWER",
        "VIEW_BORROWER_PROOF",
        "VIEW_LEADS",
    }
)

SERVER_OWNED_AUDIT_EVENT_PREFIXES: tuple[str, ...] = (
    "ACTIVATION_",
    "ADMIN_OPERATION_",
    "GENIE_ACTION_",
    "GROWTH_AGENT_",
    "LEAD_",
)


def is_server_owned_audit_event_type(event_type: str) -> bool:
    """Return whether only a governed server route may emit ``event_type``."""

    normalized = event_type.strip().upper()
    return normalized in SERVER_OWNED_AUDIT_EVENT_TYPES or normalized.startswith(
        SERVER_OWNED_AUDIT_EVENT_PREFIXES
    )

"""Shared leaf layer for the sales lifecycle store: module state, outcome-source
vocabulary, SQL text, cache accessors, and Lakebase row mappers."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.config.settings import settings
from backend.schemas.sales import (
    CallDisposition,
    LeadAssignment,
    LeadOutcome,
    SalesTeamMember,
)
from backend.services.lakebase import LakebaseClient, get_lakebase_client
from backend.services.pii_redaction import normalize_public_lender_ref
from backend.services.resilience import TTLCache

_SALES_STATE_CACHE = TTLCache(max_entries=1024)

_OUTCOME_SOURCE_SYSTEMS: tuple[str, ...] = (
    "salesforce",
    "crm_cdp",
    "los_pos",
    "servicing",
    "webhook",
    "manual_import",
)
_OUTCOME_SOURCE_LABELS: dict[str, str] = {
    "salesforce": "Salesforce CRM",
    "crm_cdp": "Customer CRM / CDP",
    "los_pos": "LOS / POS",
    "servicing": "Servicing platform",
    "webhook": "Customer webhook",
    "manual_import": "Manual import",
}
_CONFIGURED_OUTCOME_SOURCE_STATUSES: frozenset[str] = frozenset({"connected", "dry_run"})
_PUBLIC_COMPETITOR_LABEL_RE = re.compile(r"^Competitor ([A-Z]|Other)$")

_OUTCOME_SOURCE_STATUS_BY_TYPE = """
SELECT destination_type AS source_system, display_name, status
FROM mip_app.activation_destinations
WHERE destination_type = %(source_system)s
ORDER BY
  CASE status
    WHEN 'connected' THEN 1
    WHEN 'dry_run' THEN 2
    WHEN 'not_configured' THEN 3
    ELSE 4
  END,
  updated_at DESC
LIMIT 1
"""

_OUTCOME_SOURCE_STATUS_LIST = """
SELECT destination_type AS source_system, display_name, status
FROM mip_app.activation_destinations
WHERE destination_type IN ('salesforce','crm_cdp','los_pos','servicing','webhook')
ORDER BY
  CASE status
    WHEN 'connected' THEN 1
    WHEN 'dry_run' THEN 2
    WHEN 'not_configured' THEN 3
    ELSE 4
  END,
  display_name ASC
"""

_SALES_AUDIT_INSERT_SQL = """
INSERT INTO mip_app.action_audit (
    event_type, actor_email, entity_type, entity_id,
    subject_clip, subject_segment, request_id,
    correlation_id, evidence_ids, metadata
) VALUES (
    %(event_type)s, %(actor_email)s, %(entity_type)s, %(entity_id)s,
    %(subject_clip)s, %(subject_segment)s, %(request_id)s,
    %(correlation_id)s, %(evidence_ids)s, %(metadata)s::jsonb
)
RETURNING audit_id, event_at
"""


def get_sales_lakebase() -> LakebaseClient:
    return get_lakebase_client()


def clear_sales_state_cache() -> None:
    _SALES_STATE_CACHE.clear()
    # S6: every approval-workflow write path (approve/reject, assignment
    # lifecycle, outcome recording) already calls this hook, so the live
    # approval funnel invalidates with it — a UI approval must move the
    # funnel within one cache TTL. Late import: approval_funnel depends on
    # schemas + lakebase only, but keeping it out of module scope avoids
    # ever creating an import cycle with future funnel reads of sales state.
    from backend.services.approval_funnel import clear_approval_funnel_cache

    clear_approval_funnel_cache()


def _sales_state_ttl_s() -> float:
    return settings.mip_sales_state_cache_ttl_s


def _cache_get(key: str) -> Any | None:
    if _sales_state_ttl_s() <= 0:
        return None
    return _SALES_STATE_CACHE.get(key)


def _cache_set(key: str, value: Any) -> None:
    ttl_s = _sales_state_ttl_s()
    if ttl_s > 0:
        _SALES_STATE_CACHE.set(key, deepcopy(value), ttl_s)


def _copy_assignment(value: LeadAssignment | None) -> LeadAssignment | None:
    return value.model_copy(deep=True) if value is not None else None


def _copy_disposition(value: CallDisposition | None) -> CallDisposition | None:
    return value.model_copy(deep=True) if value is not None else None


def _copy_member(value: SalesTeamMember) -> SalesTeamMember:
    return value.model_copy(deep=True)


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _datetime_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _datetimes_equal(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return left is right
    return _datetime_utc(left) == _datetime_utc(right)


def _public_competitor_label(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        normalized = normalize_public_lender_ref(str(value))
    except ValueError:
        return "Competitor Other"
    if normalized is None:
        return None
    if not _PUBLIC_COMPETITOR_LABEL_RE.fullmatch(normalized):
        return "Competitor Other"
    return normalized


def _assignment_from_row(row: dict[str, Any] | None) -> LeadAssignment | None:
    if not row:
        return None
    return LeadAssignment(
        assignment_id=str(row["assignment_id"]),
        borrower_id=str(row["borrower_id"]),
        assigned_to_email=str(row["assigned_to_email"]),
        assigned_to_label=row.get("assigned_to_label"),
        assigned_by=str(row["assigned_by"]),
        assigned_at=row["assigned_at"],
        expires_at=row.get("expires_at"),
        released_at=row.get("released_at"),
        strategy=row.get("strategy") or "manual",
        status=row.get("status") or "assigned",
    )


def _assignment_duration_matches(
    assignment: LeadAssignment,
    expires_in_hours: int | None,
) -> bool:
    if expires_in_hours is None:
        return assignment.expires_at is None
    if assignment.expires_at is None:
        return False
    expected = _datetime_utc(assignment.assigned_at) + timedelta(hours=expires_in_hours)
    actual = _datetime_utc(assignment.expires_at)
    return abs((actual - expected).total_seconds()) <= 1


def _disposition_from_row(row: dict[str, Any] | None) -> CallDisposition | None:
    if not row:
        return None
    return CallDisposition(
        disposition_id=str(row["disposition_id"]),
        borrower_id=str(row["borrower_id"]),
        lo_email=str(row["lo_email"]),
        outcome=row["outcome"],
        attempt_number=int(row.get("attempt_number") or 1),
        occurred_at=row["occurred_at"],
        callback_at=row.get("callback_at"),
        notes=row.get("notes"),
        audit_event_id=str(row["audit_event_id"]) if row.get("audit_event_id") else None,
    )


def _outcome_from_row(row: dict[str, Any] | None) -> LeadOutcome | None:
    if not row:
        return None
    return LeadOutcome(
        outcome_id=str(row["outcome_id"]),
        borrower_id=str(row["borrower_id"]),
        outcome_type=row["outcome_type"],
        source_system=row["source_system"],
        source_record_ref=row.get("source_record_ref"),
        assigned_to_email=row.get("assigned_to_email"),
        campaign_id=str(row["campaign_id"]) if row.get("campaign_id") else None,
        loan_amount=row.get("loan_amount"),
        competitor_lender_label=row.get("competitor_lender_label"),
        occurred_at=row["occurred_at"],
        request_id=row.get("request_id"),
        audit_event_id=str(row["audit_event_id"]) if row.get("audit_event_id") else None,
        created_at=row.get("created_at"),
    )

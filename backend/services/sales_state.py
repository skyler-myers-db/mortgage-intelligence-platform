"""Lakebase-backed sales lifecycle state helpers with short-lived caches."""

from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends

from backend.config.settings import settings
from backend.schemas.lead import LeadSummary
from backend.schemas.sales import (
    AssignmentStrategy,
    CallDisposition,
    CallDispositionOutcome,
    LeadAssignment,
    LeadOutcome,
    LeadOutcomeSourceSystem,
    LeadOutcomeType,
    SalesTeamMember,
)
from backend.services.audit_lakebase_store import _build_insert_params
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client
from backend.services.pii_redaction import normalize_public_lender_ref, scrub_free_text
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


LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]


def get_sales_state_store(client: LakebaseDep) -> SalesStateStore:
    return SalesStateStore(client)


def clear_sales_state_cache() -> None:
    _SALES_STATE_CACHE.clear()


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


class SalesStateStore:
    """Lakebase-backed sales work-management state.

    UC gold remains the source for borrower facts and scoring. Lakebase is the
    authoritative app-state ledger for manager assignment and LO disposition
    actions, because those are human workflow events rather than Cotality facts.
    """

    def __init__(self, client: LakebaseClient | None = None) -> None:
        self._client = client or get_sales_lakebase()

    def list_team(self, *, actor: str | None = None) -> list[SalesTeamMember]:
        cache_key = f"sales_state:list_team:{actor or '_all'}"
        cached = _cache_get(cache_key)
        if isinstance(cached, list) and all(isinstance(member, SalesTeamMember) for member in cached):
            return [member.model_copy(deep=True) for member in cached]
        actor_member = self.require_active_team_member(actor) if actor else None
        rows = self._client.fetchall(
            """
            SELECT email, display_label, role, region, manager_email, capacity_per_day, active
            FROM mip_app.sales_team
            WHERE active = true
            ORDER BY role DESC, display_label ASC
            """,
            limit=200,
        )
        members = [SalesTeamMember(**row) for row in rows]
        if actor_member is None or actor_member.role == "admin":
            out = members
        elif actor_member.role == "sales_manager":
            out = [
                member
                for member in members
                if member.email == actor_member.email or member.manager_email == actor_member.email
            ]
        else:
            out = [member for member in members if member.email == actor_member.email]
        _cache_set(cache_key, out)
        return [member.model_copy(deep=True) for member in out]

    def require_active_team_member(self, email: str) -> SalesTeamMember:
        normalized_email = email.lower()
        cache_key = f"sales_state:team_member:{normalized_email}"
        cached = _cache_get(cache_key)
        if cached == "__missing__":
            raise KeyError(email)
        if isinstance(cached, SalesTeamMember):
            return _copy_member(cached)
        row = self._client.fetchone(
            """
            SELECT email, display_label, role, region, manager_email, capacity_per_day, active
            FROM mip_app.sales_team
            WHERE email = %(email)s AND active = true
            LIMIT 1
            """,
            {"email": normalized_email},
        )
        if row is None:
            _cache_set(cache_key, "__missing__")
            raise KeyError(email)
        member = SalesTeamMember(**row)
        _cache_set(cache_key, member)
        return _copy_member(member)

    def require_manager_actor(self, actor: str) -> SalesTeamMember:
        member = self.require_active_team_member(actor)
        if member.role not in {"admin", "sales_manager"}:
            raise PermissionError("sales manager or admin access required")
        return member

    def require_assignee_in_scope(self, *, actor: str, assigned_to_email: str) -> SalesTeamMember:
        actor_member = self.require_manager_actor(actor)
        assignee = self.require_active_team_member(assigned_to_email)
        if assignee.role != "loan_officer":
            raise KeyError(assigned_to_email)
        if actor_member.role != "admin" and assignee.manager_email != actor_member.email:
            raise PermissionError("loan officer is outside the manager scope")
        return assignee

    def require_visible_assignee(self, *, actor: str, assigned_to_email: str) -> SalesTeamMember:
        actor_member = self.require_active_team_member(actor)
        assignee = self.require_active_team_member(assigned_to_email)
        if assignee.role != "loan_officer":
            raise KeyError(assigned_to_email)
        if actor_member.role == "admin":
            return assignee
        if actor_member.role == "sales_manager" and assignee.manager_email == actor_member.email:
            return assignee
        if actor_member.role == "loan_officer" and assignee.email == actor_member.email:
            return assignee
        raise PermissionError("assigned_to is outside the actor scope")

    def require_disposition_scope(self, *, actor: str, lo_email: str) -> SalesTeamMember:
        actor_member = self.require_active_team_member(actor)
        lo = self.require_active_team_member(lo_email)
        if lo.role != "loan_officer":
            raise KeyError(lo_email)
        if actor_member.role == "admin":
            return lo
        if actor_member.role == "sales_manager" and lo.manager_email == actor_member.email:
            return lo
        if actor_member.role == "loan_officer" and lo.email == actor_member.email:
            return lo
        raise PermissionError("cannot log a disposition for another loan officer")

    def visible_lo_emails(self, *, actor: str) -> set[str] | None:
        """Return LO emails visible to actor; None means admin/all."""
        cache_key = f"sales_state:visible_lo_emails:{actor.lower()}"
        cached = _cache_get(cache_key)
        if cached == "__all__":
            return None
        if isinstance(cached, set):
            return set(cached)
        actor_member = self.require_active_team_member(actor)
        if actor_member.role == "admin":
            _cache_set(cache_key, "__all__")
            return None
        if actor_member.role == "loan_officer":
            out = {actor_member.email}
            _cache_set(cache_key, out)
            return set(out)
        members = self.list_team(actor=actor)
        out = {member.email for member in members if member.role == "loan_officer"}
        _cache_set(cache_key, out)
        return set(out)

    def _insert_audit_event(
        self,
        cur: Any,
        *,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str,
        payload_json: dict[str, Any],
        event_type: str,
        subject_clip: str | None = None,
        request_id: str | None = None,
    ) -> str:
        _, params = _build_insert_params(
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload_json=payload_json,
            event_type=event_type,
            subject_clip=subject_clip,
            request_id=request_id,
        )
        cur.execute(_SALES_AUDIT_INSERT_SQL, params)
        row = dict(cur.fetchone() or {})
        audit_id = row.get("audit_id")
        if audit_id is None:
            raise LakebaseError("sales audit insert returned no row")
        return str(audit_id)

    def active_assignment_for(self, borrower_id: str) -> LeadAssignment | None:
        cache_key = f"sales_state:active_assignment:{borrower_id}"
        cached = _cache_get(cache_key)
        if cached == "__none__":
            return None
        if isinstance(cached, LeadAssignment):
            return cached.model_copy(deep=True)
        assignment = _assignment_from_row(
            self._client.fetchone(
                """
                SELECT a.assignment_id, a.borrower_id, a.assigned_to_email,
                       t.display_label AS assigned_to_label, a.assigned_by,
                       a.assigned_at, a.expires_at, a.released_at, a.strategy
                FROM mip_app.lead_assignments a
                LEFT JOIN mip_app.sales_team t ON t.email = a.assigned_to_email
                WHERE a.borrower_id = %(borrower_id)s
                  AND a.released_at IS NULL
                ORDER BY a.assigned_at DESC
                LIMIT 1
                """,
                {"borrower_id": borrower_id},
            )
        )
        _cache_set(cache_key, assignment if assignment is not None else "__none__")
        return _copy_assignment(assignment)

    def latest_disposition_for(self, borrower_id: str) -> CallDisposition | None:
        cache_key = f"sales_state:latest_disposition:{borrower_id}"
        cached = _cache_get(cache_key)
        if cached == "__none__":
            return None
        if isinstance(cached, CallDisposition):
            return cached.model_copy(deep=True)
        disposition = _disposition_from_row(
            self._client.fetchone(
                """
                SELECT disposition_id, borrower_id, lo_email, outcome, attempt_number,
                       occurred_at, callback_at, notes, audit_event_id
                FROM mip_app.call_dispositions
                WHERE borrower_id = %(borrower_id)s
                ORDER BY occurred_at DESC, created_at DESC
                LIMIT 1
                """,
                {"borrower_id": borrower_id},
            )
        )
        _cache_set(cache_key, disposition if disposition is not None else "__none__")
        return _copy_disposition(disposition)

    def assignments_for(self, borrower_ids: list[str]) -> dict[str, LeadAssignment]:
        if not borrower_ids:
            return {}
        normalized = sorted({str(borrower_id) for borrower_id in borrower_ids})
        cache_key = "sales_state:assignments:" + ",".join(normalized)
        cached = _cache_get(cache_key)
        if isinstance(cached, dict) and all(isinstance(v, LeadAssignment) for v in cached.values()):
            return {str(k): v.model_copy(deep=True) for k, v in cached.items()}
        rows = self._client.fetchall(
            """
            SELECT a.assignment_id, a.borrower_id, a.assigned_to_email,
                   t.display_label AS assigned_to_label, a.assigned_by,
                   a.assigned_at, a.expires_at, a.released_at, a.strategy
            FROM mip_app.lead_assignments a
            LEFT JOIN mip_app.sales_team t ON t.email = a.assigned_to_email
            WHERE a.borrower_id = ANY(%(borrower_ids)s)
              AND a.released_at IS NULL
            ORDER BY a.borrower_id, a.assigned_at DESC
            """,
            {"borrower_ids": normalized},
            limit=max(len(normalized), 1),
        )
        out: dict[str, LeadAssignment] = {}
        for row in rows:
            borrower_id = str(row["borrower_id"])
            out.setdefault(borrower_id, _assignment_from_row(row))  # type: ignore[arg-type]
        result = {k: v for k, v in out.items() if v is not None}
        _cache_set(cache_key, result)
        return {k: v.model_copy(deep=True) for k, v in result.items()}

    def assignments_for_request(self, request_id: str | None) -> list[LeadAssignment]:
        return [
            assignment
            for row in self.assignment_rows_for_request(request_id)
            if (assignment := _assignment_from_row(row)) is not None
        ]

    def assignment_rows_for_request(self, request_id: str | None) -> list[dict[str, Any]]:
        if not request_id:
            return []
        return self._client.fetchall(
            """
            SELECT a.assignment_id, a.borrower_id, a.assigned_to_email,
                   t.display_label AS assigned_to_label, a.assigned_by,
                   a.assigned_at, a.expires_at, a.released_at, a.strategy,
                   COALESCE(a.assignment_scope, 'single') AS assignment_scope
            FROM mip_app.lead_assignments a
            LEFT JOIN mip_app.sales_team t ON t.email = a.assigned_to_email
            WHERE a.request_id = %(request_id)s
            ORDER BY a.assigned_at ASC
            """,
            {"request_id": request_id},
            limit=500,
        )

    def disposition_for_request(self, request_id: str | None) -> CallDisposition | None:
        if not request_id:
            return None
        return _disposition_from_row(
            self._client.fetchone(
                """
                SELECT disposition_id, borrower_id, lo_email, outcome, attempt_number,
                       occurred_at, callback_at, notes, audit_event_id
                FROM mip_app.call_dispositions
                WHERE request_id = %(request_id)s
                LIMIT 1
                """,
                {"request_id": request_id},
            )
        )

    def outcome_for_request(self, request_id: str | None) -> LeadOutcome | None:
        if not request_id:
            return None
        return _outcome_from_row(
            self._client.fetchone(
                """
                SELECT outcome_id, borrower_id, outcome_type, source_system,
                       source_record_ref, assigned_to_email, campaign_id,
                       loan_amount, competitor_lender_label, occurred_at,
                       request_id, audit_event_id, created_at
                FROM mip_app.lead_outcomes
                WHERE request_id = %(request_id)s
                LIMIT 1
                """,
                {"request_id": request_id},
            )
        )

    def outcome_for_source_record(
        self,
        *,
        source_system: LeadOutcomeSourceSystem,
        source_record_ref: str | None,
    ) -> LeadOutcome | None:
        if not source_record_ref:
            return None
        return _outcome_from_row(
            self._client.fetchone(
                """
                SELECT outcome_id, borrower_id, outcome_type, source_system,
                       source_record_ref, assigned_to_email, campaign_id,
                       loan_amount, competitor_lender_label, occurred_at,
                       request_id, audit_event_id, created_at
                FROM mip_app.lead_outcomes
                WHERE source_system = %(source_system)s
                  AND source_record_ref = %(source_record_ref)s
                LIMIT 1
                """,
                {"source_system": source_system, "source_record_ref": source_record_ref},
            )
        )

    def outcome_source_status(self, source_system: LeadOutcomeSourceSystem) -> dict[str, Any]:
        if source_system == "manual_import":
            return {
                "source_system": source_system,
                "display_name": _OUTCOME_SOURCE_LABELS[source_system],
                "status": "available",
                "configured": True,
            }
        row = self._client.fetchone(_OUTCOME_SOURCE_STATUS_BY_TYPE, {"source_system": source_system})
        status = str(row.get("status") if row else "not_configured")
        return {
            "source_system": source_system,
            "display_name": str(row.get("display_name") if row else _OUTCOME_SOURCE_LABELS.get(source_system, source_system)),
            "status": status,
            "configured": status in _CONFIGURED_OUTCOME_SOURCE_STATUSES,
        }

    def outcome_source_statuses(self) -> list[dict[str, Any]]:
        rows = self._client.fetchall(_OUTCOME_SOURCE_STATUS_LIST, limit=25)
        by_source = {str(row.get("source_system")): dict(row) for row in rows}
        out: list[dict[str, Any]] = []
        for source_system in _OUTCOME_SOURCE_SYSTEMS:
            if source_system == "manual_import":
                out.append(
                    {
                        "source_system": source_system,
                        "display_name": _OUTCOME_SOURCE_LABELS[source_system],
                        "status": "available",
                        "configured": True,
                    }
                )
                continue
            row = by_source.get(source_system)
            status = str(row.get("status") if row else "not_configured")
            out.append(
                {
                    "source_system": source_system,
                    "display_name": str(row.get("display_name") if row else _OUTCOME_SOURCE_LABELS.get(source_system, source_system)),
                    "status": status,
                    "configured": status in _CONFIGURED_OUTCOME_SOURCE_STATUSES,
                }
            )
        return out

    def _require_configured_outcome_source(self, source_system: LeadOutcomeSourceSystem) -> dict[str, Any]:
        status = self.outcome_source_status(source_system)
        if not status["configured"]:
            raise ValueError(f"{status['display_name']} outcome feed is not configured")
        return status

    def latest_dispositions_for(self, borrower_ids: list[str]) -> dict[str, CallDisposition]:
        if not borrower_ids:
            return {}
        normalized = sorted({str(borrower_id) for borrower_id in borrower_ids})
        cache_key = "sales_state:latest_dispositions:" + ",".join(normalized)
        cached = _cache_get(cache_key)
        if isinstance(cached, dict) and all(isinstance(v, CallDisposition) for v in cached.values()):
            return {str(k): v.model_copy(deep=True) for k, v in cached.items()}
        rows = self._client.fetchall(
            """
            SELECT DISTINCT ON (borrower_id)
                   disposition_id, borrower_id, lo_email, outcome, attempt_number,
                   occurred_at, callback_at, notes, audit_event_id
            FROM mip_app.call_dispositions
            WHERE borrower_id = ANY(%(borrower_ids)s)
            ORDER BY borrower_id, occurred_at DESC, created_at DESC
            """,
            {"borrower_ids": normalized},
            limit=max(len(normalized), 1),
        )
        result = {
            str(row["borrower_id"]): _disposition_from_row(row)  # type: ignore[dict-item]
            for row in rows
            if row.get("borrower_id")
        }
        result = {k: v for k, v in result.items() if v is not None}
        _cache_set(cache_key, result)
        return {k: v.model_copy(deep=True) for k, v in result.items()}

    def borrower_ids_for_assignee(self, assigned_to_email: str | None) -> list[str] | None:
        if assigned_to_email is None:
            return None
        rows = self._client.fetchall(
            """
            SELECT borrower_id
            FROM mip_app.lead_assignments
            WHERE assigned_to_email = %(assigned_to_email)s
              AND released_at IS NULL
            ORDER BY assigned_at DESC
            LIMIT 500
            """,
            {"assigned_to_email": assigned_to_email.lower()},
            limit=500,
        )
        return [str(row["borrower_id"]) for row in rows]

    def assign_lead(
        self,
        *,
        borrower_id: str,
        assigned_to_email: str,
        assigned_by: str,
        expires_in_hours: int | None,
        strategy: AssignmentStrategy,
        subject_clip: str | None = None,
        request_id: str | None = None,
    ) -> tuple[LeadAssignment, str]:
        assignee = self.require_assignee_in_scope(
            actor=assigned_by,
            assigned_to_email=assigned_to_email,
        )

        def _matches_existing(existing_rows: list[dict[str, Any]]) -> LeadAssignment | None:
            if len(existing_rows) != 1:
                return None
            row = existing_rows[0]
            assignment = _assignment_from_row(row)
            if assignment is None:
                return None
            if (
                str(row.get("assignment_scope") or "single") == "single"
                and assignment.borrower_id == borrower_id
                and assignment.assigned_by == assigned_by.lower()
                and assignment.assigned_to_email == assignee.email
                and assignment.strategy == strategy
                and _assignment_duration_matches(assignment, expires_in_hours)
            ):
                return assignment
            return None

        existing_rows = self.assignment_rows_for_request(request_id)
        if existing_rows:
            if assignment := _matches_existing(existing_rows):
                return assignment, ""
            raise PermissionError("request_id already belongs to a different assignment")
        with self._client.transaction() as conn, conn.cursor() as cur:
            if request_id:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%(lock_key)s))",
                    {"lock_key": f"lead_assignment:{request_id}"},
                )
                existing_rows = self.assignment_rows_for_request(request_id)
                if existing_rows:
                    if assignment := _matches_existing(existing_rows):
                        return assignment, ""
                    raise PermissionError("request_id already belongs to a different assignment")
            cur.execute(
                """
                UPDATE mip_app.lead_assignments
                SET released_at = now()
                WHERE borrower_id = %(borrower_id)s
                  AND released_at IS NULL
                """,
                {"borrower_id": borrower_id},
            )
            cur.execute(
                """
                INSERT INTO mip_app.lead_assignments (
                    borrower_id, assigned_to_email, assigned_by,
                    expires_at, strategy, request_id, assignment_scope
                )
                VALUES (
                    %(borrower_id)s, %(assigned_to_email)s, %(assigned_by)s,
                    CASE
                        WHEN %(expires_in_hours)s IS NULL THEN NULL
                        ELSE now() + (%(expires_in_hours)s::int * interval '1 hour')
                    END,
                    %(strategy)s, %(request_id)s, %(assignment_scope)s
                )
                ON CONFLICT (request_id)
                    WHERE request_id IS NOT NULL AND assignment_scope = 'single'
                    DO NOTHING
                RETURNING assignment_id, borrower_id, assigned_to_email,
                          assigned_by, assigned_at, expires_at, released_at, strategy
                """,
                {
                    "borrower_id": borrower_id,
                    "assigned_to_email": assignee.email,
                    "assigned_by": assigned_by.lower(),
                    "expires_in_hours": expires_in_hours,
                    "strategy": strategy,
                    "request_id": request_id,
                    "assignment_scope": "single",
                },
            )
            row = dict(cur.fetchone() or {})
            assignment = _assignment_from_row({**row, "assigned_to_label": assignee.display_label})
            if assignment is None:
                existing_after_conflict = self.assignment_rows_for_request(request_id)
                if assignment := _matches_existing(existing_after_conflict):
                    return assignment, ""
                if existing_after_conflict:
                    raise PermissionError("request_id already belongs to a different assignment")
                raise LakebaseError("assignment insert returned no row")
            audit_event_id = self._insert_audit_event(
                cur,
                actor=assigned_by,
                action="lead.assign",
                entity_type="borrower",
                entity_id=borrower_id,
                payload_json={
                    "borrower_id": borrower_id,
                    "assignment_id": assignment.assignment_id,
                    "assigned_to_email": assignment.assigned_to_email,
                    "assigned_by": assignment.assigned_by,
                    "assigned_at": assignment.assigned_at.isoformat(),
                    "expires_at": assignment.expires_at.isoformat() if assignment.expires_at else None,
                    "strategy": assignment.strategy,
                },
                event_type="LEAD_ASSIGN",
                subject_clip=subject_clip,
                request_id=request_id,
            )
        clear_sales_state_cache()
        return assignment, audit_event_id

    def distribute(
        self,
        *,
        borrower_ids: list[str],
        lo_emails: list[str],
        assigned_by: str,
        expires_in_hours: int | None,
        strategy: AssignmentStrategy,
        request_id: str | None = None,
    ) -> tuple[list[LeadAssignment], str]:
        assignees = [
            self.require_assignee_in_scope(actor=assigned_by, assigned_to_email=email)
            for email in lo_emails
        ]
        assignments: list[LeadAssignment] = []
        if not borrower_ids:
            return assignments, ""

        expected_by_borrower: dict[str, str] = {
            borrower_id: assignees[idx % len(assignees)].email
            for idx, borrower_id in enumerate(borrower_ids)
        }

        def _matches_existing(existing_rows: list[dict[str, Any]]) -> list[LeadAssignment] | None:
            existing = [
                assignment
                for row in existing_rows
                if (assignment := _assignment_from_row(row)) is not None
            ]
            if not existing:
                return None
            existing_by_borrower = {assignment.borrower_id: assignment for assignment in existing}
            if set(existing_by_borrower) != set(expected_by_borrower):
                return None
            for row in existing_rows:
                assignment = _assignment_from_row(row)
                if assignment is None:
                    return None
                if (
                    str(row.get("assignment_scope") or "single") != "distribution"
                    or assignment.assigned_by != assigned_by.lower()
                    or assignment.assigned_to_email != expected_by_borrower.get(assignment.borrower_id)
                    or assignment.strategy != strategy
                    or not _assignment_duration_matches(assignment, expires_in_hours)
                ):
                    return None
            return existing

        existing_rows = self.assignment_rows_for_request(request_id)
        if existing_rows:
            if existing := _matches_existing(existing_rows):
                return existing, ""
            raise PermissionError("request_id already belongs to a different distribution")
        # Score balancing happens before this store in the caller by ordering
        # borrower_ids. The durable state write stays deterministic.
        with self._client.transaction() as conn, conn.cursor() as cur:
            if request_id:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%(lock_key)s))",
                    {"lock_key": f"lead_assignment:{request_id}"},
                )
                existing_rows = self.assignment_rows_for_request(request_id)
                if existing_rows:
                    if existing := _matches_existing(existing_rows):
                        return existing, ""
                    raise PermissionError("request_id already belongs to a different distribution")
            for idx, borrower_id in enumerate(borrower_ids):
                assignee = assignees[idx % len(assignees)]
                cur.execute(
                    """
                    UPDATE mip_app.lead_assignments
                    SET released_at = now()
                    WHERE borrower_id = %(borrower_id)s
                      AND released_at IS NULL
                    """,
                    {"borrower_id": borrower_id},
                )
                cur.execute(
                    """
                    INSERT INTO mip_app.lead_assignments (
                        borrower_id, assigned_to_email, assigned_by,
                        expires_at, strategy, request_id, assignment_scope
                    )
                    VALUES (
                        %(borrower_id)s, %(assigned_to_email)s, %(assigned_by)s,
                        CASE
                            WHEN %(expires_in_hours)s IS NULL THEN NULL
                            ELSE now() + (%(expires_in_hours)s::int * interval '1 hour')
                        END,
                        %(strategy)s, %(request_id)s, %(assignment_scope)s
                    )
                    ON CONFLICT (request_id, borrower_id)
                        WHERE request_id IS NOT NULL
                        DO NOTHING
                    RETURNING assignment_id, borrower_id, assigned_to_email,
                              assigned_by, assigned_at, expires_at, released_at, strategy
                    """,
                    {
                        "borrower_id": borrower_id,
                        "assigned_to_email": assignee.email,
                        "assigned_by": assigned_by.lower(),
                        "expires_in_hours": expires_in_hours,
                        "strategy": strategy,
                        "request_id": request_id,
                        "assignment_scope": "distribution",
                    },
                )
                assignment = _assignment_from_row(
                    {**dict(cur.fetchone() or {}), "assigned_to_label": assignee.display_label}
                )
                if assignment is None:
                    existing_after_conflict = self.assignment_rows_for_request(request_id)
                    if existing := _matches_existing(existing_after_conflict):
                        return existing, ""
                    if existing_after_conflict:
                        raise PermissionError("request_id already belongs to a different distribution")
                    raise LakebaseError("assignment insert returned no row")
                assignments.append(assignment)
            counts = Counter(a.assigned_to_email for a in assignments)
            audit_event_id = self._insert_audit_event(
                cur,
                actor=assigned_by,
                action="lead.distribute",
                entity_type="lead_queue",
                entity_id="_sales_distribution",
                payload_json={
                    "borrower_ids": [a.borrower_id for a in assignments],
                    "assigned_count": len(assignments),
                    "lo_emails": [a.email for a in assignees],
                    "per_lo_counts": dict(counts),
                    "strategy": strategy,
                },
                event_type="LEAD_DISTRIBUTE",
                request_id=request_id,
            )
        clear_sales_state_cache()
        return assignments, audit_event_id

    def log_disposition(
        self,
        *,
        borrower_id: str,
        lo_email: str,
        actor: str,
        outcome: CallDispositionOutcome,
        occurred_at: datetime | None,
        callback_at: datetime | None,
        notes: str | None,
        subject_clip: str | None = None,
        request_id: str | None = None,
    ) -> tuple[CallDisposition, str]:
        lo = self.require_disposition_scope(actor=actor, lo_email=lo_email)
        assignment = self.active_assignment_for(borrower_id)
        if assignment is not None and assignment.assigned_to_email != lo.email:
            raise PermissionError("borrower is assigned to another loan officer")
        clean_notes = scrub_free_text(notes) if notes else None

        def _matches_existing(existing: CallDisposition) -> bool:
            occurred_matches = True if occurred_at is None else _datetimes_equal(existing.occurred_at, occurred_at)
            return (
                existing.borrower_id == borrower_id
                and existing.lo_email == lo.email
                and existing.outcome == outcome
                and existing.notes == clean_notes
                and _datetimes_equal(existing.callback_at, callback_at)
                and occurred_matches
            )

        existing = self.disposition_for_request(request_id)
        if existing is not None:
            if not _matches_existing(existing):
                raise ValueError("request_id already belongs to a different call disposition")
            return existing, existing.audit_event_id or ""

        with self._client.transaction() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(MAX(attempt_number), 0) + 1 AS next_attempt
                FROM mip_app.call_dispositions
                WHERE borrower_id = %(borrower_id)s
                """,
                {"borrower_id": borrower_id},
            )
            attempt = int((dict(cur.fetchone() or {}).get("next_attempt")) or 1)
            cur.execute(
                """
                INSERT INTO mip_app.call_dispositions (
                    borrower_id, lo_email, outcome, attempt_number,
                    occurred_at, callback_at, notes, request_id
                )
                VALUES (
                    %(borrower_id)s, %(lo_email)s, %(outcome)s, %(attempt_number)s,
                    COALESCE(%(occurred_at)s, now()), %(callback_at)s, %(notes)s, %(request_id)s
                )
                ON CONFLICT (request_id) WHERE request_id IS NOT NULL DO NOTHING
                RETURNING disposition_id, borrower_id, lo_email, outcome,
                          attempt_number, occurred_at, callback_at, notes,
                          audit_event_id
                """,
                {
                    "borrower_id": borrower_id,
                    "lo_email": lo.email,
                    "outcome": outcome,
                    "attempt_number": attempt,
                    "occurred_at": occurred_at,
                    "callback_at": callback_at,
                    "notes": clean_notes,
                    "request_id": request_id,
                },
            )
            row = dict(cur.fetchone() or {})
            disposition = _disposition_from_row(row)
            if disposition is None:
                disposition = self.disposition_for_request(request_id)
                if disposition is None:
                    raise LakebaseError("disposition insert returned no row")
                if not _matches_existing(disposition):
                    raise ValueError("request_id already belongs to a different call disposition")
                return disposition, disposition.audit_event_id or ""
            audit_event_id = self._insert_audit_event(
                cur,
                actor=actor,
                action="lead.disposition",
                entity_type="borrower",
                entity_id=borrower_id,
                payload_json={
                    "borrower_id": borrower_id,
                    "disposition_id": disposition.disposition_id,
                    "lo_email": disposition.lo_email,
                    "outcome": disposition.outcome,
                    "attempt_number": disposition.attempt_number,
                    "occurred_at": disposition.occurred_at.isoformat(),
                    "callback_at": disposition.callback_at.isoformat() if disposition.callback_at else None,
                    "notes": disposition.notes,
                },
                event_type="CALL_DISPOSITION",
                subject_clip=subject_clip,
                request_id=request_id,
            )
            cur.execute(
                """
                UPDATE mip_app.call_dispositions
                SET audit_event_id = %(audit_event_id)s::uuid
                WHERE disposition_id = %(disposition_id)s::uuid
                """,
                {"audit_event_id": audit_event_id, "disposition_id": disposition.disposition_id},
            )
        clear_sales_state_cache()
        return disposition.model_copy(update={"audit_event_id": audit_event_id}), audit_event_id

    def record_outcome(
        self,
        *,
        borrower_id: str,
        actor: str,
        outcome_type: LeadOutcomeType,
        source_system: LeadOutcomeSourceSystem,
        source_record_ref: str | None,
        assigned_to_email: str | None,
        campaign_id: str | None,
        loan_amount: int | None,
        competitor_lender_label: str | None,
        occurred_at: datetime | None,
        subject_clip: str | None = None,
        request_id: str | None = None,
    ) -> tuple[LeadOutcome, str]:
        self.require_manager_actor(actor)
        if assigned_to_email is not None:
            self.require_visible_assignee(actor=actor, assigned_to_email=assigned_to_email)

        def _matches_existing(existing: LeadOutcome) -> bool:
            return (
                existing.borrower_id == borrower_id
                and existing.outcome_type == outcome_type
                and existing.source_system == source_system
                and existing.source_record_ref == source_record_ref
                and existing.assigned_to_email == assigned_to_email
                and existing.campaign_id == campaign_id
                and existing.loan_amount == loan_amount
                and existing.competitor_lender_label == competitor_lender_label
                and (occurred_at is None or _datetimes_equal(existing.occurred_at, occurred_at))
            )

        existing = self.outcome_for_request(request_id)
        if existing is not None:
            if not _matches_existing(existing):
                raise ValueError("request_id already belongs to a different lead outcome")
            return existing, existing.audit_event_id or ""
        existing = self.outcome_for_source_record(
            source_system=source_system,
            source_record_ref=source_record_ref,
        )
        if existing is not None:
            if not _matches_existing(existing):
                raise ValueError("source_record_ref already belongs to a different lead outcome")
            return existing, existing.audit_event_id or ""

        self._require_configured_outcome_source(source_system)
        with self._client.transaction() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mip_app.lead_outcomes (
                    borrower_id, outcome_type, source_system, source_record_ref,
                    assigned_to_email, campaign_id, loan_amount,
                    competitor_lender_label, occurred_at, request_id,
                    created_by, payload_json
                )
                VALUES (
                    %(borrower_id)s, %(outcome_type)s, %(source_system)s,
                    %(source_record_ref)s, %(assigned_to_email)s,
                    %(campaign_id)s::uuid, %(loan_amount)s,
                    %(competitor_lender_label)s,
                    COALESCE(%(occurred_at)s, now()), %(request_id)s,
                    %(created_by)s, '{}'::jsonb
                )
                ON CONFLICT DO NOTHING
                RETURNING outcome_id, borrower_id, outcome_type, source_system,
                          source_record_ref, assigned_to_email, campaign_id,
                          loan_amount, competitor_lender_label, occurred_at,
                          request_id, audit_event_id, created_at
                """,
                {
                    "borrower_id": borrower_id,
                    "outcome_type": outcome_type,
                    "source_system": source_system,
                    "source_record_ref": source_record_ref,
                    "assigned_to_email": assigned_to_email,
                    "campaign_id": campaign_id,
                    "loan_amount": loan_amount,
                    "competitor_lender_label": competitor_lender_label,
                    "occurred_at": occurred_at,
                    "request_id": request_id,
                    "created_by": actor.lower(),
                },
            )
            outcome = _outcome_from_row(dict(cur.fetchone() or {}))
            if outcome is None:
                outcome = self.outcome_for_request(request_id)
                if outcome is None:
                    outcome = self.outcome_for_source_record(
                        source_system=source_system,
                        source_record_ref=source_record_ref,
                    )
                if outcome is None:
                    raise LakebaseError("lead outcome insert returned no row")
                if not _matches_existing(outcome):
                    if request_id and outcome.request_id == request_id:
                        raise ValueError("request_id already belongs to a different lead outcome")
                    raise ValueError("source_record_ref already belongs to a different lead outcome")
                return outcome, outcome.audit_event_id or ""
            audit_event_id = self._insert_audit_event(
                cur,
                actor=actor,
                action="lead.outcome",
                entity_type="borrower",
                entity_id=borrower_id,
                payload_json={
                    "borrower_id": borrower_id,
                    "lead_outcome_id": outcome.outcome_id,
                    "lead_outcome_type": outcome.outcome_type,
                    "source_system": outcome.source_system,
                    "source_record_ref": outcome.source_record_ref,
                    "assigned_to_email": outcome.assigned_to_email,
                    "campaign_id": outcome.campaign_id,
                    "loan_amount": outcome.loan_amount,
                    "competitor_lender_label": outcome.competitor_lender_label,
                    "occurred_at": outcome.occurred_at.isoformat(),
                },
                event_type="LEAD_OUTCOME",
                subject_clip=subject_clip,
                request_id=request_id,
            )
            cur.execute(
                """
                UPDATE mip_app.lead_outcomes
                SET audit_event_id = %(audit_event_id)s::uuid
                WHERE outcome_id = %(outcome_id)s::uuid
                """,
                {"audit_event_id": audit_event_id, "outcome_id": outcome.outcome_id},
            )
        clear_sales_state_cache()
        return outcome.model_copy(update={"audit_event_id": audit_event_id}), audit_event_id

    def lifecycle_for(self, borrower_id: str) -> dict[str, Any]:
        cache_key = f"sales_state:lifecycle:{borrower_id}"
        cached = _cache_get(cache_key)
        if isinstance(cached, dict):
            return deepcopy(cached)
        row = self._client.fetchone(
            """
            WITH latest_approval AS (
                SELECT approval_id, action, offer_code, decided_at
                FROM mip_app.approvals
                WHERE borrower_id = %(borrower_id)s
                ORDER BY decided_at DESC
                LIMIT 1
            ),
            latest_disposition AS (
                SELECT occurred_at AS disposition_at
                FROM mip_app.call_dispositions
                WHERE borrower_id = %(borrower_id)s
                ORDER BY occurred_at DESC, created_at DESC
                LIMIT 1
            )
            SELECT
                CASE
                    WHEN a.action = 'approve' THEN 'approved'
                    WHEN a.action = 'reject' THEN 'rejected'
                    WHEN a.action = 'hold' THEN 'hold'
                    ELSE 'pending'
                END AS approval_status,
                CASE
                    WHEN d.disposition_at IS NOT NULL THEN 'actioned'
                    WHEN a.action = 'approve' THEN 'queued'
                    ELSE 'none'
                END AS outreach_status,
                CASE WHEN a.action = 'approve' THEN a.approval_id ELSE NULL END AS approval_id,
                CASE WHEN a.action = 'approve' THEN a.decided_at ELSE NULL END AS approved_at,
                d.disposition_at AS outreach_at,
                now() AS synced_at
            FROM latest_approval a
            FULL OUTER JOIN latest_disposition d ON true
            """,
            {"borrower_id": borrower_id},
        ) or {}
        result = {
            "borrower_id": borrower_id,
            "approval_status": row.get("approval_status") or "pending",
            "outreach_status": row.get("outreach_status") or "none",
            "approval_id": str(row["approval_id"]) if row.get("approval_id") else None,
            "approved_at": row.get("approved_at"),
            "outreach_at": row.get("outreach_at"),
            "synced_at": row.get("synced_at"),
            "assignment": self.active_assignment_for(borrower_id),
            "latest_disposition": self.latest_disposition_for(borrower_id),
        }
        _cache_set(cache_key, result)
        return deepcopy(result)

    def aging(self, *, older_than_days: int, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._client.fetchall(
            """
            WITH latest_approval AS (
                SELECT DISTINCT ON (borrower_id)
                       borrower_id, action, decided_at
                FROM mip_app.approvals
                ORDER BY borrower_id, decided_at DESC
            ),
            latest_disposition AS (
                SELECT DISTINCT ON (borrower_id)
                       borrower_id, outcome, occurred_at
                FROM mip_app.call_dispositions
                ORDER BY borrower_id, occurred_at DESC, created_at DESC
            )
            SELECT a.borrower_id,
                   'approved' AS approval_status,
                   a.decided_at AS approved_at,
                   FLOOR(EXTRACT(EPOCH FROM (now() - a.decided_at)) / 86400)::int AS age_days,
                   CASE WHEN d.occurred_at IS NOT NULL THEN 'actioned' ELSE 'queued' END AS outreach_status,
                   d.occurred_at AS outreach_at,
                   la.assigned_to_email,
                   d.outcome AS latest_disposition_outcome,
                   d.occurred_at AS latest_disposition_at
            FROM latest_approval a
            LEFT JOIN mip_app.lead_assignments la
              ON la.borrower_id = a.borrower_id
             AND la.released_at IS NULL
            LEFT JOIN latest_disposition d ON d.borrower_id = a.borrower_id
            WHERE a.action = 'approve'
              AND d.occurred_at IS NULL
              AND a.decided_at <= now() - (%(older_than_days)s::int * interval '1 day')
            ORDER BY a.decided_at ASC
            LIMIT %(limit)s
            """,
            {"older_than_days": older_than_days, "limit": limit},
            limit=limit,
        )
        return rows

    def standup(self, *, date: str, visible_lo_emails: set[str] | None = None) -> dict[str, Any]:
        lo_filter = "AND lo_email = ANY(%(lo_emails)s)" if visible_lo_emails is not None else ""
        rows = self._client.fetchall(
            f"""
            SELECT lo_email, outcome, COUNT(*) AS n
            FROM mip_app.call_dispositions
            WHERE occurred_at >= %(date)s::date
              AND occurred_at < (%(date)s::date + interval '1 day')
              {lo_filter}
            GROUP BY lo_email, outcome
            ORDER BY lo_email, outcome
            """,
            {"date": date, "lo_emails": sorted(visible_lo_emails or [])},
            limit=500,
        )
        by_lo: dict[str, Counter[str]] = {}
        totals: Counter[str] = Counter()
        for row in rows:
            lo_email = str(row.get("lo_email"))
            outcome = str(row.get("outcome"))
            n = int(row.get("n") or 0)
            by_lo.setdefault(lo_email, Counter())[outcome] += n
            totals[outcome] += n
        return {
            "date": date,
            "calls_logged": sum(totals.values()),
            "contacts_reached": totals["connected"] + totals["callback_scheduled"] + totals["application_started"],
            "callbacks_scheduled": totals["callback_scheduled"],
            "applications_started": totals["application_started"],
            "dead_leads": totals["dead"],
            "by_lo": [
                {"lo_email": lo, "outcomes": dict(counts), "calls_logged": sum(counts.values())}
                for lo, counts in sorted(by_lo.items())
            ],
        }

    def conversion(
        self,
        *,
        from_date: str,
        to_date: str,
        group_by: str,
        visible_lo_emails: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        group_expr = "lo_email" if group_by == "lo" else "'all_cohorts'"
        lo_filter = "AND lo_email = ANY(%(lo_emails)s)" if visible_lo_emails is not None else ""
        rows = self._client.fetchall(
            f"""
            SELECT {group_expr} AS group_key,
                   COUNT(*) AS calls_attempted,
                   COUNT(*) FILTER (WHERE outcome IN ('connected','callback_scheduled','application_started')) AS contacts_reached,
                   COUNT(*) FILTER (WHERE outcome = 'callback_scheduled') AS callbacks_scheduled,
                   COUNT(*) FILTER (WHERE outcome = 'application_started') AS applications_started
            FROM mip_app.call_dispositions
            WHERE occurred_at >= %(from_date)s::date
              AND occurred_at < (%(to_date)s::date + interval '1 day')
              {lo_filter}
            GROUP BY 1
            ORDER BY applications_started DESC, calls_attempted DESC
            LIMIT 100
            """,
            {"from_date": from_date, "to_date": to_date, "lo_emails": sorted(visible_lo_emails or [])},
            limit=100,
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            calls = int(row.get("calls_attempted") or 0)
            apps = int(row.get("applications_started") or 0)
            out.append(
                {
                    "group_key": row.get("group_key"),
                    "calls_attempted": calls,
                    "contacts_reached": int(row.get("contacts_reached") or 0),
                    "callbacks_scheduled": int(row.get("callbacks_scheduled") or 0),
                    "applications_started": apps,
                    "application_start_rate": round(apps / calls, 4) if calls else 0.0,
                }
            )
        return out

    def outcome_summary(
        self,
        *,
        from_date: str,
        to_date: str,
        visible_lo_emails: set[str] | None = None,
    ) -> dict[str, Any]:
        lo_filter = "AND assigned_to_email = ANY(%(lo_emails)s)" if visible_lo_emails is not None else ""
        rows = self._client.fetchall(
            f"""
            SELECT outcome_type, source_system, assigned_to_email,
                   COALESCE(competitor_lender_label, '') AS competitor_lender_label,
                   COUNT(*) AS n
            FROM mip_app.lead_outcomes
            WHERE occurred_at >= %(from_date)s::date
              AND occurred_at < (%(to_date)s::date + interval '1 day')
              {lo_filter}
            GROUP BY outcome_type, source_system, assigned_to_email, competitor_lender_label
            ORDER BY n DESC
            LIMIT 1000
            """,
            {"from_date": from_date, "to_date": to_date, "lo_emails": sorted(visible_lo_emails or [])},
            limit=1000,
        )
        totals: Counter[str] = Counter()
        by_source: dict[str, Counter[str]] = {}
        by_lo: dict[str, Counter[str]] = {}
        competitors: Counter[str] = Counter()
        for row in rows:
            outcome_type = str(row.get("outcome_type") or "")
            source_system = str(row.get("source_system") or "unknown")
            lo_email = str(row.get("assigned_to_email") or "unassigned")
            competitor = _public_competitor_label(row.get("competitor_lender_label"))
            n = int(row.get("n") or 0)
            totals[outcome_type] += n
            by_source.setdefault(source_system, Counter())[outcome_type] += n
            by_lo.setdefault(lo_email, Counter())[outcome_type] += n
            if outcome_type == "lost_to_competitor" and competitor:
                competitors[competitor] += n
        source_statuses = self.outcome_source_statuses()
        return {
            "from_date": from_date,
            "to_date": to_date,
            "total_outcomes": sum(totals.values()),
            "applications_submitted": totals["application_submitted"],
            "closed_funded": totals["closed_funded"],
            "lost_to_competitor": totals["lost_to_competitor"],
            "withdrawn": totals["withdrawn"],
            "not_qualified": totals["not_qualified"],
            "by_source_system": [
                {"source_system": key, "outcomes": dict(counts), "total": sum(counts.values())}
                for key, counts in sorted(by_source.items())
            ],
            "source_statuses": [
                {
                    **status,
                    "outcome_count": sum(by_source.get(str(status["source_system"]), Counter()).values()),
                }
                for status in source_statuses
            ],
            "by_lo": [
                {"lo_email": key, "outcomes": dict(counts), "total": sum(counts.values())}
                for key, counts in sorted(by_lo.items())
            ],
            "top_competitors": [
                {"competitor_lender_label": key, "lost_to_competitor": count}
                for key, count in competitors.most_common(5)
            ],
        }


def hydrate_leads_with_sales_state(
    leads: list[LeadSummary],
    store: SalesStateStore | None = None,
    *,
    actor: str | None = None,
) -> list[LeadSummary]:
    if not leads:
        return leads
    store = store or SalesStateStore()
    visible_lo_emails: set[str] | None
    if actor is None:
        visible_lo_emails = set()
    else:
        try:
            visible_lo_emails = store.visible_lo_emails(actor=actor)
        except KeyError:
            visible_lo_emails = set()
    if visible_lo_emails == set():
        hydrated: list[LeadSummary] = []
        for lead in leads:
            update: dict[str, Any] = {}
            if lead.approved_at is not None:
                age = datetime.now(tz=lead.approved_at.tzinfo) - lead.approved_at
                update["aging_days"] = max(0, age.days)
            hydrated.append(lead.model_copy(update=update))
        return hydrated
    borrower_ids = [lead.borrower_id for lead in leads]
    assignments = store.assignments_for(borrower_ids)
    dispositions = store.latest_dispositions_for(borrower_ids)
    hydrated: list[LeadSummary] = []
    for lead in leads:
        update: dict[str, Any] = {}
        assignment = assignments.get(lead.borrower_id)
        if assignment is not None and (
            visible_lo_emails is None or assignment.assigned_to_email in visible_lo_emails
        ):
            update.update(
                {
                    "assigned_to_email": assignment.assigned_to_email,
                    "assigned_to_label": assignment.assigned_to_label,
                    "assigned_at": assignment.assigned_at,
                    "assignment_expires_at": assignment.expires_at,
                }
            )
        disposition = dispositions.get(lead.borrower_id)
        if disposition is not None and (
            visible_lo_emails is None or disposition.lo_email in visible_lo_emails
        ):
            update.update(
                {
                    "latest_disposition_outcome": disposition.outcome,
                    "latest_disposition_at": disposition.occurred_at,
                    "latest_callback_at": disposition.callback_at,
                }
            )
        if lead.approved_at is not None:
            age = datetime.now(tz=lead.approved_at.tzinfo) - lead.approved_at
            update["aging_days"] = max(0, age.days)
        hydrated.append(lead.model_copy(update=update))
    return hydrated


def new_server_request_id(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"

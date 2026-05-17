from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends

from backend.schemas.lead import LeadSummary
from backend.schemas.sales import (
    AssignmentStrategy,
    CallDisposition,
    CallDispositionOutcome,
    LeadAssignment,
    SalesTeamMember,
)
from backend.services.audit_lakebase_store import _build_insert_params
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client
from backend.services.pii_redaction import scrub_free_text

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


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


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


class SalesStateStore:
    """Lakebase-backed sales work-management state.

    UC gold remains the source for borrower facts and scoring. Lakebase is the
    authoritative app-state ledger for manager assignment and LO disposition
    actions, because those are human workflow events rather than Cotality facts.
    """

    def __init__(self, client: LakebaseClient | None = None) -> None:
        self._client = client or get_sales_lakebase()

    def list_team(self, *, actor: str | None = None) -> list[SalesTeamMember]:
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
            return members
        if actor_member.role == "sales_manager":
            return [
                member
                for member in members
                if member.email == actor_member.email or member.manager_email == actor_member.email
            ]
        return [member for member in members if member.email == actor_member.email]

    def require_active_team_member(self, email: str) -> SalesTeamMember:
        row = self._client.fetchone(
            """
            SELECT email, display_label, role, region, manager_email, capacity_per_day, active
            FROM mip_app.sales_team
            WHERE email = %(email)s AND active = true
            LIMIT 1
            """,
            {"email": email.lower()},
        )
        if row is None:
            raise KeyError(email)
        return SalesTeamMember(**row)

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
        actor_member = self.require_active_team_member(actor)
        if actor_member.role == "admin":
            return None
        if actor_member.role == "loan_officer":
            return {actor_member.email}
        members = self.list_team(actor=actor)
        return {member.email for member in members if member.role == "loan_officer"}

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
        return _assignment_from_row(
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

    def latest_disposition_for(self, borrower_id: str) -> CallDisposition | None:
        return _disposition_from_row(
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

    def assignments_for(self, borrower_ids: list[str]) -> dict[str, LeadAssignment]:
        if not borrower_ids:
            return {}
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
            {"borrower_ids": borrower_ids},
            limit=max(len(borrower_ids), 1),
        )
        out: dict[str, LeadAssignment] = {}
        for row in rows:
            borrower_id = str(row["borrower_id"])
            out.setdefault(borrower_id, _assignment_from_row(row))  # type: ignore[arg-type]
        return {k: v for k, v in out.items() if v is not None}

    def assignments_for_request(self, request_id: str | None) -> list[LeadAssignment]:
        if not request_id:
            return []
        rows = self._client.fetchall(
            """
            SELECT a.assignment_id, a.borrower_id, a.assigned_to_email,
                   t.display_label AS assigned_to_label, a.assigned_by,
                   a.assigned_at, a.expires_at, a.released_at, a.strategy
            FROM mip_app.lead_assignments a
            LEFT JOIN mip_app.sales_team t ON t.email = a.assigned_to_email
            WHERE a.request_id = %(request_id)s
              AND a.released_at IS NULL
            ORDER BY a.assigned_at ASC
            """,
            {"request_id": request_id},
            limit=500,
        )
        return [
            assignment
            for assignment in (_assignment_from_row(row) for row in rows)
            if assignment is not None
        ]

    def latest_dispositions_for(self, borrower_ids: list[str]) -> dict[str, CallDisposition]:
        if not borrower_ids:
            return {}
        rows = self._client.fetchall(
            """
            SELECT DISTINCT ON (borrower_id)
                   disposition_id, borrower_id, lo_email, outcome, attempt_number,
                   occurred_at, callback_at, notes, audit_event_id
            FROM mip_app.call_dispositions
            WHERE borrower_id = ANY(%(borrower_ids)s)
            ORDER BY borrower_id, occurred_at DESC, created_at DESC
            """,
            {"borrower_ids": borrower_ids},
            limit=max(len(borrower_ids), 1),
        )
        return {
            str(row["borrower_id"]): _disposition_from_row(row)  # type: ignore[dict-item]
            for row in rows
            if row.get("borrower_id")
        }

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
        existing = self.assignments_for_request(request_id)
        if existing:
            if (
                len(existing) == 1
                and existing[0].borrower_id == borrower_id
                and existing[0].assigned_by == assigned_by.lower()
                and existing[0].assigned_to_email == assignee.email
            ):
                return existing[0], ""
            raise PermissionError("request_id already belongs to a different assignment")
        with self._client.transaction() as conn, conn.cursor() as cur:
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
                    expires_at, strategy, request_id
                )
                VALUES (
                    %(borrower_id)s, %(assigned_to_email)s, %(assigned_by)s,
                    CASE
                        WHEN %(expires_in_hours)s IS NULL THEN NULL
                        ELSE now() + (%(expires_in_hours)s::int * interval '1 hour')
                    END,
                    %(strategy)s, %(request_id)s
                )
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
                },
            )
            row = dict(cur.fetchone() or {})
            assignment = _assignment_from_row({**row, "assigned_to_label": assignee.display_label})
            if assignment is None:
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
        existing = self.assignments_for_request(request_id)
        if existing:
            existing_borrowers = [assignment.borrower_id for assignment in existing]
            if (
                set(existing_borrowers) == set(borrower_ids)
                and all(assignment.assigned_by == assigned_by.lower() for assignment in existing)
            ):
                return existing, ""
            raise PermissionError("request_id already belongs to a different distribution")
        assignments: list[LeadAssignment] = []
        if not borrower_ids:
            return assignments, ""
        # Score balancing happens before this store in the caller by ordering
        # borrower_ids. The durable state write stays deterministic.
        with self._client.transaction() as conn, conn.cursor() as cur:
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
                        expires_at, strategy, request_id
                    )
                    VALUES (
                        %(borrower_id)s, %(assigned_to_email)s, %(assigned_by)s,
                        CASE
                            WHEN %(expires_in_hours)s IS NULL THEN NULL
                            ELSE now() + (%(expires_in_hours)s::int * interval '1 hour')
                        END,
                        %(strategy)s, %(request_id)s
                    )
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
                    },
                )
                assignment = _assignment_from_row(
                    {**dict(cur.fetchone() or {}), "assigned_to_label": assignee.display_label}
                )
                if assignment is None:
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
                raise LakebaseError("disposition insert returned no row")
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
        return disposition.model_copy(update={"audit_event_id": audit_event_id}), audit_event_id

    def lifecycle_for(self, borrower_id: str) -> dict[str, Any]:
        row = self._client.fetchone(
            """
            WITH latest_approval AS (
                SELECT action, offer_code, decided_at
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
                CASE WHEN a.action = 'approve' THEN a.decided_at ELSE NULL END AS approved_at,
                d.disposition_at AS outreach_at,
                now() AS synced_at
            FROM latest_approval a
            FULL OUTER JOIN latest_disposition d ON true
            """,
            {"borrower_id": borrower_id},
        ) or {}
        return {
            "borrower_id": borrower_id,
            "approval_status": row.get("approval_status") or "pending",
            "outreach_status": row.get("outreach_status") or "none",
            "approved_at": row.get("approved_at"),
            "outreach_at": row.get("outreach_at"),
            "synced_at": row.get("synced_at"),
            "assignment": self.active_assignment_for(borrower_id),
            "latest_disposition": self.latest_disposition_for(borrower_id),
        }

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

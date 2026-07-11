"""Lakebase-backed loan-officer entity + assignment lifecycle (S2).

The middle path: loan officers are a first-class entity with an array
coverage footprint (states + county FIPS, no geometry), joined to the
existing ``mip_app.sales_team`` roster by email for role/scope. The
lifecycle lives on the existing ``mip_app.lead_assignments`` rows:

    assigned -> contact_drafted -> approved -> actioned -> outcome_recorded

Transitions are strictly one step forward; anything else raises
:class:`IllegalStatusTransitionError`. Every assign and every transition
writes an ``action_audit`` row inside the same Lakebase transaction as
the state change -- no silent writes.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends

from backend.schemas.loan_officer import (
    ASSIGNMENT_LIFECYCLE,
    AssignmentLifecycleStatus,
    LoanOfficer,
    LoanOfficerAssignment,
)
from backend.services.audit_lakebase_store import _build_insert_params
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client
from backend.services.lakebase_bootstrap import ensure_loan_officer_lifecycle_schema
from backend.services.sales_state import clear_sales_state_cache

_AUDIT_INSERT_SQL = """
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

_ASSIGNMENT_SELECT_COLUMNS = """
    a.assignment_id, a.borrower_id, a.loan_officer_id,
    a.assigned_to_email AS loan_officer_email,
    o.display_name AS loan_officer_name,
    COALESCE(a.status, 'assigned') AS status,
    a.assigned_by, a.assigned_at, a.status_updated_at, a.released_at
"""


class IllegalStatusTransitionError(ValueError):
    """Raised when a requested lifecycle transition is not the next step."""


def legal_next_status(status: str) -> AssignmentLifecycleStatus | None:
    """Return the only legal next lifecycle stage, or None at the end."""
    try:
        index = ASSIGNMENT_LIFECYCLE.index(status)  # type: ignore[arg-type]
    except ValueError:
        return None
    if index + 1 >= len(ASSIGNMENT_LIFECYCLE):
        return None
    return ASSIGNMENT_LIFECYCLE[index + 1]


def validate_status_transition(from_status: str, to_status: str) -> None:
    """Raise :class:`IllegalStatusTransitionError` unless one step forward."""
    if from_status not in ASSIGNMENT_LIFECYCLE:
        raise IllegalStatusTransitionError(f"unknown assignment status {from_status!r}")
    if to_status not in ASSIGNMENT_LIFECYCLE:
        raise IllegalStatusTransitionError(f"unknown assignment status {to_status!r}")
    expected = legal_next_status(from_status)
    if expected is None:
        raise IllegalStatusTransitionError(
            f"assignment status {from_status!r} is terminal"
        )
    if to_status != expected:
        raise IllegalStatusTransitionError(
            f"illegal transition {from_status!r} -> {to_status!r}; next stage is {expected!r}"
        )


def _officer_from_row(row: dict[str, Any] | None) -> LoanOfficer | None:
    if not row:
        return None
    return LoanOfficer(
        loan_officer_id=str(row["loan_officer_id"]),
        email=str(row["email"]),
        display_name=str(row["display_name"]),
        coverage_states=list(row.get("coverage_states") or []),
        coverage_counties=list(row.get("coverage_counties") or []),
        active=bool(row.get("active", True)),
    )


def _assignment_from_row(row: dict[str, Any] | None) -> LoanOfficerAssignment | None:
    if not row:
        return None
    loan_officer_id = row.get("loan_officer_id")
    return LoanOfficerAssignment(
        assignment_id=str(row["assignment_id"]),
        borrower_id=str(row["borrower_id"]),
        loan_officer_id=str(loan_officer_id) if loan_officer_id is not None else None,
        loan_officer_email=str(row["loan_officer_email"]),
        loan_officer_name=row.get("loan_officer_name"),
        status=str(row.get("status") or "assigned"),  # type: ignore[arg-type]
        assigned_by=str(row["assigned_by"]),
        assigned_at=row["assigned_at"],
        status_updated_at=row.get("status_updated_at"),
        released_at=row.get("released_at"),
    )


class LoanOfficerStateStore:
    """Loan-officer roster reads and audited assignment lifecycle writes."""

    def __init__(self, client: LakebaseClient | None = None) -> None:
        self._client = client or get_lakebase_client()

    # -- roster ---------------------------------------------------------

    def list_officers(self) -> list[LoanOfficer]:
        ensure_loan_officer_lifecycle_schema(self._client)
        rows = self._client.fetchall(
            """
            SELECT loan_officer_id, email, display_name,
                   coverage_states, coverage_counties, active
            FROM mip_app.loan_officers
            WHERE active = true
            ORDER BY display_name ASC
            """,
            limit=200,
        )
        return [
            officer
            for row in rows
            if (officer := _officer_from_row(row)) is not None
        ]

    def officer_by_id(self, loan_officer_id: str) -> LoanOfficer | None:
        ensure_loan_officer_lifecycle_schema(self._client)
        return _officer_from_row(
            self._client.fetchone(
                """
                SELECT loan_officer_id, email, display_name,
                       coverage_states, coverage_counties, active
                FROM mip_app.loan_officers
                WHERE loan_officer_id = %(loan_officer_id)s
                  AND active = true
                LIMIT 1
                """,
                {"loan_officer_id": loan_officer_id},
            )
        )

    # -- actor scope ------------------------------------------------------

    def _actor_member(self, actor: str) -> dict[str, Any]:
        row = self._client.fetchone(
            """
            SELECT email, role
            FROM mip_app.sales_team
            WHERE email = %(email)s AND active = true
            LIMIT 1
            """,
            {"email": actor.lower()},
        )
        if row is None:
            # PermissionError (not KeyError) so routers map an unknown actor
            # to 403 instead of colliding with unknown-officer/unknown-
            # assignment 404/422 handling.
            raise PermissionError(f"{actor} is not an active sales team member")
        return dict(row)

    def _require_assigner(self, actor: str) -> dict[str, Any]:
        member = self._actor_member(actor)
        if member.get("role") not in {"admin", "sales_manager"}:
            raise PermissionError("assigning a loan officer requires manager or admin access")
        return member

    # -- lifecycle writes -------------------------------------------------

    def assign_lead(
        self,
        *,
        borrower_id: str,
        loan_officer_id: str,
        assigned_by: str,
        subject_clip: str | None = None,
        request_id: str | None = None,
    ) -> tuple[LoanOfficerAssignment, str]:
        ensure_loan_officer_lifecycle_schema(self._client)
        self._require_assigner(assigned_by)
        officer = self.officer_by_id(loan_officer_id)
        if officer is None:
            raise KeyError(loan_officer_id)
        if request_id:
            existing = self._assignment_for_request(request_id)
            if existing is not None:
                if (
                    existing.borrower_id == borrower_id
                    and existing.loan_officer_id == officer.loan_officer_id
                ):
                    return existing, ""
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
                    strategy, request_id, assignment_scope,
                    loan_officer_id, status
                )
                VALUES (
                    %(borrower_id)s, %(assigned_to_email)s, %(assigned_by)s,
                    %(strategy)s, %(request_id)s, %(assignment_scope)s,
                    %(loan_officer_id)s, %(status)s
                )
                ON CONFLICT (request_id)
                    WHERE request_id IS NOT NULL AND assignment_scope = 'single'
                    DO NOTHING
                RETURNING assignment_id, borrower_id, loan_officer_id,
                          assigned_to_email AS loan_officer_email,
                          status, assigned_by, assigned_at,
                          status_updated_at, released_at
                """,
                {
                    "borrower_id": borrower_id,
                    "assigned_to_email": officer.email,
                    "assigned_by": assigned_by.lower(),
                    "strategy": "manual",
                    "request_id": request_id,
                    "assignment_scope": "single",
                    "loan_officer_id": officer.loan_officer_id,
                    "status": "assigned",
                },
            )
            row = dict(cur.fetchone() or {})
            assignment = _assignment_from_row(
                {**row, "loan_officer_name": officer.display_name}
            )
            if assignment is None:
                existing = self._assignment_for_request(request_id)
                if existing is not None:
                    return existing, ""
                raise LakebaseError("loan-officer assignment insert returned no row")
            audit_event_id = self._insert_audit_event(
                cur,
                actor=assigned_by,
                action="lead.assign",
                entity_type="borrower",
                entity_id=borrower_id,
                payload_json={
                    "borrower_id": borrower_id,
                    "assignment_id": assignment.assignment_id,
                    "loan_officer_id": assignment.loan_officer_id,
                    "assigned_to_email": assignment.loan_officer_email,
                    "assigned_by": assignment.assigned_by,
                    "assigned_at": assignment.assigned_at.isoformat(),
                    "to_status": assignment.status,
                },
                event_type="LEAD_ASSIGN",
                subject_clip=subject_clip,
                request_id=request_id,
            )
        clear_sales_state_cache()
        return assignment, audit_event_id

    def transition_status(
        self,
        *,
        assignment_id: str,
        to_status: AssignmentLifecycleStatus,
        actor: str,
        request_id: str | None = None,
    ) -> tuple[LoanOfficerAssignment, str]:
        ensure_loan_officer_lifecycle_schema(self._client)
        member = self._actor_member(actor)
        current = self._assignment_by_id(assignment_id)
        if current is None:
            raise KeyError(assignment_id)
        if member.get("role") not in {"admin", "sales_manager"} and (
            str(member.get("email") or "").lower() != current.loan_officer_email.lower()
        ):
            raise PermissionError("assignment status is outside the actor scope")
        if current.released_at is not None:
            raise IllegalStatusTransitionError("assignment is released; lifecycle is closed")
        validate_status_transition(current.status, to_status)
        with self._client.transaction() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE mip_app.lead_assignments
                SET status = %(to_status)s,
                    status_updated_at = now()
                WHERE assignment_id = %(assignment_id)s
                  AND COALESCE(status, 'assigned') = %(from_status)s
                  AND released_at IS NULL
                RETURNING assignment_id, borrower_id, loan_officer_id,
                          assigned_to_email AS loan_officer_email,
                          status, assigned_by, assigned_at,
                          status_updated_at, released_at
                """,
                {
                    "assignment_id": assignment_id,
                    "from_status": current.status,
                    "to_status": to_status,
                },
            )
            row = dict(cur.fetchone() or {})
            assignment = _assignment_from_row(
                {**row, "loan_officer_name": current.loan_officer_name}
            )
            if assignment is None:
                # Optimistic guard lost a concurrent race: someone advanced
                # the row between the read and the UPDATE. Surface as an
                # illegal transition so the caller re-reads state.
                raise IllegalStatusTransitionError(
                    f"assignment {assignment_id} is no longer in status {current.status!r}"
                )
            audit_event_id = self._insert_audit_event(
                cur,
                actor=actor,
                action="lead.assignment_status",
                entity_type="borrower",
                entity_id=assignment.borrower_id,
                payload_json={
                    "borrower_id": assignment.borrower_id,
                    "assignment_id": assignment.assignment_id,
                    "loan_officer_id": assignment.loan_officer_id,
                    "assigned_to_email": assignment.loan_officer_email,
                    "from_status": current.status,
                    "to_status": assignment.status,
                },
                event_type="LEAD_ASSIGNMENT_STATUS",
                request_id=request_id,
            )
        clear_sales_state_cache()
        return assignment, audit_event_id

    # -- reads ------------------------------------------------------------

    def list_assignments(
        self,
        *,
        loan_officer_id: str | None = None,
        borrower_id: str | None = None,
        status: AssignmentLifecycleStatus | None = None,
        limit: int = 100,
    ) -> list[LoanOfficerAssignment]:
        ensure_loan_officer_lifecycle_schema(self._client)
        clauses = ["a.released_at IS NULL"]
        params: dict[str, Any] = {"limit": limit}
        if loan_officer_id:
            clauses.append("a.loan_officer_id = %(loan_officer_id)s")
            params["loan_officer_id"] = loan_officer_id
        if borrower_id:
            clauses.append("a.borrower_id = %(borrower_id)s")
            params["borrower_id"] = borrower_id
        if status:
            clauses.append("COALESCE(a.status, 'assigned') = %(status)s")
            params["status"] = status
        rows = self._client.fetchall(
            f"""
            SELECT {_ASSIGNMENT_SELECT_COLUMNS}
            FROM mip_app.lead_assignments a
            LEFT JOIN mip_app.loan_officers o ON o.loan_officer_id = a.loan_officer_id
            WHERE {" AND ".join(clauses)}
            ORDER BY a.assigned_at DESC
            LIMIT %(limit)s
            """,
            params,
            limit=limit,
        )
        return [
            assignment
            for row in rows
            if (assignment := _assignment_from_row(row)) is not None
        ]

    def _assignment_by_id(self, assignment_id: str) -> LoanOfficerAssignment | None:
        return _assignment_from_row(
            self._client.fetchone(
                f"""
                SELECT {_ASSIGNMENT_SELECT_COLUMNS}
                FROM mip_app.lead_assignments a
                LEFT JOIN mip_app.loan_officers o ON o.loan_officer_id = a.loan_officer_id
                WHERE a.assignment_id = %(assignment_id)s
                LIMIT 1
                """,
                {"assignment_id": assignment_id},
            )
        )

    def _assignment_for_request(self, request_id: str | None) -> LoanOfficerAssignment | None:
        if not request_id:
            return None
        return _assignment_from_row(
            self._client.fetchone(
                f"""
                SELECT {_ASSIGNMENT_SELECT_COLUMNS}
                FROM mip_app.lead_assignments a
                LEFT JOIN mip_app.loan_officers o ON o.loan_officer_id = a.loan_officer_id
                WHERE a.request_id = %(request_id)s
                LIMIT 1
                """,
                {"request_id": request_id},
            )
        )

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
        cur.execute(_AUDIT_INSERT_SQL, params)
        row = dict(cur.fetchone() or {})
        audit_id = row.get("audit_id")
        if audit_id is None:
            raise LakebaseError("loan-officer audit insert returned no row")
        return str(audit_id)


LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]


def get_loan_officer_state_store(client: LakebaseDep) -> LoanOfficerStateStore:
    return LoanOfficerStateStore(client)

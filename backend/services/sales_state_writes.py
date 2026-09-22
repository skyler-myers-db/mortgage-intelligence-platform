"""Write side of the sales lifecycle store: assignment, distribution, call
disposition, and outcome commits."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from backend.schemas.sales import (
    AssignmentStrategy,
    CallDisposition,
    CallDispositionOutcome,
    LeadAssignment,
    LeadOutcome,
    LeadOutcomeSourceSystem,
    LeadOutcomeType,
)
from backend.services.lakebase import LakebaseError
from backend.services.pii_redaction import scrub_free_text
from backend.services.sales_state_core import _SalesStateCore
from backend.services.sales_state_mappers import (
    _assignment_duration_matches,
    _assignment_from_row,
    _datetimes_equal,
    _disposition_from_row,
    _outcome_from_row,
    clear_sales_state_cache,
)


class _SalesStateWrites(_SalesStateCore):
    """Lakebase write paths; each commit clears the sales-state cache."""

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
            use_cache=False,
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
                    "expires_at": assignment.expires_at.isoformat()
                    if assignment.expires_at
                    else None,
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
            self.require_assignee_in_scope(
                actor=assigned_by,
                assigned_to_email=email,
                use_cache=False,
            )
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
                    or assignment.assigned_to_email
                    != expected_by_borrower.get(assignment.borrower_id)
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
                        raise PermissionError(
                            "request_id already belongs to a different distribution"
                        )
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
        lo = self.require_disposition_scope(actor=actor, lo_email=lo_email, use_cache=False)
        # 2026-08-07 platform audit F6. A disposition of ``connected`` asserts
        # that a borrower WAS CONTACTED, which is a stronger claim than
        # declining to contact them -- yet declining (``/outreach/reject``) is
        # approver-gated while this route admitted any active sales-team
        # member writing against any borrower, approved or not, assigned or
        # not. Require the same in-scope active assignment ``record_outcome``
        # already requires (``require_outcome_scope``): a contact claim needs
        # a governed routing record behind it, and because assignment itself
        # is gated on ``approval_status == 'approved'``
        # (``backend/api/sales.py::_ensure_assignable_borrower``) that
        # transitively restores "approved before contacted" as well.
        #
        # No approver allowlist here on purpose: loan officers are the people
        # who log call outcomes and are deliberately NOT approvers, so gating
        # on the approver list would make the field workflow unusable. The
        # assignment IS the authorization record for this actor + borrower.
        assignment = self.active_assignment_for(borrower_id, use_cache=False)
        if assignment is None:
            raise PermissionError("lead disposition requires an in-scope active assignment")
        if assignment.assigned_to_email != lo.email:
            raise PermissionError("borrower is assigned to another loan officer")
        clean_notes = scrub_free_text(notes) if notes else None

        def _matches_existing(existing: CallDisposition) -> bool:
            occurred_matches = (
                True if occurred_at is None else _datetimes_equal(existing.occurred_at, occurred_at)
            )
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
                    "callback_at": disposition.callback_at.isoformat()
                    if disposition.callback_at
                    else None,
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
                    raise ValueError(
                        "source_record_ref already belongs to a different lead outcome"
                    )
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

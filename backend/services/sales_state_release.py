"""Assignment release: the compensating write behind Undo (audit states-07 item 1).

An Undo never edits or deletes history. It sets ``released_at`` on the row
it undoes and, when the assign or distribution REPLACED another officer's
row, inserts a fresh row handing the lead back to that officer. assign and
distribute release the old row and insert the new one in ONE transaction,
so the replaced row is the borrower's row whose ``released_at`` equals the
released row's ``assigned_at`` (both are that transaction's ``now()``).

The restore happens only when the replaced officer is still an active loan
officer in the actor's scope and the route's ``restore_gate`` says the lead
is still approved and contactable; otherwise the release stands and the
response (and the audit row) carry a closed ``restore_skipped_reason``.

Each released borrower gets exactly one LEAD_UNASSIGN row on the borrower
entity with ``metadata.borrower_id`` (the decision-history contract). A
replay of the same ``request_id`` by the same actor re-reads and returns the
same body with the ORIGINAL audit id and writes nothing; the advisory lock on
``'lead_assignment_release:' || request_id`` serialises concurrent retries.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from backend.schemas.sales import (
    AssignmentReleaseResponse,
    DistributionReleaseResponse,
    LeadAssignment,
    RestoreSkippedReason,
)
from backend.services.lakebase import LakebaseError
from backend.services.sales_state_core import _SalesStateCore
from backend.services.sales_state_mappers import (
    _assignment_from_row,
    _datetime_utc,
    clear_sales_state_cache,
)

ReleaseConflictReason = Literal[
    "assignment_not_active",
    "assignment_started",
    "assignee_inactive",
    "request_id_conflict",
]
RestoreGate = Callable[[str], RestoreSkippedReason | None]


class ReleaseConflict(RuntimeError):
    """The release cannot run; ``reason`` is the closed 409 code."""

    def __init__(self, reason: ReleaseConflictReason) -> None:
        super().__init__(reason)
        self.reason: ReleaseConflictReason = reason


class DistributionNotFound(LookupError):
    """No distribution run carries the given request id."""


_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext(%(lock_key)s))"

_ACTIVE_ROW_FOR_UPDATE = """
SELECT assignment_id, borrower_id, assigned_to_email, assigned_by,
       assigned_at, expires_at, released_at, strategy,
       COALESCE(status, 'assigned') AS status
FROM mip_app.lead_assignments
WHERE borrower_id = %(borrower_id)s
  AND released_at IS NULL
FOR UPDATE
"""

_RELEASE_ROW = """
UPDATE mip_app.lead_assignments
SET released_at = now()
WHERE assignment_id = %(assignment_id)s::uuid
  AND released_at IS NULL
RETURNING assignment_id, borrower_id, assigned_to_email, assigned_by,
          assigned_at, expires_at, released_at, strategy,
          COALESCE(status, 'assigned') AS status
"""

_REPLACED_ROW = """
SELECT a.assignment_id, a.assigned_to_email, a.expires_at, a.strategy,
       COALESCE(a.status, 'assigned') AS status, a.loan_officer_id
FROM mip_app.lead_assignments a
WHERE a.borrower_id = %(borrower_id)s
  AND a.assignment_id <> %(assignment_id)s::uuid
  AND a.released_at = (
      SELECT r.assigned_at
      FROM mip_app.lead_assignments r
      WHERE r.assignment_id = %(assignment_id)s::uuid
  )
ORDER BY a.assigned_at DESC
LIMIT 1
"""

_RESTORE_ROW = """
INSERT INTO mip_app.lead_assignments (
    borrower_id, assigned_to_email, assigned_by, expires_at, strategy,
    request_id, assignment_scope, loan_officer_id, status
)
VALUES (
    %(borrower_id)s, %(assigned_to_email)s, %(assigned_by)s, %(expires_at)s,
    %(strategy)s, %(request_id)s, %(assignment_scope)s,
    %(loan_officer_id)s::uuid, %(status)s
)
RETURNING assignment_id, borrower_id, assigned_to_email, assigned_by,
          assigned_at, expires_at, released_at, strategy, status
"""

_RELEASE_AUDIT_ROWS = """
SELECT audit_id, actor_email, entity_id, metadata
FROM mip_app.action_audit
WHERE event_type = 'LEAD_UNASSIGN'
  AND request_id = %(request_id)s
ORDER BY audit_sequence ASC
"""

_ASSIGNMENTS_BY_ID = """
SELECT a.assignment_id, a.borrower_id, a.assigned_to_email,
       t.display_label AS assigned_to_label, a.assigned_by,
       a.assigned_at, a.expires_at, a.released_at, a.strategy,
       COALESCE(a.status, 'assigned') AS status
FROM mip_app.lead_assignments a
LEFT JOIN mip_app.sales_team t ON t.email = a.assigned_to_email
WHERE a.assignment_id::text = ANY(%(assignment_ids)s)
"""


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("metadata")
    if isinstance(raw, str):
        return dict(json.loads(raw or "{}"))
    return dict(raw or {})


class _SalesStateRelease(_SalesStateCore):
    """Assignment and distribution release (the Undo compensating writes)."""

    def _release_audit_rows(self, request_id: str) -> list[dict[str, Any]]:
        return self._client.fetchall(_RELEASE_AUDIT_ROWS, {"request_id": request_id}, limit=500)

    def _assignments_by_id(self, assignment_ids: list[str]) -> dict[str, LeadAssignment]:
        if not assignment_ids:
            return {}
        rows = self._client.fetchall(
            _ASSIGNMENTS_BY_ID, {"assignment_ids": assignment_ids}, limit=len(assignment_ids)
        )
        out: dict[str, LeadAssignment] = {}
        for row in rows:
            assignment = _assignment_from_row(row)
            if assignment is not None:
                out[assignment.assignment_id] = assignment
        return out

    def _replayed_rows(
        self, request_id: str, *, actor: str, allowed_assignment_ids: set[str]
    ) -> list[dict[str, Any]] | None:
        """This request's LEAD_UNASSIGN rows, or None; a foreign use is a conflict."""
        rows = self._release_audit_rows(request_id)
        if not rows:
            return None
        for row in rows:
            metadata = _metadata(row)
            if (
                str(row.get("actor_email") or "").lower() != actor.lower()
                or str(metadata.get("assignment_id") or "") not in allowed_assignment_ids
                or str(row.get("entity_id") or "") != str(metadata.get("borrower_id") or "")
            ):
                raise ReleaseConflict("request_id_conflict")
        return rows

    def _single_replay(
        self, request_id: str, *, actor: str, borrower_id: str, assignment_id: str
    ) -> AssignmentReleaseResponse | None:
        rows = self._replayed_rows(request_id, actor=actor, allowed_assignment_ids={assignment_id})
        if rows is None:
            return None
        if len(rows) != 1 or str(rows[0].get("entity_id")) != borrower_id:
            raise ReleaseConflict("request_id_conflict")
        metadata = _metadata(rows[0])
        restored_id = str(metadata.get("restored_assignment_id") or "")
        found = self._assignments_by_id([assignment_id, *([restored_id] if restored_id else [])])
        if assignment_id not in found:
            raise LakebaseError("released assignment row is missing")
        return AssignmentReleaseResponse(
            released=found[assignment_id],
            restored=found.get(restored_id) if restored_id else None,
            restore_skipped_reason=metadata.get("restore_skipped_reason"),
            audit_event_id=str(rows[0]["audit_id"]),
        )

    def _restore_replaced(
        self,
        cur: Any,
        *,
        actor: str,
        released: LeadAssignment,
        request_id: str,
        scope: str,
        restore_gate: RestoreGate,
    ) -> tuple[LeadAssignment | None, RestoreSkippedReason | None]:
        cur.execute(
            _REPLACED_ROW,
            {"borrower_id": released.borrower_id, "assignment_id": released.assignment_id},
        )
        replaced = cur.fetchone()
        if not replaced:
            return None, None
        try:
            member = self.require_assignee_in_scope(
                actor=actor, assigned_to_email=str(replaced["assigned_to_email"]), use_cache=False
            )
        except KeyError:
            return None, "previous_assignee_inactive"
        except PermissionError:
            return None, "previous_assignee_out_of_scope"
        gate_reason = restore_gate(released.borrower_id)
        if gate_reason is not None:
            return None, gate_reason
        expires_at = replaced.get("expires_at")
        if expires_at is not None and _datetime_utc(expires_at) <= datetime.now(UTC):
            expires_at = None
        loan_officer_id = replaced.get("loan_officer_id")
        cur.execute(
            _RESTORE_ROW,
            {
                "borrower_id": released.borrower_id,
                "assigned_to_email": member.email,
                "assigned_by": actor.lower(),
                "expires_at": expires_at,
                "strategy": replaced.get("strategy") or "manual",
                "request_id": request_id,
                "assignment_scope": scope,
                "loan_officer_id": str(loan_officer_id) if loan_officer_id else None,
                "status": replaced.get("status") or "assigned",
            },
        )
        restored = _assignment_from_row(
            {**dict(cur.fetchone() or {}), "assigned_to_label": member.display_label}
        )
        if restored is None:
            raise LakebaseError("assignment restore returned no row")
        return restored, None

    def _release_one(
        self,
        cur: Any,
        *,
        actor: str,
        active: dict[str, Any],
        request_id: str,
        scope: str,
        restore_gate: RestoreGate,
        subject_clip: str | None,
    ) -> AssignmentReleaseResponse:
        try:
            assignee = self.require_assignee_in_scope(
                actor=actor, assigned_to_email=str(active["assigned_to_email"]), use_cache=False
            )
        except KeyError as exc:
            raise ReleaseConflict("assignee_inactive") from exc
        cur.execute(_RELEASE_ROW, {"assignment_id": str(active["assignment_id"])})
        released = _assignment_from_row(
            {**dict(cur.fetchone() or {}), "assigned_to_label": assignee.display_label}
        )
        if released is None:
            raise ReleaseConflict("assignment_not_active")
        restored, skipped = self._restore_replaced(
            cur,
            actor=actor,
            released=released,
            request_id=request_id,
            scope=scope,
            restore_gate=restore_gate,
        )
        metadata: dict[str, Any] = {
            "borrower_id": released.borrower_id,
            "assignment_id": released.assignment_id,
            "assigned_to_email": released.assigned_to_email,
        }
        if restored is not None:
            metadata["restored_assignment_id"] = restored.assignment_id
            metadata["restored_to_email"] = restored.assigned_to_email
        elif skipped is not None:
            metadata["restore_skipped_reason"] = skipped
        audit_event_id = self._insert_audit_event(
            cur,
            actor=actor,
            action="lead.unassign",
            entity_type="borrower",
            entity_id=released.borrower_id,
            payload_json=metadata,
            event_type="LEAD_UNASSIGN",
            subject_clip=subject_clip,
            request_id=request_id,
        )
        return AssignmentReleaseResponse(
            released=released,
            restored=restored,
            restore_skipped_reason=skipped,
            audit_event_id=audit_event_id,
        )

    def release_assignment(
        self,
        *,
        borrower_id: str,
        assignment_id: str,
        actor: str,
        request_id: str,
        restore_gate: RestoreGate,
        subject_clip: str | None = None,
    ) -> AssignmentReleaseResponse:
        self.require_manager_actor(actor, use_cache=False)
        replay_keys = {"actor": actor, "borrower_id": borrower_id, "assignment_id": assignment_id}
        if replay := self._single_replay(request_id, **replay_keys):
            return replay
        with self._client.transaction() as conn, conn.cursor() as cur:
            cur.execute(_LOCK_SQL, {"lock_key": f"lead_assignment_release:{request_id}"})
            if replay := self._single_replay(request_id, **replay_keys):
                return replay
            cur.execute(_ACTIVE_ROW_FOR_UPDATE, {"borrower_id": borrower_id})
            active = cur.fetchone()
            if not active or str(active["assignment_id"]) != assignment_id:
                raise ReleaseConflict("assignment_not_active")
            if str(active.get("status") or "assigned") != "assigned":
                raise ReleaseConflict("assignment_started")
            result = self._release_one(
                cur,
                actor=actor,
                active=dict(active),
                request_id=request_id,
                scope="single",
                restore_gate=restore_gate,
                subject_clip=subject_clip,
            )
        clear_sales_state_cache()
        return result

    def _distribution_rows(self, distribution_request_id: str, *, actor: str) -> list[dict[str, Any]]:
        rows = [
            row
            for row in self.assignment_rows_for_request(distribution_request_id)
            if str(row.get("assignment_scope") or "single") == "distribution"
        ]
        if not rows:
            raise DistributionNotFound(distribution_request_id)
        for row in rows:
            if str(row.get("assigned_by") or "").lower() != actor.lower():
                raise PermissionError("only the actor who ran a distribution can undo it")
        for email in sorted({str(row["assigned_to_email"]) for row in rows}):
            try:
                self.require_assignee_in_scope(actor=actor, assigned_to_email=email, use_cache=False)
            except KeyError as exc:
                raise PermissionError("a distribution assignee is no longer in scope") from exc
        return rows

    def _distribution_replay(
        self, request_id: str, *, actor: str, rows: list[dict[str, Any]]
    ) -> DistributionReleaseResponse | None:
        run_ids = {str(row["assignment_id"]) for row in rows}
        audits = self._replayed_rows(request_id, actor=actor, allowed_assignment_ids=run_ids)
        if audits is None:
            return None
        metadata = [_metadata(row) for row in audits]
        released_ids = [str(m["assignment_id"]) for m in metadata]
        restored_ids = [str(m["restored_assignment_id"]) for m in metadata if m.get("restored_assignment_id")]
        found = self._assignments_by_id([*released_ids, *restored_ids])
        started = not_active = 0
        for row in rows:
            if str(row["assignment_id"]) in released_ids:
                continue
            if row.get("released_at") is None and str(row.get("status") or "assigned") != "assigned":
                started += 1
            else:
                not_active += 1
        return DistributionReleaseResponse(
            released_count=len(released_ids),
            restored_count=len(restored_ids),
            skipped_started_count=started,
            skipped_not_active_count=not_active,
            restore_skipped_count=sum(1 for m in metadata if m.get("restore_skipped_reason")),
            released=[found[i] for i in released_ids if i in found],
            restored=[found[i] for i in restored_ids if i in found],
            audit_event_ids=[str(row["audit_id"]) for row in audits],
        )

    def release_distribution(
        self,
        *,
        distribution_request_id: str,
        actor: str,
        request_id: str,
        restore_gate: RestoreGate,
    ) -> DistributionReleaseResponse:
        self.require_manager_actor(actor, use_cache=False)
        rows = self._distribution_rows(distribution_request_id, actor=actor)
        if replay := self._distribution_replay(request_id, actor=actor, rows=rows):
            return replay
        released: list[AssignmentReleaseResponse] = []
        started = not_active = 0
        with self._client.transaction() as conn, conn.cursor() as cur:
            cur.execute(_LOCK_SQL, {"lock_key": f"lead_assignment_release:{request_id}"})
            if replay := self._distribution_replay(request_id, actor=actor, rows=rows):
                return replay
            for row in sorted(rows, key=lambda r: (str(r["borrower_id"]), str(r["assignment_id"]))):
                cur.execute(_ACTIVE_ROW_FOR_UPDATE, {"borrower_id": str(row["borrower_id"])})
                active = cur.fetchone()
                if not active or str(active["assignment_id"]) != str(row["assignment_id"]):
                    not_active += 1
                    continue
                if str(active.get("status") or "assigned") != "assigned":
                    started += 1
                    continue
                released.append(
                    self._release_one(
                        cur,
                        actor=actor,
                        active=dict(active),
                        request_id=request_id,
                        scope="distribution",
                        restore_gate=restore_gate,
                        subject_clip=None,
                    )
                )
        clear_sales_state_cache()
        restored = [item.restored for item in released if item.restored is not None]
        return DistributionReleaseResponse(
            released_count=len(released),
            restored_count=len(restored),
            skipped_started_count=started,
            skipped_not_active_count=not_active,
            restore_skipped_count=sum(1 for item in released if item.restore_skipped_reason),
            released=[item.released for item in released],
            restored=restored,
            audit_event_ids=[item.audit_event_id for item in released],
        )

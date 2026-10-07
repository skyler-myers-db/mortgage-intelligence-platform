"""Snooze one item of the caller's own Follow-ups tray (wow-power-3 phase 2).

Order of checks, each before anything is written:

1. Idempotency: a row already holding ``request_id`` with the same actor,
   borrower and days answers its stored response; any other payload under
   that id is a 409.
2. Ownership: the tray's shared open-item read, scoped to (actor, borrower),
   must return an open item of any kind, else 409 ``no_open_follow_up``. That
   is routing that reaches no borrower classification, so it is not audited
   (the approver-409 analog, owner ruling O1).
3. Contactability: the tray's canonical evaluate + predicate pair on one gold
   row. A warehouse failure answers 503 and nothing is written. Withheld for
   any reason other than the contact frequency cap: 409 ``not_contactable``
   and exactly ONE ``LEAD_SNOOZE_REFUSED`` row (borrower id and the closed
   reason code only), because a snooze must never hide a suppression. A
   frequency-capped item wakes no earlier than the cap lifts.

Then ONE Lakebase transaction: close the pair's expired open row as 'timer',
supersede its unexpired open row, insert the ``LEAD_SNOOZE`` audit row (ids,
days, wake time and closed values only, never text) and the snooze row that
carries its ``audit_event_id``. A concurrent snooze of the same pair loses on
the open-pair unique index: 409 ``snooze_in_progress``. The actor's summary
cache is invalidated so the dot re-reads.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import uuid4

from backend.schemas.lead_snooze import LeadSnoozeResponse
from backend.services.audit_lakebase_store import build_audit_insert_params
from backend.services.databricks_sql import DatabricksSqlError
from backend.services.eligibility import REASON_FREQUENCY_CAP
from backend.services.lakebase import LakebaseError, get_lakebase_client
from backend.services.observability import emit
from backend.services.sales_followups import (
    ContactabilitySource,
    FollowupDependencyError,
    contact_verdict,
    invalidate_followup_summary,
)
from backend.services.sales_followups_sql import BORROWER_ITEM_SQL
from backend.services.sales_state_mappers import _SALES_AUDIT_INSERT_SQL

log = logging.getLogger(__name__)

REQUEST_ID_MISMATCH: Final = "request_id already belongs to a different snooze"
NO_OPEN_FOLLOW_UP: Final = "no_open_follow_up"
NOT_CONTACTABLE: Final = "not_contactable"
SNOOZE_IN_PROGRESS: Final = "snooze_in_progress"

SNOOZE_EVENT_TYPE: Final = "LEAD_SNOOZE"
SNOOZE_REFUSED_EVENT_TYPE: Final = "LEAD_SNOOZE_REFUSED"

_REPLAY_SQL: Final = """
SELECT s.actor_email, s.borrower_id, s.wake_at, s.wake_clamped,
       s.audit_event_id::text AS audit_event_id,
       a.metadata ->> 'snooze_days' AS snooze_days
FROM mip_app.lead_snoozes AS s
LEFT JOIN mip_app.action_audit AS a ON a.audit_id = s.audit_event_id
WHERE s.request_id = %(request_id)s
LIMIT 1
"""

_CLOSE_EXPIRED_SQL: Final = """
UPDATE mip_app.lead_snoozes
SET woken_at = wake_at, wake_reason = 'timer'
WHERE actor_email = %(actor)s
  AND borrower_id = %(borrower_id)s
  AND woken_at IS NULL
  AND wake_at <= %(now)s
"""

_SUPERSEDE_SQL: Final = """
UPDATE mip_app.lead_snoozes
SET woken_at = %(now)s, wake_reason = 'superseded'
WHERE actor_email = %(actor)s
  AND borrower_id = %(borrower_id)s
  AND woken_at IS NULL
  AND wake_at > %(now)s
"""

_INSERT_SQL: Final = """
INSERT INTO mip_app.lead_snoozes (
    snooze_id, actor_email, borrower_id, snoozed_at, wake_at, wake_clamped,
    request_id, audit_event_id
) VALUES (
    %(snooze_id)s::uuid, %(actor)s, %(borrower_id)s, %(now)s, %(wake_at)s, %(wake_clamped)s,
    %(request_id)s, %(audit_event_id)s::uuid
)
ON CONFLICT DO NOTHING
RETURNING snooze_id::text AS snooze_id
"""


class SnoozeConflict(Exception):
    """A 409 with a closed, machine-readable detail."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class _InsertLost(Exception):
    """The snooze insert hit a unique index: roll the audit row back."""


def _insert_audit(cur: Any, *, actor: str, action: str, event_type: str, borrower_id: str,
                  metadata: dict[str, Any], request_id: str) -> str:
    params = build_audit_insert_params(
        actor=actor,
        action=action,
        entity_type="borrower",
        entity_id=borrower_id,
        payload_json=metadata,
        event_type=event_type,
        request_id=request_id,
    )
    cur.execute(_SALES_AUDIT_INSERT_SQL, params)
    row = dict(cur.fetchone() or {})
    if row.get("audit_id") is None:
        raise LakebaseError("snooze audit insert returned no row")
    return str(row["audit_id"])


class LeadSnoozeService:
    def __init__(self, lakebase_factory: Callable[[], Any]) -> None:
        self._lakebase_factory = lakebase_factory

    def snooze(
        self,
        *,
        actor: str,
        borrower_id: str,
        days: int,
        request_id: str,
        repo: ContactabilitySource,
    ) -> LeadSnoozeResponse:
        try:
            lakebase = self._lakebase_factory()
            replay = self._replay(lakebase, actor, borrower_id, days, request_id)
            if replay is not None:
                return replay
            item = lakebase.fetchone(BORROWER_ITEM_SQL, {"actor": actor, "borrower_id": borrower_id})
        except LakebaseError as exc:
            raise FollowupDependencyError("lakebase") from exc
        if item is None:
            emit(log, "lead_snooze_refused", reason=NO_OPEN_FOLLOW_UP)
            raise SnoozeConflict(NO_OPEN_FOLLOW_UP)
        try:
            gold = repo.contactability([borrower_id])
        except DatabricksSqlError as exc:
            raise FollowupDependencyError("warehouse") from exc
        verdict = contact_verdict(gold.get(borrower_id))
        try:
            if not verdict.contactable and verdict.reason != REASON_FREQUENCY_CAP:
                self._refuse(lakebase, actor, borrower_id, str(verdict.reason), request_id)
                emit(log, "lead_snooze_refused", reason=NOT_CONTACTABLE, contact_block_reason=verdict.reason)
                raise SnoozeConflict(NOT_CONTACTABLE)
            now = datetime.now(UTC)
            wake_at = now + timedelta(days=days)
            earliest = verdict.earliest_recontact_at
            clamped = earliest is not None and earliest > wake_at
            if clamped and earliest is not None:
                wake_at = earliest
            response = self._write(
                lakebase,
                actor=actor,
                borrower_id=borrower_id,
                days=days,
                request_id=request_id,
                now=now,
                wake_at=wake_at,
                clamped=clamped,
                kind=str(item.get("kind")),
            )
        except LakebaseError as exc:
            raise FollowupDependencyError("lakebase") from exc
        invalidate_followup_summary(actor)
        emit(log, "lead_snooze_written", snooze_days=days, wake_clamped=response.wake_clamped)
        return response

    def _replay(self, lakebase: Any, actor: str, borrower_id: str, days: int,
                request_id: str) -> LeadSnoozeResponse | None:
        row = lakebase.fetchone(_REPLAY_SQL, {"request_id": request_id})
        if row is None:
            return None
        same = (
            str(row.get("actor_email")) == actor
            and str(row.get("borrower_id")) == borrower_id
            and str(row.get("snooze_days")) == str(days)
        )
        if not same:
            raise SnoozeConflict(REQUEST_ID_MISMATCH)
        return LeadSnoozeResponse(
            borrower_id=borrower_id,
            wake_at=row["wake_at"],
            wake_clamped=bool(row.get("wake_clamped")),
            audit_event_id=str(row.get("audit_event_id") or ""),
        )

    def _refuse(self, lakebase: Any, actor: str, borrower_id: str, reason: str,
                request_id: str) -> None:
        with lakebase.transaction() as conn, conn.cursor() as cur:
            _insert_audit(
                cur,
                actor=actor,
                action="lead.snooze_refused",
                event_type=SNOOZE_REFUSED_EVENT_TYPE,
                borrower_id=borrower_id,
                metadata={"borrower_id": borrower_id, "contact_block_reason": reason},
                request_id=request_id,
            )

    def _write(self, lakebase: Any, *, actor: str, borrower_id: str, days: int, request_id: str,
               now: datetime, wake_at: datetime, clamped: bool, kind: str) -> LeadSnoozeResponse:
        snooze_id = str(uuid4())
        pair = {"actor": actor, "borrower_id": borrower_id, "now": now}
        try:
            with lakebase.transaction() as conn, conn.cursor() as cur:
                cur.execute(_CLOSE_EXPIRED_SQL, pair)
                cur.execute(_SUPERSEDE_SQL, pair)
                audit_event_id = _insert_audit(
                    cur,
                    actor=actor,
                    action="lead.snooze",
                    event_type=SNOOZE_EVENT_TYPE,
                    borrower_id=borrower_id,
                    metadata={
                        "borrower_id": borrower_id,
                        "snooze_id": snooze_id,
                        "snooze_days": days,
                        "wake_at": wake_at.isoformat(),
                        "wake_clamped": clamped,
                        "followup_kind": kind,
                    },
                    request_id=request_id,
                )
                cur.execute(
                    _INSERT_SQL,
                    {
                        **pair,
                        "snooze_id": snooze_id,
                        "wake_at": wake_at,
                        "wake_clamped": clamped,
                        "request_id": request_id,
                        "audit_event_id": audit_event_id,
                    },
                )
                if cur.fetchone() is None:
                    raise _InsertLost()
        except _InsertLost:
            replay = self._replay(lakebase, actor, borrower_id, days, request_id)
            if replay is not None:
                return replay
            emit(log, "lead_snooze_refused", reason=SNOOZE_IN_PROGRESS)
            raise SnoozeConflict(SNOOZE_IN_PROGRESS) from None
        return LeadSnoozeResponse(
            borrower_id=borrower_id,
            wake_at=wake_at,
            wake_clamped=clamped,
            audit_event_id=audit_event_id,
        )


def get_lead_snooze_service() -> LeadSnoozeService:
    """FastAPI dependency: the Lakebase client resolves inside the call."""
    return LeadSnoozeService(get_lakebase_client)

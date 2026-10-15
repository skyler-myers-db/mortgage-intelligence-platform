"""Follow-ups due: the actor's own open work (D-shell-deviations-a; shell-09, wow-power-3).

``summary`` feeds the rail item's dot. It reads Lakebase ONLY (it never
constructs or calls a SQL-warehouse client), writes no audit row, and is
cached per actor for 30 s without stale serving; a snooze invalidates it.

``list`` feeds the tray. It reads the open items from Lakebase, then makes ONE
batched gold read and checks every row, of every kind, with the Lead Queue's
own pair: ``GoldEligibilityService.evaluate`` AND the warehouse's
``eligible_sql_predicate`` verdict on the same row. A disagreement fails
closed. An id gold no longer carries is withheld as ``not_in_coverage`` and
never dropped: loan officers keep a parallel callback sheet, and a silently
vanished callback is worse than a withheld one. Neither read writes an audit
row, resolves the audit store or bumps the identity-fallback counter.

Eligibility (the tray and the snooze share it): an active sales-team member,
or an approver. The approver check runs only after a forwarded identity is
confirmed, so its actor resolution never takes the default-actor fallback.

Logs carry exception types and counts only, never an id or an email; the
summary's cache key is the opaque actor cache key, never the email.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast

from starlette.requests import Request

from backend.schemas.sales_followups import (
    ContactBlockReason,
    FollowupItem,
    FollowupKind,
    FollowupList,
    FollowupSummary,
)
from backend.services.actor_identity import actor_cache_key, forwarded_identity
from backend.services.databricks_sql import DatabricksSqlError
from backend.services.eligibility import (
    REASON_FREQUENCY_CAP,
    REASON_SUPPRESSED,
    EligibilityDecision,
    get_eligibility_service,
)
from backend.services.lakebase import LakebaseError, get_lakebase_client
from backend.services.observability import emit
from backend.services.rbac import can_access_approver
from backend.services.repositories.databricks_borrower_contactability import (
    BorrowerContactabilityRow,
)
from backend.services.resilience_cache import TTLCache
from backend.services.sales_followups_sql import LIST_SQL, PER_KIND_CAP, SUMMARY_SQL
from backend.services.sales_state import SalesStateStore

log = logging.getLogger(__name__)

SUMMARY_TTL_S = 30.0
NOT_IN_COVERAGE: Literal["not_in_coverage"] = "not_in_coverage"
_NOT_PROVEN: Literal["eligibility_not_proven"] = "eligibility_not_proven"
_KINDS = ("callback", "approval_follow_up", "requested_follow_up", "new_assignment")

_SUMMARY_CACHE = TTLCache(max_entries=1024)
_INELIGIBLE = FollowupSummary(eligible=False, next_due_at=None)


class FollowupDependencyError(RuntimeError):
    """A dependency read failed; the router answers 503 with a safe detail."""

    def __init__(self, dependency: Literal["warehouse", "lakebase"]) -> None:
        super().__init__(dependency)
        self.dependency = dependency


class ContactabilitySource(Protocol):
    """The one repository method the tray needs (``BorrowerRepository.contactability``)."""

    def contactability(
        self, borrower_ids: Sequence[str]
    ) -> dict[str, BorrowerContactabilityRow]: ...


@dataclass(frozen=True)
class ContactVerdict:
    contactable: bool
    reason: ContactBlockReason | None
    earliest_recontact_at: datetime | None


def normalize_actor(identity: str) -> str:
    return identity.strip().lower()


def summary_cache_key(actor: str) -> str:
    """The opaque per-actor key: the cache's DEBUG events never carry the email."""
    return f"followups_summary:{actor_cache_key(actor)}"


def invalidate_followup_summary(actor: str) -> None:
    _SUMMARY_CACHE.invalidate(summary_cache_key(normalize_actor(actor)))


def _block_reason(decision: EligibilityDecision) -> ContactBlockReason:
    """The reason a row is withheld, with a cap that is the ONLY block.

    ``evaluate`` reports the frequency cap before a suppression or a missing
    marketing-eligible proof, so a borrower touched recently and then put on
    do-not-contact reads ``frequency_cap``. The tray and the snooze treat a
    cap as waitable (a snooze may hide it, the wake clamps to its end), so a
    cap that also carries a suppression or no proof is reported as that
    block instead: a cap never masks a suppression.
    """
    reason = decision.reason_code or _NOT_PROVEN
    if reason == REASON_FREQUENCY_CAP:
        if decision.suppression_reason or decision.dnc:
            return cast(ContactBlockReason, REASON_SUPPRESSED)
        if not decision.marketing_eligible:
            return _NOT_PROVEN
    return cast(ContactBlockReason, reason)


def contact_verdict(row: BorrowerContactabilityRow | None) -> ContactVerdict:
    """The canonical evaluate + predicate pair; both must allow, else fail closed."""
    if row is None:
        return ContactVerdict(False, NOT_IN_COVERAGE, None)
    decision = get_eligibility_service().evaluate(row)
    if decision.eligible and row.contactable_sql is True:
        return ContactVerdict(True, None, None)
    reason = _block_reason(decision)
    earliest = decision.earliest_recontact_at if reason == REASON_FREQUENCY_CAP else None
    return ContactVerdict(False, reason, earliest)


def actor_is_eligible(request: Request, actor: str, lakebase: Any) -> bool:
    """An active sales-team member, else an approver. Raises on a Lakebase failure."""
    try:
        SalesStateStore(lakebase).require_active_team_member(actor, use_cache=True)
    except KeyError:
        return can_access_approver(request)
    return True


class FollowupService:
    """The rail summary and the tray list for one request's actor."""

    def __init__(
        self,
        lakebase_factory: Callable[[], Any],
        *,
        cache: TTLCache | None = None,
    ) -> None:
        self._lakebase_factory = lakebase_factory
        self._cache = cache if cache is not None else _SUMMARY_CACHE

    # -- summary (Lakebase only) ---------------------------------------------
    def summary(self, request: Request) -> FollowupSummary:
        identity = forwarded_identity(request)
        actor = normalize_actor(identity) if identity else ""
        if not actor:
            return _INELIGIBLE
        try:
            summary = self._cache.get_or_set(
                summary_cache_key(actor),
                lambda: self._read_summary(request, actor),
                ttl_s=SUMMARY_TTL_S,
                stale_if_error=False,
            )
        except LakebaseError as exc:
            emit(log, "followups_summary_failed", level=logging.WARNING, exc_type=type(exc).__name__)
            raise FollowupDependencyError("lakebase") from exc
        return cast(FollowupSummary, summary).model_copy()

    def _read_summary(self, request: Request, actor: str) -> FollowupSummary:
        lakebase = self._lakebase_factory()
        if not actor_is_eligible(request, actor, lakebase):
            return _INELIGIBLE
        row = lakebase.fetchone(SUMMARY_SQL, {"actor": actor})
        return FollowupSummary(eligible=True, next_due_at=(row or {}).get("next_due_at"))

    # -- list (Lakebase, then one gold read) ---------------------------------
    def is_eligible(self, request: Request, actor: str) -> bool:
        try:
            return actor_is_eligible(request, actor, self._lakebase_factory())
        except LakebaseError as exc:
            raise FollowupDependencyError("lakebase") from exc

    def list(
        self,
        actor: str,
        *,
        window_days: int,
        repo: ContactabilitySource,
    ) -> FollowupList:
        try:
            rows = self._lakebase_factory().fetchall(
                LIST_SQL,
                {"actor": actor, "window_days": window_days},
                limit=len(_KINDS) * (PER_KIND_CAP + 1),
            )
        except LakebaseError as exc:
            emit(log, "followups_list_failed", level=logging.WARNING, dependency="lakebase",
                 exc_type=type(exc).__name__)
            raise FollowupDependencyError("lakebase") from exc
        truncated = any(int(row.get("kind_rank") or 0) > PER_KIND_CAP for row in rows)
        kept = [row for row in rows if int(row.get("kind_rank") or 0) <= PER_KIND_CAP]
        borrower_ids = list(dict.fromkeys(str(row["borrower_id"]) for row in kept))
        try:
            gold = repo.contactability(borrower_ids) if borrower_ids else {}
        except DatabricksSqlError as exc:
            emit(log, "followups_list_failed", level=logging.WARNING, dependency="warehouse",
                 exc_type=type(exc).__name__)
            raise FollowupDependencyError("warehouse") from exc
        items: list[FollowupItem] = []
        hidden = 0
        for row in kept:
            verdict = contact_verdict(gold.get(str(row["borrower_id"])))
            if row.get("snoozed") and (verdict.contactable or verdict.reason == REASON_FREQUENCY_CAP):
                # A snooze hides only what could be called (or waits on the cap);
                # it never hides a suppression, which re-surfaces as withheld.
                hidden += 1
                continue
            items.append(_item(row, verdict))
        emit(
            log,
            "followups_list_read",
            items=len(items),
            withheld=sum(1 for item in items if not item.contactable),
            hidden=hidden,
            truncated=truncated,
        )
        return FollowupList(items=items, generated_at=datetime.now(UTC), truncated=truncated)


def _item(row: dict[str, Any], verdict: ContactVerdict) -> FollowupItem:
    kind = cast(FollowupKind, row["kind"])
    return FollowupItem(
        kind=kind,
        borrower_id=str(row["borrower_id"]),
        due_at=row.get("due_at"),
        since_at=row["since_at"],
        attempt_number=row.get("attempt_number") if kind == "callback" else None,
        owner_label=row.get("owner_label") if kind == "requested_follow_up" else None,
        owner_state=row.get("owner_state") if kind == "requested_follow_up" else None,
        contactable=verdict.contactable,
        contact_block_reason=verdict.reason,
        earliest_recontact_at=verdict.earliest_recontact_at,
        woke_on_signal=bool(row.get("woke_on_signal")),
    )


def get_followup_service() -> FollowupService:
    """FastAPI dependency: the Lakebase client resolves inside the call (a 503, never a 500)."""
    return FollowupService(get_lakebase_client)

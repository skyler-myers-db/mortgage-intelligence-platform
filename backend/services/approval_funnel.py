"""S6 approval-funnel reads over live Lakebase app state.

The funnel's workflow stages are computed from the tables the approval
workflow actually writes — never from a mirror or a snapshot:

* ``approved``          — distinct borrowers with EITHER an active S2
                          assignment at-or-past ``approved`` OR a human
                          ``approve`` decision in ``mip_app.approvals``.
                          (``/outreach/approve`` is the primary approval
                          API and does not require an assignment, so the
                          union keeps both entry points honest.)
* ``actioned``          — distinct borrowers whose LATEST approval decision
                          is ``approve`` and who have at least one
                          ``mip_app.call_dispositions`` row.
* ``outcome_recorded``  — the ``actioned`` set narrowed to borrowers whose
                          active assignment reached the terminal stage
                          (the S6 outcome feedback row is written in the
                          same transaction, so the two never diverge).

2026-08-08 UX walk — "Actioned" meant two different things on adjacent
analytics tabs. The executive funnel counts gold ``borrower_lifecycle_state``
rows with ``approval_status='approved' AND outreach_status='actioned'`` (live:
3). This tab counted ``mip_app.lead_assignments`` rows at-or-past the
``actioned`` LO-assignment status (live: 0). One word, one page, two numbers.

The stage now reads the SAME concept from the Lakebase tables the gold mirror
is built from: ``jobs/sync_lifecycle_state.py`` derives
``outreach_status='actioned'`` from the existence of a ``call_dispositions``
row and ``approval_status='approved'`` from the latest ``approvals.action``.
Reading those two tables directly is the live-first source for this tab and is
exact — the gold mirror is the lagging copy, not the other way round, so any
difference is one sync behind and is disclosed in the stage's evidence source.

The LO assignment-progression concept is NOT deleted: it stays on the
per-loan-officer drill-down (:meth:`ApprovalFunnelStore.per_loan_officer`),
where every column is explicitly an ``mip_app.lead_assignments`` status. It is
no longer published as a funnel stage called "Actioned".

Reads are cached in a short-TTL cache keyed by the sales-state TTL knob
(``MIP_SALES_STATE_CACHE_TTL_S``) and invalidated by
:func:`clear_approval_funnel_cache`, which
``backend.services.sales_state.clear_sales_state_cache`` calls after
every approval / lifecycle / outcome write — a UI approval therefore
moves the funnel on the next request, well within one cache TTL.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Annotated, Any

from fastapi import Depends

from backend.config.settings import settings
from backend.schemas.funnel import (
    ApprovalFunnelStage,
    ApproverActivityRow,
    AssignmentOutcomeCounts,
    FunnelPopulation,
    LoanOfficerFunnelRow,
)
from backend.schemas.loan_officer import (
    ASSIGNMENT_LIFECYCLE,
    ASSIGNMENT_OUTCOME_EVENT_PREFIX,
    ASSIGNMENT_OUTCOMES,
)
from backend.services.lakebase import LakebaseClient, get_lakebase_client
from backend.services.lakebase_bootstrap import (
    ensure_assignment_outcome_schema,
    ensure_loan_officer_lifecycle_schema,
)
from backend.services.resilience import TTLCache

_APPROVAL_FUNNEL_CACHE = TTLCache(max_entries=64)

# Human-facing stage labels; the canonical high-opportunity KPI copy lives
# in the frontend (HIGH_OPPORTUNITY_KPI_LABEL) — this label intentionally
# avoids restating the numeric threshold.
STAGE_LABELS: dict[str, str] = {
    "population": "Marketable population",
    "high_opportunity": "High opportunity",
    "approved": "Approved",
    "actioned": "Actioned",
    "outcome_recorded": "Outcome recorded",
}

STAGE_SOURCES: dict[str, str] = {
    "approved": "mip_app.approvals + mip_app.lead_assignments",
    # Same concept as the executive funnel's Actioned stage, read from the
    # Lakebase tables the gold lifecycle mirror is synced from.
    "actioned": "mip_app.approvals + mip_app.call_dispositions",
    "outcome_recorded": (
        "mip_app.approvals + mip_app.call_dispositions + mip_app.lead_assignments"
    ),
}

_APPROVED_UNION_SQL = """
SELECT COUNT(*) AS n
FROM (
    SELECT borrower_id
    FROM mip_app.lead_assignments
    WHERE released_at IS NULL
      AND COALESCE(status, 'assigned') IN ('approved','actioned','outcome_recorded')
    UNION
    SELECT borrower_id
    FROM mip_app.approvals
    WHERE action = 'approve'
) AS approved_union
"""

# Lakebase-native twin of the executive funnel's Actioned expression
# (``approval_status='approved' AND outreach_status='actioned'`` over
# ``mip.gold.borrower_lifecycle_state``). ``latest_decision`` mirrors the
# DISTINCT ON ordering in jobs/sync_lifecycle_state.py so an approve-then-
# reject borrower drops out of the stage exactly as it does in gold.
#
# ``outcome_recorded`` is INTERSECTed with the actioned set on purpose: the
# funnel is rendered as strictly narrowing stages, so a terminal assignment
# without a recorded disposition must not reappear below a stage it is not
# in (2026-08-07 audit F5 containment contract).
_WORKFLOW_STAGE_COUNTS_SQL = """
WITH latest_decision AS (
    SELECT DISTINCT ON (borrower_id) borrower_id, action
    FROM mip_app.approvals
    ORDER BY borrower_id, decided_at DESC, approval_id::text DESC
),
approved_now AS (
    SELECT borrower_id FROM latest_decision WHERE action = 'approve'
),
actioned_borrowers AS (
    SELECT borrower_id FROM approved_now
    INTERSECT
    SELECT DISTINCT borrower_id FROM mip_app.call_dispositions
),
outcome_borrowers AS (
    SELECT borrower_id FROM actioned_borrowers
    INTERSECT
    SELECT borrower_id
    FROM mip_app.lead_assignments
    WHERE released_at IS NULL
      AND COALESCE(status, 'assigned') = 'outcome_recorded'
)
SELECT
    (SELECT COUNT(*) FROM actioned_borrowers) AS actioned,
    (SELECT COUNT(*) FROM outcome_borrowers)  AS outcome_recorded
"""

_RECENT_APPROVALS_SQL = """
SELECT approval_id, borrower_id, offer_code, actor_email,
       assigned_to_email, decided_at
FROM mip_app.approvals
WHERE action = 'approve'
ORDER BY decided_at DESC
LIMIT %(limit)s
"""

_PER_LO_STATUS_SQL = """
SELECT o.loan_officer_id, o.display_name, o.email,
       COALESCE(a.status, 'assigned') AS status,
       COUNT(a.assignment_id) AS n
FROM mip_app.loan_officers o
LEFT JOIN mip_app.lead_assignments a
  ON a.loan_officer_id = o.loan_officer_id
 AND a.released_at IS NULL
WHERE o.active = true
GROUP BY o.loan_officer_id, o.display_name, o.email, COALESCE(a.status, 'assigned')
ORDER BY o.display_name ASC
"""

_LO_OUTCOME_COUNTS_SQL = """
SELECT f.event_type, COUNT(*) AS n
FROM mip_app.feedback f
JOIN mip_app.lead_assignments a ON a.assignment_id = f.assignment_id
WHERE a.loan_officer_id = %(loan_officer_id)s
  AND f.event_type LIKE 'assignment_outcome_%%'
GROUP BY f.event_type
"""


def clear_approval_funnel_cache() -> None:
    """Drop cached funnel aggregates after any approval-workflow write."""
    _APPROVAL_FUNNEL_CACHE.clear()


def _cache_ttl_s() -> float:
    return settings.mip_sales_state_cache_ttl_s


def _cache_get(key: str) -> Any | None:
    if _cache_ttl_s() <= 0:
        return None
    return _APPROVAL_FUNNEL_CACHE.get(key)


def _cache_set(key: str, value: Any) -> None:
    ttl_s = _cache_ttl_s()
    if ttl_s > 0:
        _APPROVAL_FUNNEL_CACHE.set(key, deepcopy(value), ttl_s)


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


class ApprovalFunnelStore:
    """Live Lakebase reads for the S6 approval funnel."""

    def __init__(self, client: LakebaseClient | None = None) -> None:
        self._client = client or get_lakebase_client()

    def _ensure_schema(self) -> None:
        ensure_loan_officer_lifecycle_schema(self._client)
        ensure_assignment_outcome_schema(self._client)

    # -- workflow stage counts -------------------------------------------

    def workflow_stages(self, population: FunnelPopulation) -> list[ApprovalFunnelStage]:
        """Return the full five-stage funnel (UC population + Lakebase)."""
        cached = _cache_get("approval_funnel:workflow_counts")
        if isinstance(cached, dict):
            counts = dict(cached)
        else:
            counts = self._workflow_counts()
            _cache_set("approval_funnel:workflow_counts", counts)
        return [
            ApprovalFunnelStage(
                stage="population",
                stage_order=1,
                label=STAGE_LABELS["population"],
                borrower_count=population.population,
                source=population.source,
            ),
            ApprovalFunnelStage(
                stage="high_opportunity",
                stage_order=2,
                label=STAGE_LABELS["high_opportunity"],
                borrower_count=population.high_opportunity,
                source=population.source,
            ),
            ApprovalFunnelStage(
                stage="approved",
                stage_order=3,
                label=STAGE_LABELS["approved"],
                borrower_count=counts.get("approved", 0),
                source=STAGE_SOURCES["approved"],
            ),
            ApprovalFunnelStage(
                stage="actioned",
                stage_order=4,
                label=STAGE_LABELS["actioned"],
                borrower_count=counts.get("actioned", 0),
                source=STAGE_SOURCES["actioned"],
            ),
            ApprovalFunnelStage(
                stage="outcome_recorded",
                stage_order=5,
                label=STAGE_LABELS["outcome_recorded"],
                borrower_count=counts.get("outcome_recorded", 0),
                source=STAGE_SOURCES["outcome_recorded"],
            ),
        ]

    def _workflow_counts(self) -> dict[str, int]:
        self._ensure_schema()
        approved_row = self._client.fetchone(_APPROVED_UNION_SQL) or {}
        # actioned / outcome_recorded come from one statement whose set
        # algebra guarantees outcome_recorded ⊆ actioned ⊆ approved, so the
        # funnel narrows monotonically no matter what the tables hold.
        stage_row = self._client.fetchone(_WORKFLOW_STAGE_COUNTS_SQL) or {}
        return {
            "approved": _int(approved_row.get("n")),
            "actioned": _int(stage_row.get("actioned")),
            "outcome_recorded": _int(stage_row.get("outcome_recorded")),
        }

    # -- who approved what -------------------------------------------------

    def recent_approvals(self, limit: int = 20) -> list[ApproverActivityRow]:
        cache_key = f"approval_funnel:recent_approvals:{limit}"
        cached = _cache_get(cache_key)
        if isinstance(cached, list):
            return [row.model_copy(deep=True) for row in cached]
        self._ensure_schema()
        rows = self._client.fetchall(_RECENT_APPROVALS_SQL, {"limit": limit}, limit=limit)
        out: list[ApproverActivityRow] = []
        for row in rows:
            approval_id = row.get("approval_id")
            borrower_id = row.get("borrower_id")
            actor_email = row.get("actor_email")
            decided_at = row.get("decided_at")
            if not approval_id or not borrower_id or not actor_email or decided_at is None:
                continue
            out.append(
                ApproverActivityRow(
                    approval_id=str(approval_id),
                    borrower_id=str(borrower_id),
                    offer_code=row.get("offer_code"),
                    actor_email=str(actor_email),
                    assigned_to_email=row.get("assigned_to_email"),
                    decided_at=decided_at,
                )
            )
        _cache_set(cache_key, out)
        return out

    # -- per-loan-officer --------------------------------------------------

    def per_loan_officer(self) -> list[LoanOfficerFunnelRow]:
        cached = _cache_get("approval_funnel:per_loan_officer")
        if isinstance(cached, list):
            return [row.model_copy(deep=True) for row in cached]
        self._ensure_schema()
        rows = self._client.fetchall(_PER_LO_STATUS_SQL, limit=2000)
        by_officer: dict[str, LoanOfficerFunnelRow] = {}
        for row in rows:
            loan_officer_id = str(row.get("loan_officer_id") or "")
            if not loan_officer_id:
                continue
            officer = by_officer.get(loan_officer_id)
            if officer is None:
                officer = LoanOfficerFunnelRow(
                    loan_officer_id=loan_officer_id,
                    display_name=str(row.get("display_name") or ""),
                    email=str(row.get("email") or ""),
                )
                by_officer[loan_officer_id] = officer
            status = str(row.get("status") or "assigned")
            count = _int(row.get("n"))
            # A LEFT JOIN miss (officer with zero assignments) still emits
            # one row with n=0 for status 'assigned'; adding 0 keeps the
            # officer visible with an honest zero state.
            if status in ASSIGNMENT_LIFECYCLE and count:
                setattr(officer, status, getattr(officer, status) + count)
        out = list(by_officer.values())
        for officer in out:
            officer.total_active = (
                officer.assigned
                + officer.contact_drafted
                + officer.approved
                + officer.actioned
                + officer.outcome_recorded
            )
        _cache_set("approval_funnel:per_loan_officer", out)
        return out

    def officer_row(self, loan_officer_id: str) -> LoanOfficerFunnelRow | None:
        for officer in self.per_loan_officer():
            if officer.loan_officer_id == loan_officer_id:
                return officer
        return None

    def outcome_counts(self, loan_officer_id: str) -> AssignmentOutcomeCounts:
        cache_key = f"approval_funnel:outcomes:{loan_officer_id}"
        cached = _cache_get(cache_key)
        if isinstance(cached, AssignmentOutcomeCounts):
            return cached.model_copy(deep=True)
        self._ensure_schema()
        rows = self._client.fetchall(
            _LO_OUTCOME_COUNTS_SQL,
            {"loan_officer_id": loan_officer_id},
            limit=len(ASSIGNMENT_OUTCOMES) + 1,
        )
        counts = {outcome: 0 for outcome in ASSIGNMENT_OUTCOMES}
        for row in rows:
            event_type = str(row.get("event_type") or "")
            outcome = event_type.removeprefix(ASSIGNMENT_OUTCOME_EVENT_PREFIX)
            if outcome in counts:
                counts[outcome] = _int(row.get("n"))
        out = AssignmentOutcomeCounts(**counts)
        _cache_set(cache_key, out)
        return out


LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]


def get_approval_funnel_store(client: LakebaseDep) -> ApprovalFunnelStore:
    return ApprovalFunnelStore(client)

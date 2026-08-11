"""Leads list API.

Slice 5 adds audit emission on the ranked-list view: one
``VIEW_LEADS`` row per render, carrying the segment filter (if any)
and the list of borrower_ids the user saw. Governance §4 wants this
so we can reconstruct "which list did the approver see when they
decided to approve". No PII lands in the audit row -- borrower ids are
already masked before API egress.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response

from backend.schemas._validators_tenant import normalize_public_lender_ref
from backend.schemas.common import validate_internal_staff_email
from backend.schemas.lead import SEGMENT_CODE_VALUES, LeadSummary
from backend.schemas.lead_query import (
    DEFAULT_LEAD_LIMIT,
    MAX_LEAD_LIMIT,
    AgedDaysParam,
    ApprovalStatusParam,
    AssignedToParam,
    BorrowerIdsParam,
    CohortIdParam,
    ConsentStatusParam,
    CountiesParam,
    CountyParam,
    FunnelStageParam,
    GeographyParam,
    IncludeIdentityProofParam,
    IncludeSuppressedForAnalyticsParam,
    LenderRelationshipParam,
    LienStatusParam,
    LimitParam,
    LoanProductParam,
    MarketingEligibilityParam,
    MinEquityPctLabelParam,
    MinEquityPctParam,
    OccupancyParam,
    OriginationChannelParam,
    OutreachStatusParam,
    OwnerLinkParam,
    ProductParam,
    PurchaseIntentParam,
    RecencyParam,
    SegmentCodesParam,
    SegmentModeParam,
    StateParam,
    StatesParam,
    TargetLenderRefParam,
    ZipParam,
    ZipsParam,
)
from backend.services.audit_store import AuditStore, get_audit_store, resolve_actor
from backend.services.lakebase import LakebaseError
from backend.services.lead_cohort_replay import (
    cohort_portfolio_criteria,
    resolve_cohort_replay,
)
from backend.services.lead_query_helpers import (
    parse_borrower_ids as _parse_borrower_ids,
)
from backend.services.lead_query_helpers import (
    parse_csv_filter as _parse_csv_filter,
)
from backend.services.lead_query_helpers import (
    parse_segment_codes as _parse_segment_codes,
)
from backend.services.lead_query_helpers import (
    parse_segment_mode as _parse_segment_mode,
)
from backend.services.lead_query_helpers import (
    portfolio_criteria_from_query as _portfolio_criteria_from_query,
)
from backend.services.lead_query_helpers import (
    requires_marketing_override_admin as _requires_marketing_override_admin,
)
from backend.services.observability import emit
from backend.services.rbac import require_admin
from backend.services.repositories import LeadRepository, get_lead_repository
from backend.services.repositories.databricks_lead_cohorts import (
    GrowthAgentHandoffInvalid,
    GrowthAgentHandoffProof,
    GrowthAgentHandoffStale,
    LeadCohortFilters,
    normalise_lead_queue_handoff_filters,
    validate_growth_agent_handoff_identity,
    verify_growth_agent_handoff,
)
from backend.services.sales_state import (
    SalesStateStore,
    get_sales_state_store,
    hydrate_leads_with_sales_state,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["leads"])

# Re-exported: the limit bounds are declared next to the Query() annotation
# that enforces them, and callers (backend.services.lead_warm, the unit
# tests) have always read them from here.
__all__ = ["DEFAULT_LEAD_LIMIT", "MAX_LEAD_LIMIT", "router"]

_ALLOWED_SEGMENT_CODES: frozenset[str] = frozenset(SEGMENT_CODE_VALUES)
_ALLOWED_FUNNEL_STAGES: frozenset[str] = frozenset(
    {
        "addressable",
        "in_the_money",
        "high_opportunity",
        "offer_recommended",
        "approved",
        "actioned",
    }
)

RepoDep = Annotated[LeadRepository, Depends(get_lead_repository)]
StoreDep = Annotated[AuditStore, Depends(get_audit_store)]
SalesStateDep = Annotated[SalesStateStore, Depends(get_sales_state_store)]


def _safe_audit_write(store: AuditStore, **kwargs: object) -> None:
    """Background-task audit writer -- swallow + log every failure.

    R5-18: broaden from ``LakebaseError`` to ``Exception`` because this
    runs in BackgroundTasks and an unhandled exception is silently
    swallowed by FastAPI's runner. Emit only the exception class name
    (never ``str(exc)``, which can leak payload content) so operators
    see the pattern in structured logs without widening the PII
    surface.
    """
    try:
        store.write(**kwargs)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 -- background path must not raise
        emit(
            log,
            "audit.dropped",
            dependency="lakebase",
            exc_type=type(exc).__name__,
            outcome="error",
        )


@router.get("/leads", response_model=list[LeadSummary])
def list_leads(
    request: Request,
    response: Response,
    background: BackgroundTasks,
    repo: RepoDep,
    audit: StoreDep,
    sales_state: SalesStateDep,
    segment: str | None = None,
    segment_codes: SegmentCodesParam = None,
    segment_mode: SegmentModeParam = "any",
    portfolio_id: str | None = None,
    state: StateParam = None,
    zip_code: ZipParam = None,
    county: CountyParam = None,
    states: StatesParam = None,
    zips: ZipsParam = None,
    counties: CountiesParam = None,
    borrower_ids: BorrowerIdsParam = None,
    target_lender_ref: TargetLenderRefParam = None,
    geography: GeographyParam = None,
    occupancy: OccupancyParam = None,
    lien_status: LienStatusParam = None,
    lender_relationship: LenderRelationshipParam = None,
    product: ProductParam = None,
    loan_product: LoanProductParam = None,
    origination_channel: OriginationChannelParam = None,
    min_equity_pct_label: MinEquityPctLabelParam = None,
    min_equity_pct: MinEquityPctParam = None,
    owner_link: OwnerLinkParam = None,
    purchase_intent: PurchaseIntentParam = None,
    marketing_eligibility: MarketingEligibilityParam = "Eligible only",
    consent_status: ConsentStatusParam = None,
    recency: RecencyParam = None,
    include_suppressed_for_analytics: IncludeSuppressedForAnalyticsParam = False,
    include_identity_proof: IncludeIdentityProofParam = False,
    approval_status: ApprovalStatusParam = "any",
    outreach_status: OutreachStatusParam = "any",
    assigned_to: AssignedToParam = None,
    aged_days: AgedDaysParam = None,
    cohort_id: CohortIdParam = None,
    funnel_stage: FunnelStageParam = None,
    limit: LimitParam = DEFAULT_LEAD_LIMIT,
) -> list[LeadSummary]:
    # 2026-05-04 FIX β: plumb optional state/zip filters through to the
    # repo. The repo's geo-filtered path bypasses lead_population (which
    # has score >= 50 baked in) and queries borrower_360, so the returned
    # rows match the addressable counts the map tooltips report. Without
    # this, the previous behaviour returned the national top-N from
    # lead_population and the FE filtered client-side, producing 0 rows
    # for ZIPs whose borrowers didn't make the national top 500.
    if segment:
        segment = segment.strip().lower()
        if segment not in _ALLOWED_SEGMENT_CODES:
            raise HTTPException(status_code=422, detail="segment contains an unknown segment")
    handoff_values = request.query_params.getlist("growth_handoff")
    if len(handoff_values) > 1:
        raise HTTPException(status_code=422, detail="Growth Agent handoff proof is invalid")
    growth_handoff = handoff_values[0].strip() if handoff_values else None
    if growth_handoff and len(growth_handoff) > 4096:
        raise HTTPException(status_code=422, detail="Growth Agent handoff proof is invalid")
    effective_marketing_eligibility = marketing_eligibility
    if include_suppressed_for_analytics:
        effective_marketing_eligibility = None

    if funnel_stage and funnel_stage not in _ALLOWED_FUNNEL_STAGES:
        raise HTTPException(status_code=422, detail="funnel_stage contains an unknown stage")

    if growth_handoff and cohort_id:
        raise HTTPException(
            status_code=422,
            detail="Growth Agent handoff cannot be combined with a persisted cohort",
        )
    if not growth_handoff and _requires_marketing_override_admin(
        marketing_eligibility=effective_marketing_eligibility,
        consent_status=consent_status,
        include_suppressed_for_analytics=include_suppressed_for_analytics,
    ):
        require_admin(request)
    if include_identity_proof and not growth_handoff:
        require_admin(request)
    actor = resolve_actor(request)
    parsed_segments = _parse_segment_codes(segment_codes)
    parsed_states = _parse_csv_filter(states, width=2, label="states")
    parsed_zips = _parse_csv_filter(zips, width=5, label="zips", numeric=True)
    parsed_counties = _parse_csv_filter(counties, width=5, label="counties", numeric=True)
    parsed_borrower_ids = _parse_borrower_ids(borrower_ids)
    if growth_handoff and (assigned_to or parsed_borrower_ids):
        raise HTTPException(
            status_code=422,
            detail="Growth Agent handoff cannot contain borrower or assignee filters",
        )
    assignment_filter_ids: list[str] | None = None
    if assigned_to:
        try:
            assigned_to = validate_internal_staff_email(assigned_to)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="assigned_to must be an internal staff email") from exc
        try:
            sales_state.require_visible_assignee(actor=actor, assigned_to_email=assigned_to)
            assignment_filter_ids = sales_state.borrower_ids_for_assignee(assigned_to)
        except LakebaseError as exc:
            raise HTTPException(status_code=503, detail="Lakebase temporarily unavailable") from exc
        except KeyError as exc:
            raise HTTPException(status_code=422, detail="assigned_to must be an active loan officer") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="assigned_to is outside the actor scope") from exc
        if parsed_borrower_ids:
            allowed = set(assignment_filter_ids)
            parsed_borrower_ids = [bid for bid in parsed_borrower_ids if bid in allowed]
        else:
            parsed_borrower_ids = assignment_filter_ids
        if parsed_borrower_ids == []:
            response.headers["X-Total-Matching"] = "0"
            response.headers["X-Returned-Rows"] = "0"
            return []
    cohort_filters: dict[str, object] = {}

    cohort_has_replay_filter = False
    cohort_stated_count: int | None = None
    cohort_unreplayable: list[str] = []
    segment_mode = _parse_segment_mode(segment_mode)

    if cohort_id:
        # Cohort id is the governed source of truth. Query params are
        # useful for shareable URLs and visual chips, but they must not
        # widen a confirmed Genie cohort if the URL is edited by hand.
        replay = resolve_cohort_replay(cohort_id, actor=actor)
        cohort_filters = replay.filters
        cohort_stated_count = replay.stated_count
        cohort_unreplayable = replay.unreplayable_filters
        segment = None
        state = None
        zip_code = None
        portfolio_id = None
        geography = None
        occupancy = None
        lien_status = None
        lender_relationship = None
        product = None
        min_equity_pct_label = None
        min_equity_pct = None
        owner_link = None
        purchase_intent = None
        marketing_eligibility = "Eligible only"
        consent_status = None
        recency = None
        approval_status = "any"
        outreach_status = "any"
        assigned_to = None
        aged_days = None
        county = replay.county_fips
        target_lender_ref = replay.target_lender_ref
        segment_mode = replay.segment_mode
        parsed_segments = replay.segment_codes
        parsed_states = replay.state_codes
        parsed_zips = replay.zip_codes
        parsed_counties = replay.county_fipses
        parsed_borrower_ids = replay.borrower_ids
    try:
        target_lender_ref = normalize_public_lender_ref(target_lender_ref, allow_all=True)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="target_lender_ref must be the configured tenant lender, a public-safe Competitor alias, or All",
        ) from exc
    if target_lender_ref == "All":
        target_lender_ref = None

    if cohort_id:
        portfolio_criteria, cohort_has_replay_filter = cohort_portfolio_criteria(
            cohort_filters,
            has_replay_filter=any(
                (
                    parsed_states,
                    parsed_zips,
                    parsed_counties,
                    parsed_borrower_ids,
                    county,
                    parsed_segments,
                    target_lender_ref,
                )
            ),
        )
    else:
        try:
            portfolio_criteria = _portfolio_criteria_from_query(
                geography=geography,
                occupancy=occupancy,
                lien_status=lien_status,
                lender_relationship=lender_relationship,
                product=product,
                target_lender_ref=target_lender_ref,
                loan_product=loan_product,
                origination_channel=origination_channel,
                min_equity_pct_label=min_equity_pct_label,
                min_equity_pct=min_equity_pct,
                owner_link=owner_link,
                purchase_intent=purchase_intent,
                marketing_eligibility=effective_marketing_eligibility,
                consent_status=consent_status,
                recency=recency,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    repo_kwargs: dict[str, object] = {}
    if portfolio_criteria is not None:
        repo_kwargs["portfolio_criteria"] = portfolio_criteria
    if parsed_counties:
        repo_kwargs["county_fipses"] = parsed_counties

    if cohort_id and not cohort_has_replay_filter:
        raise HTTPException(
            status_code=422,
            detail="cohort has no replayable lead filters",
        )

    repository_args: dict[str, object] = {
        "segment": segment,
        "portfolio_id": portfolio_id,
        "state": state,
        "zip_code": zip_code,
        "county_fips": county,
        "state_codes": parsed_states,
        "zip_codes": parsed_zips,
        "borrower_ids": parsed_borrower_ids,
        "segment_codes": parsed_segments,
        "segment_mode": segment_mode,
        "target_lender_ref": target_lender_ref,
        "cohort_id": cohort_id,
        "funnel_stage": funnel_stage,
        "approval_status": None if approval_status == "any" else approval_status,
        "outreach_status": None if outreach_status == "any" else outreach_status,
        "aged_days": aged_days,
        **repo_kwargs,
    }
    handoff_proof: GrowthAgentHandoffProof | None = None
    normalized_handoff_filters: dict[str, object] | None = None
    if growth_handoff:
        try:
            normalized_handoff_filters = normalise_lead_queue_handoff_filters(
                LeadCohortFilters(
                    segment=segment,
                    state=state,
                    zip_code=zip_code,
                    county_fips=county,
                    county_fipses=parsed_counties,
                    state_codes=parsed_states,
                    zip_codes=parsed_zips,
                    borrower_ids=parsed_borrower_ids,
                    segment_codes=parsed_segments,
                    segment_mode=segment_mode,
                    target_lender_ref=target_lender_ref,
                    funnel_stage=funnel_stage,
                    portfolio_criteria=portfolio_criteria,
                    approval_status=None if approval_status == "any" else approval_status,
                    outreach_status=None if outreach_status == "any" else outreach_status,
                    aged_days=aged_days,
                )
            )
            handoff_proof = verify_growth_agent_handoff(
                growth_handoff,
                actor=actor,
                normalized_filters=normalized_handoff_filters,
            )
        except GrowthAgentHandoffStale as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (GrowthAgentHandoffInvalid, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=503,
                detail="Growth Agent handoff verification is unavailable",
            ) from exc
    identity: dict[str, str | int] | None = None
    if include_identity_proof or handoff_proof is not None:
        list_with_identity = getattr(repo, "list_with_identity", None)
        if not callable(list_with_identity):
            raise HTTPException(
                status_code=503,
                detail="Lead Queue cohort identity proof is unavailable",
            )
        try:
            leads, identity = list_with_identity(limit=limit, **repository_args)
        except ValueError as exc:
            raise HTTPException(
                status_code=503,
                detail="Lead Queue cohort identity proof is incomplete",
            ) from exc
        if handoff_proof is not None:
            try:
                validate_growth_agent_handoff_identity(handoff_proof, identity)
            except GrowthAgentHandoffStale as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        total_matching = int(identity.get("total") or 0)
    else:
        leads = repo.list(limit=limit, **repository_args)
        count_fn = getattr(repo, "count", None)
        # Test-local repositories and external connectors may implement only
        # the list contract. Production reports the complete cohort count.
        total_matching = count_fn(**repository_args) if callable(count_fn) else len(leads)

    try:
        leads = hydrate_leads_with_sales_state(leads, sales_state, actor=actor)
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail="Lakebase temporarily unavailable") from exc
    response.headers["X-Total-Matching"] = str(total_matching)
    response.headers["X-Returned-Rows"] = str(len(leads))
    if cohort_id and cohort_stated_count is not None:
        # What the Genie answer said, next to what this queue actually matched.
        # The queue replays only the reviewed geography/segment subset, so an
        # answer narrowed by any numeric threshold replays broader — measured
        # live 2026-08-10 at 55x (32 borrowers -> 1,766). The UI compares these
        # two and says so rather than presenting a different population under
        # the same question.
        response.headers["X-Cohort-Stated-Count"] = str(cohort_stated_count)
        if cohort_stated_count != total_matching:
            response.headers["X-Cohort-Count-Delta"] = str(total_matching - cohort_stated_count)
        if cohort_unreplayable:
            response.headers["X-Cohort-Unreplayable-Filters"] = ",".join(
                cohort_unreplayable[:12]
            )
    if identity is not None and "ranked_total" in identity:
        # Geo-filtered reads report the geography population as the total
        # (map-tile promise); this header carries the ranked subset
        # (score >= 50) so the UI can state both truthfully (audit C4).
        response.headers["X-Ranked-Matching"] = str(identity["ranked_total"])
    if identity is not None:
        response.headers["X-Cohort-Snapshot-ID"] = str(identity["snapshot_id"])
        if include_identity_proof:
            response.headers["X-Cohort-Digest"] = str(identity["cohort_digest"])
    if handoff_proof is not None:
        response.headers["X-Cohort-Fingerprint"] = handoff_proof.cohort_fingerprint
        response.headers["X-Growth-Agent-Run-ID"] = handoff_proof.run_id
    # When the result set hit the requested cap, advertise the truncation
    # explicitly so the frontend can tell "exactly N" vs "N and there's
    # more you didn't see". We can't distinguish at this layer between
    # "exactly N rows exist" and "more than N exist"; the header is a
    # conservative signal ("capped at") and the UI phrases it that way.
    if len(leads) >= limit:
        response.headers["X-Truncated-At"] = str(limit)
    audit_payload: dict[str, object] = {
        "rendered_borrower_ids": [lead.borrower_id for lead in leads],
        "portfolio_id": portfolio_id,
        "segment": segment,
        "limit": limit,
    }
    if parsed_segments:
        audit_payload["segment_codes"] = parsed_segments
        audit_payload["segment_mode"] = segment_mode
    if state:
        audit_payload["state"] = state.upper()
    if zip_code:
        audit_payload["zip"] = zip_code
    if county:
        audit_payload["county"] = county
    if parsed_states:
        audit_payload["states"] = parsed_states
    if parsed_zips:
        audit_payload["zips"] = parsed_zips
    if parsed_counties:
        audit_payload["counties"] = parsed_counties
    if parsed_borrower_ids:
        audit_payload["borrower_ids"] = parsed_borrower_ids
    if target_lender_ref:
        audit_payload["target_lender_ref"] = target_lender_ref
    if cohort_id:
        audit_payload["cohort_id"] = cohort_id
    if handoff_proof is not None:
        audit_payload.update(
            {
                "growth_agent_run_id": handoff_proof.run_id,
                "growth_agent_filters_fingerprint": handoff_proof.filters_fingerprint,
                "growth_agent_cohort_fingerprint": handoff_proof.cohort_fingerprint,
                "growth_agent_source_snapshot": handoff_proof.source_snapshot,
                "tool_result_hash": handoff_proof.tool_result_hash,
            }
        )
    if funnel_stage:
        audit_payload["funnel_stage"] = funnel_stage
    if approval_status != "any":
        audit_payload["approval_status"] = approval_status
    if outreach_status != "any":
        audit_payload["outreach_status"] = outreach_status
    if assigned_to:
        audit_payload["assigned_to_email"] = assigned_to
    if aged_days is not None:
        audit_payload["aged_days"] = aged_days
    portfolio_payload = portfolio_criteria.model_dump(exclude_none=True) if portfolio_criteria else {}
    if portfolio_payload:
        audit_payload["portfolio_criteria"] = portfolio_payload
    background.add_task(
        _safe_audit_write,
        audit,
        actor=actor,
        action="view_leads_ranked",
        entity_type="lead_queue",
        entity_id=segment or (",".join(parsed_segments) if parsed_segments else "_all"),
        payload_json=audit_payload,
        event_type="VIEW_LEADS",
        subject_segment=segment or (",".join(parsed_segments) if parsed_segments else None),
    )
    return leads

"""Resolve Lead Queue filter parameters into one authorized repository query.

Every Lead Queue read -- the ranked list (``GET /leads``) and the audit-free
aggregates beside it -- must apply the SAME authorization preamble before it
touches SQL: the admin gate on suppressed rows and identity proofs, the
assignee-visibility check, the governed Genie cohort replay (which overrides a
hand-edited URL) and, for the list only, the signed Growth Agent handoff. The
preamble used to live inline in ``list_leads``; a second endpoint would have
had to copy it, and a copy that forgot the cohort override or the admin gate
would widen a cohort or expose suppressed rows. ``resolve_lead_query`` is that
preamble, moved verbatim, and ``view_leads_audit_payload`` is the VIEW_LEADS
payload the list writes from its result.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from fastapi import HTTPException, Request

from backend.schemas._validators_tenant import normalize_public_lender_ref
from backend.schemas.common import validate_internal_staff_email
from backend.schemas.genie_geo_filters import GENIE_CITY_FILTER_KEY
from backend.schemas.lead import SEGMENT_CODE_VALUES, LeadSummary
from backend.schemas.lead_facets import LeadFacetDimension
from backend.schemas.lead_query import LeadQueryParams
from backend.schemas.portfolio import PortfolioCriteria
from backend.services.audit_store import resolve_actor
from backend.services.lakebase import LakebaseError
from backend.services.lead_cohort_replay import (
    cohort_portfolio_criteria,
    resolve_cohort_replay,
)
from backend.services.lead_query_helpers import (
    apply_cohort_equity_floor as _apply_cohort_equity_floor,
)
from backend.services.lead_query_helpers import (
    parse_borrower_ids as _parse_borrower_ids,
)
from backend.services.lead_query_helpers import (
    parse_city_states as _parse_city_states,
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
from backend.services.rbac import require_admin
from backend.services.repositories.databricks_lead_cohorts import (
    GrowthAgentHandoffInvalid,
    GrowthAgentHandoffProof,
    GrowthAgentHandoffStale,
    LeadCohortFilters,
    normalise_lead_queue_handoff_filters,
    verify_growth_agent_handoff,
)
from backend.services.sales_state import SalesStateStore

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


# Fixed 422 copy for the public score / spread bounds. Never echoes a value.
BOUNDS_WITH_COHORT_DETAIL = "A Genie cohort sets its own thresholds; remove the score or spread bounds"
BOUNDS_WITH_HANDOFF_DETAIL = "Growth Agent handoff cannot contain score or spread bounds"
_INVERTED_BOUND_DETAIL = {
    "opportunity_score": "min_opportunity_score must not exceed max_opportunity_score",
    "rate_spread_bps": "min_rate_spread_bps must not exceed max_rate_spread_bps",
}


def _check_public_bounds(bounds: dict[str, int], *, cohort_id: str | None, handoff: str | None) -> None:
    """Refuse an inverted pair, and any bound beside a cohort or a handoff.

    A persisted Genie cohort replays its own reviewed floors and a signed
    Growth Agent handoff is bound to the filters it was issued for: a bound
    beside either would narrow a governed population behind its proof, so it
    fails visibly instead of being dropped.
    """

    for dimension, detail in _INVERTED_BOUND_DETAIL.items():
        low = bounds.get(f"min_{dimension}")
        high = bounds.get(f"max_{dimension}")
        if low is not None and high is not None and low > high:
            raise HTTPException(status_code=422, detail=detail)
    if bounds and cohort_id:
        raise HTTPException(status_code=422, detail=BOUNDS_WITH_COHORT_DETAIL)
    if bounds and handoff:
        raise HTTPException(status_code=422, detail=BOUNDS_WITH_HANDOFF_DETAIL)


@dataclass(frozen=True)
class ResolvedLeadQuery:
    """One authorized Lead Queue query, ready for the repository.

    ``repository_args`` is the exact keyword set every repository read takes
    (list, count, identity), so the rows and the totals can never apply
    different filters. The other fields are what the VIEW_LEADS payload and
    the response headers report. ``assignment_empty`` means the assignee
    filter matched no borrower this actor may see: the caller answers an empty
    result WITHOUT reading, and the remaining fields keep their defaults.
    """

    actor: str
    assignment_empty: bool = False
    repository_args: dict[str, object] = field(default_factory=dict)
    include_identity_proof: bool = False
    handoff_proof: GrowthAgentHandoffProof | None = None
    segment: str | None = None
    portfolio_id: str | None = None
    state: str | None = None
    zip_code: str | None = None
    county: str | None = None
    segment_codes: list[str] | None = None
    segment_mode: str = "any"
    state_codes: list[str] | None = None
    zip_codes: list[str] | None = None
    city_states: list[str] | None = None
    county_fipses: list[str] | None = None
    borrower_ids: list[str] | None = None
    target_lender_ref: str | None = None
    cohort_id: str | None = None
    cohort_stated_count: int | None = None
    cohort_unreplayable: list[str] = field(default_factory=list)
    # The EFFECTIVE floors (a cohort's, or the public bound) and the public
    # ceilings, exactly as the repository applies them.
    min_opportunity_score: int | None = None
    min_rate_spread_bps: int | None = None
    max_opportunity_score: int | None = None
    max_rate_spread_bps: int | None = None
    funnel_stage: str | None = None
    approval_status: str = "any"
    outreach_status: str = "any"
    assigned_to: str | None = None
    aged_days: int | None = None
    portfolio_criteria: PortfolioCriteria | None = None


def resolve_lead_query(
    request: Request,
    sales_state: SalesStateStore,
    params: LeadQueryParams,
    *,
    growth_handoff: Sequence[str] | None,
    verify_handoff: bool = True,
) -> ResolvedLeadQuery:
    """Validate, authorize and replay ``params`` into one repository query.

    ``growth_handoff`` is every raw ``growth_handoff`` query value the
    request carried (``request.query_params.getlist``), or None for a read
    that never honours a handoff (the aggregates): a signed handoff binds
    the ranked rows to one agent run, which a count or a facet has no rows
    to bind. ``verify_handoff=False`` (a Lead Queue page past 0, whose signed
    view cursor already binds this exact handoff) keeps every gate the
    handoff's presence decides and skips only its re-verification.
    """

    segment = params.segment
    segment_codes = params.segment_codes
    segment_mode: str = params.segment_mode
    portfolio_id = params.portfolio_id
    state = params.state
    zip_code = params.zip_code
    county = params.county
    states = params.states
    zips = params.zips
    counties = params.counties
    cities = params.cities
    borrower_ids = params.borrower_ids
    target_lender_ref = params.target_lender_ref
    geography = params.geography
    occupancy = params.occupancy
    lien_status = params.lien_status
    lender_relationship = params.lender_relationship
    product = params.product
    loan_product = params.loan_product
    origination_channel = params.origination_channel
    min_equity_pct_label = params.min_equity_pct_label
    min_equity_pct = params.min_equity_pct
    owner_link = params.owner_link
    purchase_intent = params.purchase_intent
    marketing_eligibility = params.marketing_eligibility
    consent_status = params.consent_status
    recency = params.recency
    include_suppressed_for_analytics = params.include_suppressed_for_analytics
    include_identity_proof = params.include_identity_proof
    approval_status: str = params.approval_status
    outreach_status: str = params.outreach_status
    assigned_to = params.assigned_to
    aged_days = params.aged_days
    cohort_id = params.cohort_id
    funnel_stage: str | None = params.funnel_stage

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
    handoff_values = list(growth_handoff or ())
    if len(handoff_values) > 1:
        raise HTTPException(status_code=422, detail="Growth Agent handoff proof is invalid")
    handoff = handoff_values[0].strip() if handoff_values else None
    if handoff and len(handoff) > 4096:
        raise HTTPException(status_code=422, detail="Growth Agent handoff proof is invalid")
    effective_marketing_eligibility: str | None = marketing_eligibility
    if include_suppressed_for_analytics:
        effective_marketing_eligibility = None

    if funnel_stage and funnel_stage not in _ALLOWED_FUNNEL_STAGES:
        raise HTTPException(status_code=422, detail="funnel_stage contains an unknown stage")

    if handoff and cohort_id:
        raise HTTPException(
            status_code=422,
            detail="Growth Agent handoff cannot be combined with a persisted cohort",
        )
    public_bounds = params.public_bounds()
    _check_public_bounds(public_bounds, cohort_id=cohort_id, handoff=handoff)
    if not handoff and _requires_marketing_override_admin(
        marketing_eligibility=effective_marketing_eligibility,
        consent_status=consent_status,
        include_suppressed_for_analytics=include_suppressed_for_analytics,
    ):
        require_admin(request)
    if include_identity_proof and not handoff:
        require_admin(request)
    actor = resolve_actor(request)
    parsed_segments = _parse_segment_codes(segment_codes)
    parsed_states = _parse_csv_filter(states, width=2, label="states")
    parsed_zips = _parse_csv_filter(zips, width=5, label="zips", numeric=True)
    parsed_counties = _parse_csv_filter(counties, width=5, label="counties", numeric=True)
    # Pairs, not a CSV of names: `parse_csv_filter`'s isalpha()/width check
    # rejects every multi-word city and every `CITY~ST` token.
    parsed_cities = _parse_city_states(cities)
    parsed_borrower_ids = _parse_borrower_ids(borrower_ids)
    if handoff and (assigned_to or parsed_borrower_ids):
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
            # A validated assignee always resolves to a list (None only for a
            # None email), so the fallback is unreachable.
            allowed = set(assignment_filter_ids or ())
            parsed_borrower_ids = [bid for bid in parsed_borrower_ids if bid in allowed]
        else:
            parsed_borrower_ids = assignment_filter_ids
        if parsed_borrower_ids == []:
            return ResolvedLeadQuery(
                actor=actor,
                assignment_empty=True,
                segment=segment,
                portfolio_id=portfolio_id,
                assigned_to=assigned_to,
            )
    cohort_filters: dict[str, object] = {}

    cohort_has_replay_filter = False
    cohort_stated_count: int | None = None
    cohort_unreplayable: list[str] = []
    # Reviewed numeric floors carried by a governed Genie cohort. Without them
    # a score-narrowed answer of 32 replayed as 1,766 (live 2026-08-11).
    cohort_narrowing_floors: tuple[bool, ...] = ()
    cohort_min_opportunity_score: int | None = None
    cohort_min_rate_spread_bps: int | None = None
    cohort_min_equity_pct: int | None = None
    segment_mode = _parse_segment_mode(segment_mode)

    if cohort_id:
        # Cohort id is the governed source of truth. Query params are
        # useful for shareable URLs and visual chips, but they must not
        # widen a confirmed Genie cohort if the URL is edited by hand.
        replay = resolve_cohort_replay(cohort_id, actor=actor)
        cohort_filters = replay.filters
        cohort_stated_count = replay.stated_count
        cohort_unreplayable = replay.header_unreplayable_filters
        cohort_narrowing_floors = replay.narrowing_floors
        cohort_min_opportunity_score = replay.min_opportunity_score
        cohort_min_rate_spread_bps = replay.min_rate_spread_bps
        cohort_min_equity_pct = replay.min_equity_pct
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
        parsed_cities = replay.city_states
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

    portfolio_criteria: PortfolioCriteria | None
    if cohort_id:
        portfolio_criteria, cohort_has_replay_filter = cohort_portfolio_criteria(
            cohort_filters,
            has_replay_filter=any(
                (
                    parsed_states,
                    parsed_zips,
                    parsed_counties,
                    parsed_cities,
                    parsed_borrower_ids,
                    county,
                    parsed_segments,
                    target_lender_ref,
                    # Only floors that compile to a predicate count -- see
                    # CohortReplay.narrowing_floors for why a zero score or
                    # equity floor must not open the whole book.
                    *cohort_narrowing_floors,
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

    if cohort_id and portfolio_criteria is not None:
        # The equity floor rides the reviewed Portfolio vocabulary, which
        # already compiles `equity_pct >= :equity_floor`.
        portfolio_criteria = _apply_cohort_equity_floor(portfolio_criteria, cohort_min_equity_pct)

    repo_kwargs: dict[str, object] = {}
    if portfolio_criteria is not None:
        repo_kwargs["portfolio_criteria"] = portfolio_criteria
    if parsed_counties:
        repo_kwargs["county_fipses"] = parsed_counties
    # Only pass the floors when a governed cohort set them: repositories and
    # test doubles that predate this contract keep their existing signature.
    if cohort_min_opportunity_score is not None:
        repo_kwargs["min_opportunity_score"] = cohort_min_opportunity_score
    if cohort_min_rate_spread_bps is not None:
        repo_kwargs["min_rate_spread_bps"] = cohort_min_rate_spread_bps
    # Public bounds never meet a cohort (refused above), so a public floor is
    # the effective floor whenever it is set. Each is passed only when set.
    repo_kwargs.update(public_bounds)

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
        "city_states": parsed_cities,
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
    if handoff and verify_handoff:
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
                    city_states=parsed_cities,
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
                handoff,
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
    return ResolvedLeadQuery(
        actor=actor,
        repository_args=repository_args,
        include_identity_proof=include_identity_proof,
        handoff_proof=handoff_proof,
        segment=segment,
        portfolio_id=portfolio_id,
        state=state,
        zip_code=zip_code,
        county=county,
        segment_codes=parsed_segments,
        segment_mode=segment_mode,
        state_codes=parsed_states,
        zip_codes=parsed_zips,
        city_states=parsed_cities,
        county_fipses=parsed_counties,
        borrower_ids=parsed_borrower_ids,
        target_lender_ref=target_lender_ref,
        cohort_id=cohort_id,
        cohort_stated_count=cohort_stated_count,
        cohort_unreplayable=cohort_unreplayable,
        min_opportunity_score=public_bounds.get("min_opportunity_score", cohort_min_opportunity_score),
        min_rate_spread_bps=public_bounds.get("min_rate_spread_bps", cohort_min_rate_spread_bps),
        max_opportunity_score=public_bounds.get("max_opportunity_score"),
        max_rate_spread_bps=public_bounds.get("max_rate_spread_bps"),
        funnel_stage=funnel_stage,
        approval_status=approval_status,
        outreach_status=outreach_status,
        assigned_to=assigned_to,
        aged_days=aged_days,
        portfolio_criteria=portfolio_criteria,
    )


def without_facet_dimension(params: LeadQueryParams, dimension: LeadFacetDimension) -> LeadQueryParams:
    """Drop the facet dimension's own filter, keep every other one.

    A menu counts its options with every OTHER filter applied: counted with
    its own filter, the STATE menu would show the chosen state and a zero
    for every other one.
    """

    if dimension == "state":
        return replace(params, state=None, states=None)
    if dimension == "segment":
        return replace(params, segment=None, segment_codes=None, segment_mode="any")
    if dimension == "product":
        return replace(params, product=None)
    return replace(params, approval_status="any")


def view_leads_audit_payload(
    resolved: ResolvedLeadQuery,
    leads: list[LeadSummary],
    *,
    limit: int,
) -> dict[str, object]:
    """The VIEW_LEADS payload: which list the actor saw, and under which filters."""

    handoff_proof = resolved.handoff_proof
    audit_payload: dict[str, object] = {
        "rendered_borrower_ids": [lead.borrower_id for lead in leads],
        "portfolio_id": resolved.portfolio_id,
        "segment": resolved.segment,
        "limit": limit,
    }
    if resolved.segment_codes:
        audit_payload["segment_codes"] = resolved.segment_codes
        audit_payload["segment_mode"] = resolved.segment_mode
    if resolved.state:
        audit_payload["state"] = resolved.state.upper()
    if resolved.zip_code:
        audit_payload["zip"] = resolved.zip_code
    if resolved.county:
        audit_payload["county"] = resolved.county
    if resolved.state_codes:
        audit_payload["states"] = resolved.state_codes
    if resolved.zip_codes:
        audit_payload["zips"] = resolved.zip_codes
    if resolved.city_states:
        audit_payload[GENIE_CITY_FILTER_KEY] = resolved.city_states
    if resolved.county_fipses:
        audit_payload["counties"] = resolved.county_fipses
    if resolved.borrower_ids:
        audit_payload["borrower_ids"] = resolved.borrower_ids
    if resolved.target_lender_ref:
        audit_payload["target_lender_ref"] = resolved.target_lender_ref
    if resolved.cohort_id:
        audit_payload["cohort_id"] = resolved.cohort_id
    if resolved.min_opportunity_score is not None:
        audit_payload["min_opportunity_score"] = resolved.min_opportunity_score
    if resolved.min_rate_spread_bps is not None:
        audit_payload["min_rate_spread_bps"] = resolved.min_rate_spread_bps
    if resolved.max_opportunity_score is not None:
        audit_payload["max_opportunity_score"] = resolved.max_opportunity_score
    if resolved.max_rate_spread_bps is not None:
        audit_payload["max_rate_spread_bps"] = resolved.max_rate_spread_bps
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
    if resolved.funnel_stage:
        audit_payload["funnel_stage"] = resolved.funnel_stage
    if resolved.approval_status != "any":
        audit_payload["approval_status"] = resolved.approval_status
    if resolved.outreach_status != "any":
        audit_payload["outreach_status"] = resolved.outreach_status
    if resolved.assigned_to:
        audit_payload["assigned_to_email"] = resolved.assigned_to
    if resolved.aged_days is not None:
        audit_payload["aged_days"] = resolved.aged_days
    portfolio_criteria = resolved.portfolio_criteria
    portfolio_payload = portfolio_criteria.model_dump(exclude_none=True) if portfolio_criteria else {}
    if portfolio_payload:
        audit_payload["portfolio_criteria"] = portfolio_payload
    return audit_payload

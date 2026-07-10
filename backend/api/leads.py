"""Leads list API.

Slice 5 adds audit emission on the ranked-list view: one
``VIEW_LEADS`` row per render, carrying the segment filter (if any)
and the list of borrower_ids the user saw. Governance §4 wants this
so we can reconstruct "which list did the approver see when they
decided to approve". No PII lands in the audit row -- borrower ids are
already masked before API egress.
"""
from __future__ import annotations

import json
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response

from backend.schemas._validators import normalize_public_lender_ref
from backend.schemas.common import validate_internal_staff_email, validate_public_borrower_id
from backend.schemas.lead import SEGMENT_CODE_VALUES, LeadSummary
from backend.schemas.portfolio import PortfolioCriteria
from backend.services.audit_store import AuditStore, get_audit_store, resolve_actor
from backend.services.lakebase import LakebaseError, get_lakebase_client
from backend.services.observability import emit
from backend.services.rbac import require_admin
from backend.services.repositories import LeadRepository, get_lead_repository
from backend.services.sales_state import (
    SalesStateStore,
    get_sales_state_store,
    hydrate_leads_with_sales_state,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["leads"])

# Kept in sync with DatabricksLeadRepository.{DEFAULT_LIMIT, MAX_LIMIT}.
# Exposed as module-level constants so the router's Query() annotations
# and the unit tests can both read one source of truth.
DEFAULT_LEAD_LIMIT: int = 500
MAX_LEAD_LIMIT: int = 5000
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

_COHORT_FILTER_SELECT_SQL = """
SELECT route_filters
FROM mip_app.genie_cohorts
WHERE cohort_id = %(cohort_id)s
  AND actor_email = %(actor_email)s
LIMIT 1
"""

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


def _parse_csv_filter(
    raw: str | None,
    *,
    width: int,
    label: str,
    numeric: bool = False,
) -> list[str] | None:
    if raw is None:
        return None
    values = [part.strip().upper() for part in raw.split(",") if part.strip()]
    if not values:
        return None
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        valid_chars = value.isdigit() if numeric else value.isalpha()
        if len(value) != width or not valid_chars:
            raise HTTPException(
                status_code=422,
                detail=f"{label} must be comma-separated {width}-character values",
            )
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _parse_segment_codes(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    out: list[str] = []
    for part in raw.split(","):
        code = part.strip().lower()
        if not code:
            continue
        if code not in _ALLOWED_SEGMENT_CODES:
            raise HTTPException(status_code=422, detail="segment_codes contains an unknown segment")
        if code not in out:
            out.append(code)
    return out or None


def _parse_segment_mode(raw: str) -> Literal["any", "all"]:
    mode = raw.strip().lower()
    if mode not in {"any", "all"}:
        raise HTTPException(status_code=422, detail="segment_mode must be any or all")
    return mode


def _parse_borrower_ids(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    out: list[str] = []
    for value in raw.split(","):
        borrower_id = value.strip()
        if not borrower_id:
            continue
        try:
            borrower_id = validate_public_borrower_id(borrower_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="borrower_ids must be comma-separated synthetic B-* ids",
            ) from exc
        if borrower_id not in out:
            out.append(borrower_id)
    return out or None


def _cohort_list(
    filters: dict[str, object],
    key: str,
    *,
    width: int | None = None,
    numeric: bool = False,
    borrower_ids: bool = False,
) -> list[str] | None:
    raw = filters.get(key)
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise HTTPException(status_code=422, detail=f"cohort {key} filter is invalid")
    out: list[str] = []
    for value in raw:
        text = str(value).strip()
        if not text:
            continue
        if borrower_ids:
            try:
                normalized = validate_public_borrower_id(text)
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail="cohort borrower_ids filter is invalid",
                ) from exc
        else:
            normalized = text.upper()
            if width is not None:
                valid_chars = normalized.isdigit() if numeric else normalized.isalpha()
                if len(normalized) != width or not valid_chars:
                    raise HTTPException(
                        status_code=422,
                        detail=f"cohort {key} filter is invalid",
                    )
        if normalized not in out:
            out.append(normalized)
    return out or None


def _cohort_segment_mode(filters: dict[str, object]) -> str:
    raw = filters.get("segment_mode")
    if raw is None:
        return "any"
    if not isinstance(raw, str):
        raise HTTPException(status_code=422, detail="cohort segment_mode filter is invalid")
    normalized = raw.strip().lower()
    if normalized not in {"any", "all"}:
        raise HTTPException(status_code=422, detail="cohort segment_mode filter is invalid")
    return normalized


def _load_cohort_filters(cohort_id: str, *, actor: str) -> dict[str, object]:
    try:
        row = get_lakebase_client().fetchone(
            _COHORT_FILTER_SELECT_SQL,
            {"cohort_id": cohort_id, "actor_email": actor},
        )
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail="Lakebase temporarily unavailable") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="cohort not found")
    filters_raw = row.get("route_filters") or {}
    if isinstance(filters_raw, str):
        try:
            filters_raw = json.loads(filters_raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="cohort route filters are invalid") from exc
    if not isinstance(filters_raw, dict):
        raise HTTPException(status_code=422, detail="cohort route filters are invalid")
    return filters_raw


def _portfolio_criteria_from_query(
    *,
    geography: str | None,
    occupancy: str | None,
    lien_status: str | None,
    lender_relationship: str | None,
    product: str | None,
    target_lender_ref: str | None,
    min_equity_pct_label: str | None,
    min_equity_pct: float | None,
    owner_link: str | None = None,
    purchase_intent: str | None = None,
    marketing_eligibility: str | None = None,
    consent_status: str | None = None,
    recency: str | None = None,
) -> PortfolioCriteria | None:
    fields: dict[str, object] = {}
    if geography:
        fields["geography"] = geography
    if occupancy:
        fields["occupancy"] = occupancy
    if lien_status:
        fields["lien_status"] = lien_status
    if lender_relationship:
        fields["lender_relationship"] = lender_relationship
    if product:
        fields["product"] = product
    if target_lender_ref:
        fields["target_lender_ref"] = target_lender_ref
    if min_equity_pct_label:
        fields["min_equity_pct_label"] = min_equity_pct_label
    if min_equity_pct is not None:
        fields["min_equity_pct"] = min_equity_pct
    if owner_link:
        fields["owner_link"] = owner_link
    if purchase_intent:
        fields["purchase_intent"] = purchase_intent
    if marketing_eligibility:
        fields["marketing_eligibility"] = marketing_eligibility
    if consent_status:
        fields["consent_status"] = consent_status
    if recency:
        fields["recency"] = recency
    if not fields:
        return None
    return PortfolioCriteria(**fields)


def _requires_marketing_override_admin(
    *,
    marketing_eligibility: str | None,
    consent_status: str | None,
    include_suppressed_for_analytics: bool = False,
) -> bool:
    """Return true when a lead-list request may expose suppressed rows."""

    def normalize(value: str | None) -> str:
        return (value or "").strip().lower().replace("_", " ").replace("-", " ")

    if include_suppressed_for_analytics:
        return True
    normalized_marketing = normalize(marketing_eligibility or "Eligible only")
    normalized_consent = normalize(consent_status)
    if normalized_marketing and normalized_marketing not in {"eligible only", "eligible"}:
        return True
    return normalized_consent in {"opt out", "unknown"}


@router.get("/leads", response_model=list[LeadSummary])
def list_leads(
    request: Request,
    response: Response,
    background: BackgroundTasks,
    repo: RepoDep,
    audit: StoreDep,
    sales_state: SalesStateDep,
    segment: str | None = None,
    segment_codes: Annotated[
        str | None,
        Query(
            alias="segment_codes",
            description=(
                "Optional comma-separated SegmentCode list for multi-card "
                "filters. Use when more than one segment card is active."
            ),
        ),
    ] = None,
    segment_mode: Annotated[
        str,
        Query(
            description=(
                "any = segment arrays overlap; all = borrower contains every "
                "selected segment code."
            ),
        ),
    ] = "any",
    portfolio_id: str | None = None,
    state: Annotated[
        str | None,
        Query(
            min_length=2,
            max_length=2,
            pattern=r"^[A-Za-z]{2}$",
            description=(
                "Optional 2-char USPS state code. When present, the repo "
                "queries borrower_360 directly (no score floor) so the "
                "returned rows match the per-state map count."
            ),
        ),
    ] = None,
    zip_code: Annotated[
        str | None,
        Query(
            alias="zip",
            min_length=5,
            max_length=5,
            pattern=r"^\d{5}$",
            description=(
                "Optional 5-char ZIP. Same borrower_360 query path as state. "
                "Use with state for the most narrow filter."
            ),
        ),
    ] = None,
    county: Annotated[
        str | None,
        Query(
            alias="county",
            min_length=5,
            max_length=5,
            pattern=r"^\d{5}$",
            description=(
                "Optional 5-char county FIPS. Same borrower_360 query path "
                "as state/zip so map drill-downs preserve the counted cohort."
            ),
        ),
    ] = None,
    states: Annotated[
        str | None,
        Query(
            alias="states",
            max_length=256,
            description=(
                "Optional comma-separated USPS states for Genie-generated "
                "cohort actions."
            ),
        ),
    ] = None,
    zips: Annotated[
        str | None,
        Query(
            alias="zips",
            max_length=4096,
            description=(
                "Optional comma-separated 5-digit ZIP list for Genie-generated "
                "cohort actions."
            ),
        ),
    ] = None,
    counties: Annotated[
        str | None,
        Query(
            alias="counties",
            max_length=4096,
            description=(
                "Optional comma-separated 5-digit county FIPS list for "
                "Genie-generated cohort actions."
            ),
        ),
    ] = None,
    borrower_ids: Annotated[
        str | None,
        Query(
            alias="borrower_ids",
            max_length=8192,
            description=(
                "Optional comma-separated synthetic borrower IDs for Genie "
                "borrower-list cohort actions."
            ),
        ),
    ] = None,
    target_lender_ref: Annotated[
        str | None,
        Query(
            alias="target_lender_ref",
            max_length=64,
            description="Optional public-demo-safe current-lender ref such as the configured tenant lender or Competitor A.",
        ),
    ] = None,
    geography: Annotated[
        str | None,
        Query(
            alias="geography",
            max_length=64,
            description="Optional Portfolio Builder geography label to replay the built population.",
        ),
    ] = None,
    occupancy: Annotated[
        str | None,
        Query(
            alias="occupancy",
            max_length=64,
            description="Optional Portfolio Builder occupancy filter.",
        ),
    ] = None,
    lien_status: Annotated[
        str | None,
        Query(
            alias="lien_status",
            max_length=64,
            description="Optional Portfolio Builder lien-status filter.",
        ),
    ] = None,
    lender_relationship: Annotated[
        str | None,
        Query(
            alias="lender_relationship",
            max_length=64,
            description="Optional Portfolio Builder lender-relationship filter.",
        ),
    ] = None,
    product: Annotated[
        str | None,
        Query(
            alias="product",
            max_length=64,
            description="Optional Portfolio Builder product filter.",
        ),
    ] = None,
    min_equity_pct_label: Annotated[
        str | None,
        Query(
            alias="min_equity_pct_label",
            max_length=32,
            description="Optional Portfolio Builder display equity threshold.",
        ),
    ] = None,
    min_equity_pct: Annotated[
        float | None,
        Query(
            alias="min_equity_pct",
            ge=0,
            le=100,
            description="Optional numeric Portfolio Builder equity threshold.",
        ),
    ] = None,
    owner_link: Annotated[
        str | None,
        Query(
            alias="owner_link",
            max_length=64,
            description="Optional owner-link bucket from Segment Intelligence.",
        ),
    ] = None,
    purchase_intent: Annotated[
        str | None,
        Query(
            alias="purchase_intent",
            max_length=64,
            description="Optional purchase-intent bucket from Segment Intelligence.",
        ),
    ] = None,
    marketing_eligibility: Annotated[
        str,
        Query(
            alias="marketing_eligibility",
            max_length=32,
            description="Contactability gate. Defaults to Eligible only for fail-closed campaign/export use.",
        ),
    ] = "Eligible only",
    consent_status: Annotated[
        str | None,
        Query(
            alias="consent_status",
            max_length=32,
            description="Optional consent filter: Opt-in, Opt-out, Unknown, Any.",
        ),
    ] = None,
    recency: Annotated[
        str | None,
        Query(
            alias="recency",
            max_length=32,
            description="Optional touch-recency filter: Untouched 30d/60d/90d or Any.",
        ),
    ] = None,
    include_suppressed_for_analytics: Annotated[
        bool,
        Query(
            alias="include_suppressed_for_analytics",
            description=(
                "Admin-only analytics override. When true, clears the default "
                "Eligible only marketing gate so suppressed/non-opt-in rows can "
                "be counted or inspected without making them campaign-actionable."
            ),
        ),
    ] = False,
    approval_status: Annotated[
        Literal["pending", "approved", "rejected", "hold", "any"],
        Query(alias="approval_status", description="Sales workflow approval state filter."),
    ] = "any",
    outreach_status: Annotated[
        Literal["none", "queued", "actioned", "sent", "bounced", "replied", "any"],
        Query(alias="outreach_status", description="Sales workflow outreach state filter."),
    ] = "any",
    assigned_to: Annotated[
        str | None,
        Query(alias="assigned_to", max_length=256, description="Internal LO email assigned to the lead."),
    ] = None,
    aged_days: Annotated[
        int | None,
        Query(alias="aged_days", ge=1, le=90, description="Only approved leads aged at least this many days with no outreach."),
    ] = None,
    cohort_id: Annotated[
        str | None,
        Query(
            alias="cohort_id",
            max_length=64,
            description="Optional Lakebase persisted cohort id produced by a governed Genie action.",
        ),
    ] = None,
    funnel_stage: Annotated[
        Literal[
            "addressable",
            "in_the_money",
            "high_opportunity",
            "offer_recommended",
            "approved",
            "actioned",
        ] | None,
        Query(
            alias="funnel_stage",
            description=(
                "Exact native-analytics Lead Funnel drilldown. When present, "
                "the repository applies the same gold.borrower_360 predicate "
                "used by the funnel snapshot so X-Total-Matching equals the "
                "clicked stage count."
            ),
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_LEAD_LIMIT,
            description=(
                "Maximum leads to return. Defaults to 500; max 5000. When the "
                "resultset hits this cap the response sets `X-Truncated-At` "
                "so the UI can render 'Showing N — refine filters'."
            ),
        ),
    ] = DEFAULT_LEAD_LIMIT,
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
    effective_marketing_eligibility = marketing_eligibility
    if include_suppressed_for_analytics:
        effective_marketing_eligibility = None

    if funnel_stage and funnel_stage not in _ALLOWED_FUNNEL_STAGES:
        raise HTTPException(status_code=422, detail="funnel_stage contains an unknown stage")

    if _requires_marketing_override_admin(
        marketing_eligibility=effective_marketing_eligibility,
        consent_status=consent_status,
        include_suppressed_for_analytics=include_suppressed_for_analytics,
    ):
        require_admin(request)
    actor = resolve_actor(request)
    parsed_segments = _parse_segment_codes(segment_codes)
    parsed_states = _parse_csv_filter(states, width=2, label="states")
    parsed_zips = _parse_csv_filter(zips, width=5, label="zips", numeric=True)
    parsed_counties = _parse_csv_filter(counties, width=5, label="counties", numeric=True)
    parsed_borrower_ids = _parse_borrower_ids(borrower_ids)
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
    segment_mode = _parse_segment_mode(segment_mode)

    if cohort_id:
        # Cohort id is the governed source of truth. Query params are
        # useful for shareable URLs and visual chips, but they must not
        # widen a confirmed Genie cohort if the URL is edited by hand.
        cohort_filters = _load_cohort_filters(cohort_id, actor=actor)
        segment = None
        state = None
        zip_code = None
        county = None
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
        target_lender_ref = None
        parsed_segments = None
        segment_mode = _cohort_segment_mode(cohort_filters)
        parsed_states = _cohort_list(cohort_filters, "states", width=2)
        parsed_zips = _cohort_list(cohort_filters, "zips", width=5, numeric=True)
        parsed_counties = _cohort_list(cohort_filters, "counties", width=5, numeric=True)
        parsed_borrower_ids = _cohort_list(cohort_filters, "borrower_ids", borrower_ids=True)
        cohort_county = str(cohort_filters.get("county") or "").strip()
        if cohort_county:
            if not cohort_county.isdigit() or len(cohort_county) != 5:
                raise HTTPException(status_code=422, detail="cohort county filter is invalid")
            county = cohort_county
        cohort_segments = _cohort_list(cohort_filters, "segment_codes")
        if cohort_segments is not None:
            parsed_segments = _parse_segment_codes(",".join(cohort_segments))
        cohort_target = str(cohort_filters.get("target_lender_ref") or "").strip()
        if cohort_target:
            target_lender_ref = cohort_target
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
        portfolio_raw = cohort_filters.get("portfolio_criteria")
        cohort_has_replay_filter = any(
            (
                parsed_states,
                parsed_zips,
                parsed_counties,
                parsed_borrower_ids,
                county,
                parsed_segments,
                target_lender_ref,
            )
        )
        if isinstance(portfolio_raw, dict):
            try:
                original_criteria = PortfolioCriteria(**portfolio_raw)
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail="cohort portfolio criteria are invalid",
                ) from exc
            if not original_criteria.has_effective_predicate(count_default_marketing=False):
                raise HTTPException(
                    status_code=422,
                    detail="cohort portfolio criteria are invalid",
                )
            cohort_has_replay_filter = True
            safe_portfolio_raw = dict(portfolio_raw)
            safe_portfolio_raw["marketing_eligibility"] = "Eligible only"
            safe_portfolio_raw.pop("consent_status", None)
            portfolio_criteria = PortfolioCriteria(**safe_portfolio_raw)
        elif portfolio_raw is not None:
            raise HTTPException(
                status_code=422,
                detail="cohort portfolio criteria are invalid",
            )
        else:
            portfolio_criteria = PortfolioCriteria(marketing_eligibility="Eligible only")
    else:
        try:
            portfolio_criteria = _portfolio_criteria_from_query(
                geography=geography,
                occupancy=occupancy,
                lien_status=lien_status,
                lender_relationship=lender_relationship,
                product=product,
                target_lender_ref=target_lender_ref,
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

    leads = repo.list(
        segment=segment,
        portfolio_id=portfolio_id,
        limit=limit,
        state=state,
        zip_code=zip_code,
        county_fips=county,
        state_codes=parsed_states,
        zip_codes=parsed_zips,
        borrower_ids=parsed_borrower_ids,
        segment_codes=parsed_segments,
        segment_mode=segment_mode,
        target_lender_ref=target_lender_ref,
        cohort_id=cohort_id,
        funnel_stage=funnel_stage,
        approval_status=None if approval_status == "any" else approval_status,
        outreach_status=None if outreach_status == "any" else outreach_status,
        aged_days=aged_days,
        **repo_kwargs,
    )
    try:
        leads = hydrate_leads_with_sales_state(leads, sales_state, actor=actor)
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail="Lakebase temporarily unavailable") from exc
    count_fn = getattr(repo, "count", None)
    if callable(count_fn):
        total_matching = count_fn(
            segment=segment,
            portfolio_id=portfolio_id,
            state=state,
            zip_code=zip_code,
            county_fips=county,
            state_codes=parsed_states,
            zip_codes=parsed_zips,
            borrower_ids=parsed_borrower_ids,
            segment_codes=parsed_segments,
            segment_mode=segment_mode,
            target_lender_ref=target_lender_ref,
            cohort_id=cohort_id,
            funnel_stage=funnel_stage,
            approval_status=None if approval_status == "any" else approval_status,
            outreach_status=None if outreach_status == "any" else outreach_status,
            aged_days=aged_days,
            **repo_kwargs,
        )
    else:
        # Test-local repositories and external connectors may implement only
        # the list contract. The production Databricks repository implements
        # count; use the returned row count as a lower-bound only when the
        # dependency cannot report a cohort total.
        total_matching = len(leads)
    response.headers["X-Total-Matching"] = str(total_matching)
    response.headers["X-Returned-Rows"] = str(len(leads))
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

"""Query-parameter contract for ``GET /api/leads``.

The Lead Queue accepts 40 query parameters -- geography drill-downs,
Portfolio Builder replays, sales-workflow state, the governed Genie
cohort handoff, and the public opportunity-score / rate-spread bounds. Declaring them inline left the router's signature ~290
lines long, which buried the ten lines of logic underneath it.

Each parameter is exported as an ``Annotated`` alias so the router reads
as a list of names and the FastAPI ``Query()`` metadata -- alias,
pattern, bounds, description -- lives in one reviewable place. FastAPI
resolves an aliased ``Annotated`` identically to an inline one, so the
generated OpenAPI surface is byte-identical; ``tests/fixtures/
openapi_baseline.json`` pins that.

Defaults live in ``lead_query_params``, the one dependency every Lead Queue
read (the ranked list, and the audit-free count and facets) declares them
through: they are part of the endpoint's behaviour, not of the parameter's
type. ``LeadQueryParams`` is what that dependency returns, frozen, in the
declaration order the OpenAPI surface lists.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, Query

from backend.schemas.common import PUBLIC_UUID_PATTERN
from backend.schemas.genie_numeric_filters import GENIE_NUMERIC_FILTER_BOUNDS

# Kept in sync with DatabricksLeadRepository.{DEFAULT_LIMIT, MAX_LIMIT}.
# Module-level so the router's Query() annotation, backend.api.leads
# (which re-exports them), and the unit tests all read one source of truth.
DEFAULT_LEAD_LIMIT: int = 500
MAX_LEAD_LIMIT: int = 5000

SegmentCodesParam = Annotated[
    str | None,
    Query(
        alias="segment_codes",
        description=(
            "Optional comma-separated SegmentCode list for multi-card "
            "filters. Use when more than one segment card is active."
        ),
    ),
]

SegmentModeParam = Annotated[
    str,
    Query(
        description=(
            "any = segment arrays overlap; all = borrower contains every "
            "selected segment code."
        ),
    ),
]

StateParam = Annotated[
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
]

ZipParam = Annotated[
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
]

CountyParam = Annotated[
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
]

StatesParam = Annotated[
    str | None,
    Query(
        alias="states",
        max_length=256,
        description=(
            "Optional comma-separated USPS states for Genie-generated "
            "cohort actions."
        ),
    ),
]

ZipsParam = Annotated[
    str | None,
    Query(
        alias="zips",
        max_length=4096,
        description=(
            "Optional comma-separated 5-digit ZIP list for Genie-generated "
            "cohort actions."
        ),
    ),
]

CitiesParam = Annotated[
    str | None,
    Query(
        alias="cities",
        max_length=4096,
        description=(
            "Optional comma-separated CITY~ST pairs (for example "
            "CHICAGO~IL,FORT LAUDERDALE~FL) for city-grain Genie cohort "
            "actions. Always a pair: 5 city names in gold span two states, "
            "so a bare name opens the wrong population."
        ),
    ),
]

CountiesParam = Annotated[
    str | None,
    Query(
        alias="counties",
        max_length=4096,
        description=(
            "Optional comma-separated 5-digit county FIPS list for "
            "Genie-generated cohort actions."
        ),
    ),
]

BorrowerIdsParam = Annotated[
    str | None,
    Query(
        alias="borrower_ids",
        max_length=8192,
        description=(
            "Optional comma-separated synthetic borrower IDs for Genie "
            "borrower-list cohort actions."
        ),
    ),
]

TargetLenderRefParam = Annotated[
    str | None,
    Query(
        alias="target_lender_ref",
        max_length=64,
        description="Optional public-demo-safe current-lender ref such as the configured tenant lender or Competitor A.",
    ),
]

GeographyParam = Annotated[
    str | None,
    Query(
        alias="geography",
        max_length=64,
        description="Optional Portfolio Builder geography label to replay the built population.",
    ),
]

OccupancyParam = Annotated[
    str | None,
    Query(
        alias="occupancy",
        max_length=64,
        description="Optional Portfolio Builder occupancy filter.",
    ),
]

LienStatusParam = Annotated[
    str | None,
    Query(
        alias="lien_status",
        max_length=64,
        description="Optional Portfolio Builder lien-status filter.",
    ),
]

LenderRelationshipParam = Annotated[
    str | None,
    Query(
        alias="lender_relationship",
        max_length=64,
        description="Optional Portfolio Builder lender-relationship filter.",
    ),
]

ProductParam = Annotated[
    str | None,
    Query(
        alias="product",
        max_length=64,
        description="Optional Portfolio Builder product filter.",
    ),
]

LoanProductParam = Annotated[
    str | None,
    Query(alias="loan_product", max_length=64, description="Optional loan product-type filter."),
]

OriginationChannelParam = Annotated[
    str | None,
    Query(alias="origination_channel", max_length=64, description="Optional origination-channel filter."),
]

MinEquityPctLabelParam = Annotated[
    str | None,
    Query(
        alias="min_equity_pct_label",
        max_length=32,
        description="Optional Portfolio Builder display equity threshold.",
    ),
]

MinEquityPctParam = Annotated[
    float | None,
    Query(
        alias="min_equity_pct",
        ge=0,
        le=100,
        description="Optional numeric Portfolio Builder equity threshold.",
    ),
]

OwnerLinkParam = Annotated[
    str | None,
    Query(
        alias="owner_link",
        max_length=64,
        description="Optional owner-link bucket from Segment Intelligence.",
    ),
]

PurchaseIntentParam = Annotated[
    str | None,
    Query(
        alias="purchase_intent",
        max_length=64,
        description="Optional purchase-intent bucket from Segment Intelligence.",
    ),
]

MarketingEligibilityParam = Annotated[
    str,
    Query(
        alias="marketing_eligibility",
        max_length=32,
        description="Contactability gate. Defaults to Eligible only for fail-closed campaign/export use.",
    ),
]

ConsentStatusParam = Annotated[
    str | None,
    Query(
        alias="consent_status",
        max_length=32,
        description="Optional consent filter: Opt-in, Opt-out, Unknown, Any.",
    ),
]

RecencyParam = Annotated[
    str | None,
    Query(
        alias="recency",
        max_length=32,
        description="Optional touch-recency filter: Untouched 30d/60d/90d or Any.",
    ),
]

IncludeSuppressedForAnalyticsParam = Annotated[
    bool,
    Query(
        alias="include_suppressed_for_analytics",
        description=(
            "Admin-only analytics override. When true, clears the default "
            "Eligible only marketing gate so suppressed/non-opt-in rows can "
            "be counted or inspected without making them campaign-actionable."
        ),
    ),
]

IncludeIdentityProofParam = Annotated[
    bool,
    Query(
        alias="include_identity_proof",
        description=(
            "Admin/evaluation-only complete-cohort digest and snapshot headers. "
            "Disabled by default because it performs an aggregate proof query."
        ),
    ),
]

ApprovalStatusParam = Annotated[
    Literal["pending", "approved", "rejected", "hold", "any"],
    Query(alias="approval_status", description="Sales workflow approval state filter."),
]

OutreachStatusParam = Annotated[
    Literal["none", "queued", "actioned", "sent", "bounced", "replied", "any"],
    Query(alias="outreach_status", description="Sales workflow outreach state filter."),
]

AssignedToParam = Annotated[
    str | None,
    Query(alias="assigned_to", max_length=256, description="Internal LO email assigned to the lead."),
]

AgedDaysParam = Annotated[
    int | None,
    Query(alias="aged_days", ge=1, le=90, description="Only approved leads aged at least this many days with no outreach."),
]

CohortIdParam = Annotated[
    str | None,
    Query(
        alias="cohort_id",
        max_length=64,
        description="Optional Lakebase persisted cohort id produced by a governed Genie action.",
    ),
]

FunnelStageParam = Annotated[
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
]

# The public bounds reuse the reviewed ranges a governed Genie cohort floor
# is validated against, so a URL can never ask for a threshold the cohort
# vocabulary would refuse (score 0..100; spread is signed, -1000..5000).
SCORE_BOUND_RANGE: tuple[int, int] = GENIE_NUMERIC_FILTER_BOUNDS["min_opportunity_score"]
SPREAD_BOUND_RANGE: tuple[int, int] = GENIE_NUMERIC_FILTER_BOUNDS["min_rate_spread_bps"]

MinOpportunityScoreParam = Annotated[
    int | None,
    Query(
        alias="min_opportunity_score",
        ge=SCORE_BOUND_RANGE[0],
        le=SCORE_BOUND_RANGE[1],
        description="Optional inclusive lower bound on the opportunity score.",
    ),
]

MaxOpportunityScoreParam = Annotated[
    int | None,
    Query(
        alias="max_opportunity_score",
        ge=SCORE_BOUND_RANGE[0],
        le=SCORE_BOUND_RANGE[1],
        description="Optional inclusive upper bound on the opportunity score.",
    ),
]

MinRateSpreadBpsParam = Annotated[
    int | None,
    Query(
        alias="min_rate_spread_bps",
        ge=SPREAD_BOUND_RANGE[0],
        le=SPREAD_BOUND_RANGE[1],
        description=(
            "Optional inclusive lower bound on the signed rate spread in basis "
            "points. Borrowers with no spread never match a spread bound."
        ),
    ),
]

MaxRateSpreadBpsParam = Annotated[
    int | None,
    Query(
        alias="max_rate_spread_bps",
        ge=SPREAD_BOUND_RANGE[0],
        le=SPREAD_BOUND_RANGE[1],
        description=(
            "Optional inclusive upper bound on the signed rate spread in basis "
            "points. Borrowers with no spread never match a spread bound."
        ),
    ),
]

# Maker-checker scope (audit flow-02, 12.4 #10): the open borrowers of one
# approval request. GET /leads only: the aggregates refuse it (a read of named
# borrowers is audited), and the list refuses it beside a cohort, a borrower
# list or a Growth Agent handoff.
ApprovalRequestBatchParam = Annotated[
    str | None,
    Query(
        alias="approval_request_batch",
        pattern=PUBLIC_UUID_PATTERN.pattern,
        description=(
            "Optional approval request id: the list shows that request's open "
            "borrowers (approvers, or the requester). GET /leads only."
        ),
    ),
]

LimitParam = Annotated[
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
]

ApprovalStatusValue = Literal["pending", "approved", "rejected", "hold", "any"]
OutreachStatusValue = Literal["none", "queued", "actioned", "sent", "bounced", "replied", "any"]
FunnelStageValue = Literal[
    "addressable",
    "in_the_money",
    "high_opportunity",
    "offer_recommended",
    "approved",
    "actioned",
]


@dataclass(frozen=True)
class LeadQueryParams:
    """Every Lead Queue filter a request carried, exactly as FastAPI parsed it.

    Field order is the OpenAPI parameter order of ``GET /leads`` (pinned by
    ``tests/unit/test_lead_query_resolution.py``); ``limit`` is not a filter
    and stays its own dependency (``lead_limit``). Values are raw: resolving
    them against the actor, a persisted cohort or a Growth Agent handoff is
    the job of the Lead Queue resolution service.
    """

    segment: str | None
    segment_codes: str | None
    segment_mode: str
    portfolio_id: str | None
    state: str | None
    zip_code: str | None
    county: str | None
    states: str | None
    zips: str | None
    counties: str | None
    cities: str | None
    borrower_ids: str | None
    target_lender_ref: str | None
    geography: str | None
    occupancy: str | None
    lien_status: str | None
    lender_relationship: str | None
    product: str | None
    loan_product: str | None
    origination_channel: str | None
    min_equity_pct_label: str | None
    min_equity_pct: float | None
    owner_link: str | None
    purchase_intent: str | None
    marketing_eligibility: str
    consent_status: str | None
    recency: str | None
    include_suppressed_for_analytics: bool
    include_identity_proof: bool
    approval_status: ApprovalStatusValue
    outreach_status: OutreachStatusValue
    assigned_to: str | None
    aged_days: int | None
    cohort_id: str | None
    funnel_stage: FunnelStageValue | None
    min_opportunity_score: int | None = None
    max_opportunity_score: int | None = None
    min_rate_spread_bps: int | None = None
    max_rate_spread_bps: int | None = None
    approval_request_batch: str | None = None

    def public_bounds(self) -> dict[str, int]:
        """The score and spread bounds this request set, keyed by wire name."""

        return {
            key: value
            for key, value in (
                ("min_opportunity_score", self.min_opportunity_score),
                ("max_opportunity_score", self.max_opportunity_score),
                ("min_rate_spread_bps", self.min_rate_spread_bps),
                ("max_rate_spread_bps", self.max_rate_spread_bps),
            )
            if value is not None
        }


def lead_query_params(
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
    cities: CitiesParam = None,
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
    min_opportunity_score: MinOpportunityScoreParam = None,
    max_opportunity_score: MaxOpportunityScoreParam = None,
    min_rate_spread_bps: MinRateSpreadBpsParam = None,
    max_rate_spread_bps: MaxRateSpreadBpsParam = None,
    approval_request_batch: ApprovalRequestBatchParam = None,
) -> LeadQueryParams:
    """FastAPI dependency: the Lead Queue filter parameters, in wire order.

    FastAPI flattens a dependency's parameters into the operation in
    declaration order, so an endpoint that declares this and then
    ``lead_limit`` lists exactly the parameters, aliases, defaults and bounds
    the inline signature did: the committed OpenAPI baseline is unchanged.
    """

    return LeadQueryParams(
        segment=segment,
        segment_codes=segment_codes,
        segment_mode=segment_mode,
        portfolio_id=portfolio_id,
        state=state,
        zip_code=zip_code,
        county=county,
        states=states,
        zips=zips,
        counties=counties,
        cities=cities,
        borrower_ids=borrower_ids,
        target_lender_ref=target_lender_ref,
        geography=geography,
        occupancy=occupancy,
        lien_status=lien_status,
        lender_relationship=lender_relationship,
        product=product,
        loan_product=loan_product,
        origination_channel=origination_channel,
        min_equity_pct_label=min_equity_pct_label,
        min_equity_pct=min_equity_pct,
        owner_link=owner_link,
        purchase_intent=purchase_intent,
        marketing_eligibility=marketing_eligibility,
        consent_status=consent_status,
        recency=recency,
        include_suppressed_for_analytics=include_suppressed_for_analytics,
        include_identity_proof=include_identity_proof,
        approval_status=approval_status,
        outreach_status=outreach_status,
        assigned_to=assigned_to,
        aged_days=aged_days,
        cohort_id=cohort_id,
        funnel_stage=funnel_stage,
        min_opportunity_score=min_opportunity_score,
        max_opportunity_score=max_opportunity_score,
        min_rate_spread_bps=min_rate_spread_bps,
        max_rate_spread_bps=max_rate_spread_bps,
        approval_request_batch=approval_request_batch,
    )


def lead_limit(limit: LimitParam = DEFAULT_LEAD_LIMIT) -> int:
    """FastAPI dependency: the page size, declared after the filters."""

    return limit


LeadQueryParamsDep = Annotated[LeadQueryParams, Depends(lead_query_params)]
LeadLimitDep = Annotated[int, Depends(lead_limit)]

"""Query-parameter contract for ``GET /api/leads``.

The Lead Queue accepts 35 query parameters -- geography drill-downs,
Portfolio Builder replays, sales-workflow state, and the governed Genie
cohort handoff. Declaring them inline left the router's signature ~290
lines long, which buried the ten lines of logic underneath it.

Each parameter is exported as an ``Annotated`` alias so the router reads
as a list of names and the FastAPI ``Query()`` metadata -- alias,
pattern, bounds, description -- lives in one reviewable place. FastAPI
resolves an aliased ``Annotated`` identically to an inline one, so the
generated OpenAPI surface is byte-identical; ``tests/fixtures/
openapi_baseline.json`` pins that.

Defaults stay in the router signature: they are part of the endpoint's
behaviour, not of the parameter's type.
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Query

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

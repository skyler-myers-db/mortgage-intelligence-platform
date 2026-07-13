"""Governed registry of Unity Catalog assets exposed by Module 0."""

from __future__ import annotations

from dataclasses import dataclass

from backend.config.settings import settings
from backend.schemas.assets import AssetObjectType


@dataclass(frozen=True)
class AssetDescriptor:
    key: str
    title: str
    description: str
    schema_name: str
    object_name: str
    object_type: AssetObjectType = "table"
    catalog_name: str | None = None
    readiness_source_name: str | None = None
    allow_count_fallback: bool = True
    aliases: tuple[str, ...] = ()

    @property
    def fqn(self) -> str:
        catalog = self.catalog_name or settings.mip_default_catalog
        return f"{catalog}.{self.schema_name}.{self.object_name}"

    @property
    def logical_fqn(self) -> str:
        if self.catalog_name:
            return self.fqn
        return f"mip.{self.schema_name}.{self.object_name}"


def _descriptor(
    schema_name: str,
    object_name: str,
    *,
    title: str,
    description: str,
    object_type: AssetObjectType = "table",
    catalog_name: str | None = None,
    readiness_source_name: str | None = None,
    allow_count_fallback: bool = True,
    aliases: tuple[str, ...] = (),
) -> AssetDescriptor:
    return AssetDescriptor(
        key=object_name,
        title=title,
        description=description,
        schema_name=schema_name,
        object_name=object_name,
        object_type=object_type,
        catalog_name=catalog_name,
        readiness_source_name=readiness_source_name,
        allow_count_fallback=allow_count_fallback,
        aliases=aliases,
    )


ASSET_DESCRIPTORS: tuple[AssetDescriptor, ...] = (
    _descriptor(
        "gold",
        "lead_population",
        title="Gold Lead Queue Population",
        description=(
            "Ranked, quality-filtered borrowers eligible for the lead queue. "
            "This asset carries borrower-safe display fields, segment codes, "
            "evidence references, ranking, and approval state."
        ),
        readiness_source_name="UC Gold Lead Population",
    ),
    _descriptor(
        "gold",
        "borrower_360",
        title="Gold Borrower 360",
        description=(
            "Borrower-level dossier table used by Borrower 360, offers, and "
            "analytics. It joins governed Cotality-derived signals with Module 0 "
            "scoring outputs."
        ),
        readiness_source_name="UC Gold Borrower 360",
    ),
    _descriptor(
        "gold",
        "lead_scores",
        title="Gold Lead Scores",
        description=(
            "Deterministic score table with the five scoring sub-scores, "
            "opportunity score, refinance-economics flag, and primary offer code."
        ),
        readiness_source_name="UC Gold Lead Scores",
    ),
    _descriptor(
        "gold",
        "segment_population",
        title="Gold Segment Population",
        description="Segment rollup table that powers Segment Intelligence and analytics.",
        readiness_source_name="UC Gold Segment Population",
    ),
    _descriptor(
        "gold",
        "borrower_dossier",
        title="Gold Borrower Dossier",
        description="Pre-joined borrower dossier used by proof, offer, and 360 read paths.",
        readiness_source_name="UC Gold Borrower Dossier",
    ),
    _descriptor(
        "gold",
        "evidence_events",
        title="Gold Evidence Events",
        description=(
            "Append-only evidence stream. Evidence rows explain which governed "
            "source signal supported a score, offer, or queue decision."
        ),
    ),
    _descriptor(
        "gold",
        "source_readiness",
        title="Gold Source Readiness",
        description=(
            "Non-PII source status summary used by data estate and admin surfaces "
            "to prove what data is live, pending, or synthetic."
        ),
    ),
    _descriptor(
        "corelogic",
        "entrada_eval_property_domain_v3",
        catalog_name="cotality_mortgage_data",
        title="Cotality Property Domain Share",
        description="Governed shared property-domain input used by the Module 0 property lift.",
        allow_count_fallback=False,
    ),
    _descriptor(
        "corelogic",
        "entrada_eval_voluntary_lien_status_marketing_v2",
        catalog_name="cotality_mortgage_data",
        title="Cotality Voluntary Lien Share",
        description="Governed shared lien and valuation input used by Module 0 economics.",
        allow_count_fallback=False,
    ),
    _descriptor(
        "corelogic",
        "entrada_eval_mls_listing_v1",
        catalog_name="cotality_mortgage_data",
        title="Cotality MLS Listing Share",
        description="Governed shared MLS listing input used by the listing-activity lift.",
        allow_count_fallback=False,
    ),
    _descriptor(
        "corelogic",
        "entrada_eval_heloc_propensity_score_v1",
        catalog_name="cotality_mortgage_data",
        title="Cotality HELOC Propensity Share",
        description="Governed shared HELOC propensity-model input; not a permit filing feed.",
        allow_count_fallback=False,
    ),
    _descriptor(
        "corelogic",
        "entrada_eval_refi_propensity_score_v1",
        catalog_name="cotality_mortgage_data",
        title="Cotality Refi Propensity Share",
        description="Governed shared refinance propensity-model input.",
        allow_count_fallback=False,
    ),
    _descriptor(
        "corelogic",
        "entrada_eval_mortgage_domain_v1",
        catalog_name="cotality_mortgage_data",
        title="Cotality Mortgage Domain Share",
        description="Governed shared mortgage-event history used by lifecycle evidence.",
        allow_count_fallback=False,
    ),
    _descriptor(
        "corelogic",
        "entrada_eval_owner_transfer_domain_v1",
        catalog_name="cotality_mortgage_data",
        title="Cotality Owner Transfer Share",
        description="Governed shared owner-transfer and sale-event history.",
        allow_count_fallback=False,
    ),
    _descriptor(
        "silver",
        "property_master",
        title="Silver Property Master",
        description="PII-minimized CLIP-grain property lift from the Cotality property share.",
    ),
    _descriptor(
        "silver",
        "property_owners",
        title="Silver Property Owners",
        description="Multi-owner, masked owner-entity lift used by household and eligibility logic.",
    ),
    _descriptor(
        "silver",
        "lien_current",
        title="Silver Current Liens",
        description="Typed current-lien and valuation inputs used by borrower economics.",
    ),
    _descriptor(
        "silver",
        "market_rates_weekly",
        title="Silver Weekly Market Rates",
        description="Governed weekly MORTGAGE30US market-rate reference used by rate-spread logic.",
    ),
    _descriptor(
        "silver",
        "mortgage_events",
        title="Silver Mortgage Events",
        description="PII-minimized mortgage-event history lifted from the Cotality share.",
    ),
    _descriptor(
        "silver",
        "owner_transfer_events",
        title="Silver Owner Transfer Events",
        description="PII-minimized owner-transfer and sale events lifted from the Cotality share.",
    ),
    _descriptor(
        "gold",
        "property_owner_bridge",
        title="Gold Property Owner Bridge",
        description="Owner-Link rollup used for related-property and investor evidence.",
    ),
    _descriptor(
        "gold",
        "household_rollup",
        title="Gold Household Rollup",
        description="Opt-in household grouping with one contact-eligible primary borrower.",
    ),
    _descriptor(
        "first_party",
        "loan_applications",
        title="First-Party Loan Applications",
        description="Governed first-party LOS application facts used for origination channel.",
    ),
    _descriptor(
        "gold",
        "fn_bounded_mortgage_rate",
        title="Bounded Mortgage Rate Function",
        description="Canonical rate-boundary function used before amortization estimates.",
        object_type="function",
        allow_count_fallback=False,
    ),
    _descriptor(
        "gold",
        "fn_estimated_upb",
        title="Estimated UPB Function",
        description="Canonical amortization function for estimated current unpaid principal balance.",
        object_type="function",
        allow_count_fallback=False,
    ),
    _descriptor(
        "gold",
        "fn_estimated_upb_confidence_band",
        title="Estimated UPB Confidence Band Function",
        description="Canonical confidence-band function around estimated current UPB.",
        object_type="function",
        allow_count_fallback=False,
    ),
    _descriptor(
        "gold",
        "fn_high_opportunity",
        title="High Opportunity Function",
        description="Canonical threshold for high-opportunity borrower scores.",
        object_type="function",
        allow_count_fallback=False,
    ),
    _descriptor(
        "gold",
        "fn_in_the_money",
        title="In-the-Money Function",
        description="Canonical refinance-economics screen over rate spread and equity.",
        object_type="function",
        allow_count_fallback=False,
    ),
    _descriptor(
        "gold",
        "fn_lead_score",
        title="Lead Score Function",
        description="Canonical weighted Module 0 opportunity-score function.",
        object_type="function",
        allow_count_fallback=False,
    ),
    _descriptor(
        "gold",
        "fn_loan_product_type",
        title="Loan Product Type Function",
        description="Canonical loan-product classifier using lien facts and governed limits.",
        object_type="function",
        allow_count_fallback=False,
    ),
    _descriptor(
        "gold",
        "fn_next_best_offer",
        title="Next Best Offer Function",
        description="Canonical reviewed function selecting the primary offer path.",
        object_type="function",
        allow_count_fallback=False,
    ),
    _descriptor(
        "gold",
        "fn_rate_spread",
        title="Rate Spread Function",
        description="Canonical basis-point spread between borrower and market rates.",
        object_type="function",
        allow_count_fallback=False,
    ),
    _descriptor(
        "gold",
        "fn_score_band",
        title="Score Band Function",
        description="Canonical high, medium, and low opportunity-band classifier.",
        object_type="function",
        allow_count_fallback=False,
    ),
    _descriptor(
        "silver",
        "listing_activity",
        title="Silver MLS Listing Activity",
        description=(
            "CLIP-keyed Cotality MLS listing lift. Excludes raw street address, "
            "public remarks, agent/contact, and lockbox fields; active/current "
            "rows drive listed-for-sale triggers."
        ),
        readiness_source_name="MLS Listings",
        aliases=("mls_listing", "listings"),
    ),
    _descriptor(
        "silver",
        "heloc_propensity",
        title="Silver HELOC Propensity",
        description=(
            "CLIP-keyed Cotality HELOC propensity model scores. This is a "
            "model propensity feed, not a filed building-permit source."
        ),
        readiness_source_name="Cotality HELOC Propensity",
        aliases=("heloc_intent",),
    ),
    _descriptor(
        "silver",
        "refi_propensity",
        title="Silver Refi Propensity",
        description="CLIP-keyed Cotality refinance propensity model scores.",
        readiness_source_name="Cotality Refi Propensity",
        aliases=("refinance_propensity",),
    ),
    _descriptor(
        "gold",
        "lockin_cohort",
        title="Gold Lock-in Cohort",
        description="Borrower cohort table used to identify retention and lock-in opportunities.",
    ),
    _descriptor(
        "gold",
        "borrower_lifecycle_state",
        title="Gold Borrower Lifecycle State",
        description=(
            "Scheduled Unity Catalog mirror of borrower approval and outreach state. "
            "Lakebase remains authoritative for the operational records."
        ),
    ),
    _descriptor(
        "gold",
        "funnel_snapshot_daily",
        title="Gold Funnel Snapshot Daily",
        description="Daily aggregate funnel counts used by executive analytics.",
    ),
    _descriptor(
        "gold",
        "county_rollup",
        title="Gold County Rollup",
        description="County-level rollup for geographic analytics and drilldowns.",
    ),
    _descriptor(
        "gold",
        "zip_rollup",
        title="Gold ZIP Rollup",
        description="ZIP-level rollup for geographic analytics and drilldowns.",
        aliases=("zip_code_rollup",),
    ),
    _descriptor(
        "gold",
        "equity_spread_points",
        title="Gold Equity Spread Points",
        description=(
            "Precomputed economics scatter surface: per-borrower equity x "
            "rate-spread points with fn_score_band bands and density-bin "
            "coordinates for the Analytics economics overview and zoom."
        ),
    ),
    _descriptor(
        "semantics",
        "portfolio_headline_metric_view",
        title="Portfolio Headline Metric View",
        description=(
            "Borrower-grain semantic view defining every demoed headline KPI: "
            "marketable population, refi economics screen, high opportunity "
            "(canonical fn_high_opportunity threshold), offers available "
            "(non-null fn_next_best_offer), primary offer paths, and average "
            "opportunity score."
        ),
        object_type="view",
        aliases=("headline_metric_view",),
    ),
    _descriptor(
        "semantics",
        "lead_generation_metric_view",
        title="Lead Generation Metric View",
        description="Business-friendly KPI view used by the home page and Genie answers.",
        object_type="view",
    ),
    _descriptor(
        "semantics",
        "segment_performance_metric_view",
        title="Segment Performance Metric View",
        description="Semantic segment rollup used by analytics segment pages.",
        object_type="view",
    ),
    _descriptor(
        "semantics",
        "borrower_opportunity_metric_view",
        title="Borrower Opportunity Metric View",
        description=(
            "Business-friendly semantic view for borrower economics, geography, "
            "segments, and opportunity score analysis."
        ),
        object_type="view",
    ),
    _descriptor(
        "ref",
        "offer_rules_config",
        title="Offer Rules Configuration",
        description="Governed threshold table for refinance-economics and primary offer rules.",
        aliases=("offer_rules",),
    ),
    _descriptor(
        "ref",
        "lender_dictionary",
        title="Lender Dictionary",
        description="Public lender alias dictionary used for non-PII target-lender labeling.",
    ),
)

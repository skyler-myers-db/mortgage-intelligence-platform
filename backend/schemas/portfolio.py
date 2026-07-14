"""Portfolio criteria, preview, campaign, and saved-build contracts."""

import math
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.schemas._validators import (
    normalize_public_lender_ref,
    reviewed_geography_labels,
    reviewed_state_codes,
)
from backend.schemas.campaign_status import (
    CampaignStatus,  # noqa: F401 - compatibility re-export
    CampaignStatusPatchRequest,  # noqa: F401 - compatibility re-export
)
from backend.schemas.portfolio_campaign import (
    CampaignRecommendationEvidence,  # noqa: F401 - compatibility re-export
    CampaignRecommendationRequest,  # noqa: F401 - compatibility re-export
    CampaignRecommendationResponse,  # noqa: F401 - compatibility re-export
    CampaignRecommendationVariant,  # noqa: F401 - compatibility re-export
    PortfolioOfferMixRow,
    assert_borrower_campaign_copy,
    assert_public_campaign_json,
    assert_public_campaign_text,
    bind_portfolio_criteria,
)

_OCCUPANCY_LABELS: frozenset[str] = frozenset(
    {"Owner-occupied", "Non-owner-occupied", "All"},
)
_LIEN_STATUS_LABELS: frozenset[str] = frozenset(
    {"Open 1st lien", "Open first lien", "Open HELOC", "Free & clear", "Free and clear", "Any"},
)
_LENDER_RELATIONSHIP_LABELS: frozenset[str] = frozenset(
    {"All", "Current customer", "Former customer", "Competitor customer", "Competitor"},
)
_PRODUCT_LABELS: frozenset[str] = frozenset(
    {"All products", "Refi", "HELOC", "Cash-out", "Purchase", "Retention"},
)
# S1.6 loan product type; distinct from the recommended-offer product filter.
_LOAN_PRODUCT_LABELS: frozenset[str] = frozenset(
    {"All loan products", "Conventional", "Jumbo", "FHA", "VA", "Other", "Unknown"},
)
_LOAN_PRODUCT_ALIASES: dict[str, str] = {
    "all": "All loan products",
    "all_loan_products": "All loan products",
    "conventional": "Conventional",
    "jumbo": "Jumbo",
    "fha": "FHA",
    "va": "VA",
    "other": "Other",
    "unknown": "Unknown",
}
# S1.6: origination-channel dimension (gold.borrower_360.origination_channel,
# sourced from funded first-party LOS applications; Unknown = NULL).
_ORIGINATION_CHANNEL_LABELS: frozenset[str] = frozenset(
    {"All channels", "Loan officer", "Digital", "Branch", "Call center", "Unknown"},
)
_ORIGINATION_CHANNEL_ALIASES: dict[str, str] = {
    "all": "All channels",
    "all_channels": "All channels",
    "loan_officer": "Loan officer",
    "digital": "Digital",
    "branch": "Branch",
    "call_center": "Call center",
    "unknown": "Unknown",
}
_EQUITY_LABELS: frozenset[str] = frozenset({"≥ 15%", "≥ 25%", "≥ 40%", "Any"})
_OWNER_LINK_LABELS: frozenset[str] = frozenset(
    {"All", "Single-property owner", "Multi-property (2-4)", "Portfolio investor (5+)"}
)
_PURCHASE_INTENT_LABELS: frozenset[str] = frozenset(
    {"All", "Listed for sale", "HELOC intent", "Both"}
)
_PURCHASE_INTENT_ALIASES: dict[str, str] = {
    # Legacy deep links used this label before Cotality delivered the HELOC
    # propensity feed. Normalize it so no active surface claims filed permits.
    "recent_permit_activity": "HELOC intent",
}
_MARKETING_ELIGIBILITY_LABELS: frozenset[str] = frozenset(
    {"Eligible only", "Any", "Suppressed only"}
)
_MARKETING_ELIGIBILITY_ALIASES: dict[str, str] = {
    "eligible_only": "Eligible only",
    "eligible": "Eligible only",
    "suppressed_only": "Suppressed only",
    "suppressed": "Suppressed only",
    "any": "Any",
}
_CONSENT_STATUS_ALIASES: dict[str, str] = {
    "opt_in": "Opt-in",
    "opt-in": "Opt-in",
    "optin": "Opt-in",
    "opt_out": "Opt-out",
    "opt-out": "Opt-out",
    "optout": "Opt-out",
    "unknown": "Unknown",
    "any": "Any",
}
_RECENCY_ALIASES: dict[str, str] = {
    "untouched_30d": "Untouched 30d",
    "30d": "Untouched 30d",
    "untouched_60d": "Untouched 60d",
    "60d": "Untouched 60d",
    "untouched_90d": "Untouched 90d",
    "90d": "Untouched 90d",
    "any": "Any",
}
_OUTREACH_CHANNELS: frozenset[str] = frozenset({"email", "sms", "direct_mail"})
_SEND_WINDOW_DAYS: frozenset[str] = frozenset(
    {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
)
_SEND_WINDOW_DAY_ALIASES: dict[str, str] = {
    "mon": "Monday",
    "monday": "Monday",
    "tue": "Tuesday",
    "tues": "Tuesday",
    "tuesday": "Tuesday",
    "wed": "Wednesday",
    "weds": "Wednesday",
    "wednesday": "Wednesday",
    "thu": "Thursday",
    "thur": "Thursday",
    "thurs": "Thursday",
    "thursday": "Thursday",
    "fri": "Friday",
    "friday": "Friday",
    "sat": "Saturday",
    "saturday": "Saturday",
    "sun": "Sunday",
    "sunday": "Sunday",
}


def _validate_optional_label(
    value: str | None,
    *,
    allowed: frozenset[str],
    field_name: str,
) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if stripped not in allowed:
        raise ValueError(f"{field_name} must be one of the reviewed Portfolio Builder options")
    return stripped


def _normalise_reviewed_alias(
    value: str | None,
    *,
    allowed: frozenset[str],
    aliases: dict[str, str],
    field_name: str,
) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if stripped in allowed:
        return stripped
    alias = aliases.get(stripped.lower().replace(" ", "_"))
    if alias in allowed:
        return alias
    raise ValueError(f"{field_name} must be one of the reviewed options")


class KpiTrend(BaseModel):
    """A single KPI's 7-day history + delta.

    ``series`` is ordered oldest → newest; may be shorter than 7 if the
    daily funnel snapshot hasn't accumulated that much history yet. ``delta_pct``
    is signed percent change from series[0] to series[-1]; ``None`` when the
    series has fewer than 2 points or the starting value is zero.
    """

    series: list[float]
    delta_pct: float | None = None
    direction: str = "flat"  # "up" | "down" | "flat"
    comparison_label: str | None = None
    note: str | None = None


class PortfolioPreview(BaseModel):
    # Headline KPIs — ALL measured over the named semantic view
    # mip.semantics.portfolio_headline_metric_view (borrower_360-derived;
    # trends from funnel_snapshot_daily). Cotality Delta Share + public FRED
    # data only; no lender CRM / campaign / app-activity signal contributes.
    marketable_population: int
    high_intent_leads: int
    # SUM(is_high_opportunity) — canonical threshold pinned in
    # mip.gold.fn_high_opportunity / scoring.HIGH_OPPORTUNITY_THRESHOLD.
    top_tier_opportunities: int | None = None
    # SUM(offer_recommended) — actionable lane (non-null and not nurture).
    offers_recommended: int | None = None
    # SUM(offer_available) — borrowers with a non-null fn_next_best_offer
    # decision ("offers available" headline measure).
    offers_available: int | None = None
    avg_score: int | None = Field(default=None, ge=0, le=100)
    # Cohort economics are measured over the exact filtered population in the
    # same semantic query as the headline KPIs. These replace client-side
    # placeholder assumptions; missing source values remain null.
    avg_current_lien_balance_usd: int | None = Field(default=None, ge=0)
    # Same filtered query, restricted to the in-the-money population used by
    # the economics scenario. Never substitute the all-borrower average.
    avg_high_intent_lien_balance_usd: int | None = Field(default=None, ge=0)
    total_current_lien_balance_usd: int | None = Field(default=None, ge=0)
    avg_equity_pct: float | None = Field(default=None, ge=0, le=100)
    avg_rate_spread_bps: float | None = None
    offer_mix: list["PortfolioOfferMixRow"] = Field(default_factory=list)
    # Optional trend histories keyed by KPI field name. When absent, the UI
    # renders the KPI without a sparkline.
    trends: dict[str, KpiTrend] = Field(default_factory=dict)
    # Explains the trend contract. "live" means sparklines are cohort-
    # aligned; "not_applicable" means the API intentionally withheld
    # trend lines because the snapshot table does not store that custom
    # filter grain; "unavailable" means the snapshot query failed.
    trend_status: str = "live"
    trend_note: str | None = None
    # MAX(snapshot_at) from funnel_snapshot_daily — the most recent time the
    # gold mirror was refreshed. Rendered in the user's local timezone.
    data_refreshed_at: datetime | None = None
    # Lakebase-sourced lifecycle counts (your team's activity in the app).
    # Not Cotality-derived — hidden from the headline strip but exposed here
    # for panels that explicitly surface operator activity (e.g. admin
    # audit trail summary).
    approved_count: int | None = None
    in_outreach_count: int | None = None

    # R5-20: server-authoritative "this workspace has never had a gold
    # refresh" flag. Frontend used to infer day-0 from
    # ``data_refreshed_at is None`` + ``marketable_population == 0``, but
    # during a partial CTAS roll those two facts desynchronise (row in
    # borrower_360 with no snapshot row yet, or vice versa) and the UI
    # renders a mixed state -- KPI cards for a population that isn't
    # really there. Authoritative signal is ``COUNT(*) FROM
    # mip.gold.lead_population == 0``. Additive on the wire; default
    # ``False`` keeps pre-R5-20 clients parsing.
    day_zero: bool = False


class PortfolioCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    geography: str | None = None
    states: list[str] | None = None
    occupancy: str | None = None
    lien_status: str | None = None
    lender_relationship: str | None = None
    product: str | None = None
    loan_product: str | None = None
    origination_channel: str | None = None
    target_lender_ref: str | None = None
    min_equity_pct: float | None = None
    owner_link: str | None = None
    purchase_intent: str | None = None
    marketing_eligibility: str | None = "Eligible only"
    consent_status: str | None = None
    recency: str | None = None
    # Alternative entry point the frontend uses — a display label like
    # "≥ 25%" / "Any". Resolved server-side to ``min_equity_pct``.
    min_equity_pct_label: str | None = None

    @field_validator("min_equity_pct")
    @classmethod
    def _equity_pct_is_bounded(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value < 0 or value > 100:
            raise ValueError("min_equity_pct must be between 0 and 100")
        return value

    @field_validator("geography")
    @classmethod
    def _geography_is_reviewed_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.lower() not in reviewed_geography_labels():
            raise ValueError("geography must be one of the reviewed Portfolio Builder options")
        return stripped

    @field_validator("states")
    @classmethod
    def _states_are_reviewed_codes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        allowed = reviewed_state_codes()
        reviewed: list[str] = []
        for raw in value:
            code = (raw or "").strip().upper()
            if not re.fullmatch(r"[A-Z]{2}", code):
                raise ValueError("states must be reviewed two-letter USPS state codes")
            if allowed and code not in allowed:
                raise ValueError("states must be in the current reviewed geography footprint")
            if code not in reviewed:
                reviewed.append(code)
        if allowed and len(reviewed) == len(allowed):
            return None
        return reviewed or None

    @field_validator("occupancy")
    @classmethod
    def _occupancy_is_reviewed_label(cls, value: str | None) -> str | None:
        return _validate_optional_label(
            value,
            allowed=_OCCUPANCY_LABELS,
            field_name="occupancy",
        )

    @field_validator("lien_status")
    @classmethod
    def _lien_status_is_reviewed_label(cls, value: str | None) -> str | None:
        return _validate_optional_label(
            value,
            allowed=_LIEN_STATUS_LABELS,
            field_name="lien_status",
        )

    @field_validator("lender_relationship")
    @classmethod
    def _lender_relationship_is_reviewed_label(cls, value: str | None) -> str | None:
        return _validate_optional_label(
            value,
            allowed=_LENDER_RELATIONSHIP_LABELS,
            field_name="lender_relationship",
        )

    @field_validator("product")
    @classmethod
    def _product_is_reviewed_label(cls, value: str | None) -> str | None:
        return _validate_optional_label(value, allowed=_PRODUCT_LABELS, field_name="product")

    @field_validator("loan_product")
    @classmethod
    def _loan_product_is_reviewed_label(cls, value: str | None) -> str | None:
        return _normalise_reviewed_alias(
            value,
            allowed=_LOAN_PRODUCT_LABELS,
            aliases=_LOAN_PRODUCT_ALIASES,
            field_name="loan_product",
        )

    @field_validator("origination_channel")
    @classmethod
    def _origination_channel_is_reviewed_label(cls, value: str | None) -> str | None:
        return _normalise_reviewed_alias(
            value,
            allowed=_ORIGINATION_CHANNEL_LABELS,
            aliases=_ORIGINATION_CHANNEL_ALIASES,
            field_name="origination_channel",
        )

    @field_validator("min_equity_pct_label")
    @classmethod
    def _equity_label_is_reviewed_label(cls, value: str | None) -> str | None:
        return _validate_optional_label(
            value,
            allowed=_EQUITY_LABELS,
            field_name="min_equity_pct_label",
        )

    @field_validator("owner_link")
    @classmethod
    def _owner_link_is_reviewed_label(cls, value: str | None) -> str | None:
        return _validate_optional_label(
            value,
            allowed=_OWNER_LINK_LABELS,
            field_name="owner_link",
        )

    @field_validator("purchase_intent")
    @classmethod
    def _purchase_intent_is_reviewed_label(cls, value: str | None) -> str | None:
        return _normalise_reviewed_alias(
            value,
            allowed=_PURCHASE_INTENT_LABELS,
            aliases=_PURCHASE_INTENT_ALIASES,
            field_name="purchase_intent",
        )

    @field_validator("marketing_eligibility")
    @classmethod
    def _marketing_eligibility_is_reviewed_label(cls, value: str | None) -> str | None:
        return _normalise_reviewed_alias(
            value,
            allowed=_MARKETING_ELIGIBILITY_LABELS,
            aliases=_MARKETING_ELIGIBILITY_ALIASES,
            field_name="marketing_eligibility",
        )

    @field_validator("consent_status")
    @classmethod
    def _consent_status_is_reviewed_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        allowed = frozenset({"Opt-in", "Opt-out", "Unknown", "Any"})
        normalised = _normalise_reviewed_alias(
            stripped,
            allowed=allowed,
            aliases=_CONSENT_STATUS_ALIASES,
            field_name="consent_status",
        )
        if normalised is None:
            raise ValueError("consent_status must be one of the reviewed consent options")
        return normalised

    @field_validator("recency")
    @classmethod
    def _recency_is_reviewed_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        allowed = frozenset({"Untouched 30d", "Untouched 60d", "Untouched 90d", "Any"})
        normalised = _normalise_reviewed_alias(
            stripped,
            allowed=allowed,
            aliases=_RECENCY_ALIASES,
            field_name="recency",
        )
        if normalised is None:
            raise ValueError("recency must be one of the reviewed recency options")
        return normalised

    @field_validator("target_lender_ref")
    @classmethod
    def _target_lender_ref_is_public_safe(cls, value: str | None) -> str | None:
        try:
            return normalize_public_lender_ref(value, allow_all=True)
        except ValueError as exc:
            raise ValueError("target_lender_ref must be a public-safe lender alias") from exc

    def has_effective_predicate(self, *, count_default_marketing: bool = True) -> bool:
        """True when the reviewed labels would compile to a real SQL predicate."""

        geography = (self.geography or "").strip().lower()
        if self.states:
            return True
        if geography and not (
            geography == "all" or (geography.startswith("all ") and geography.endswith(" states"))
        ):
            return True
        if self.occupancy in {"Owner-occupied", "Non-owner-occupied"}:
            return True
        lien_status = (self.lien_status or "").strip().lower()
        if lien_status in {
            "free & clear",
            "free and clear",
            "open 1st lien",
            "open first lien",
            "open heloc",
        }:
            return True
        owner_link = (self.owner_link or "").strip().lower()
        if owner_link in {
            "single-property owner",
            "multi-property (2-4)",
            "portfolio investor (5+)",
        }:
            return True
        purchase_intent = (self.purchase_intent or "").strip().lower()
        if purchase_intent in {"listed for sale", "heloc intent", "both"}:
            return True
        relationship = (self.lender_relationship or "").strip().lower()
        if relationship in {
            "current customer",
            "former customer",
            "competitor customer",
            "competitor",
        }:
            return True
        target_lender_ref = (self.target_lender_ref or "").strip().lower()
        if target_lender_ref and target_lender_ref != "all":
            return True
        if self.product and self.product != "All products":
            return True
        if self.loan_product and self.loan_product != "All loan products":
            return True
        if self.origination_channel and self.origination_channel != "All channels":
            return True
        if self.min_equity_pct is not None and self.min_equity_pct > 0:
            return True
        if self.marketing_eligibility == "Suppressed only":
            return True
        if count_default_marketing and self.marketing_eligibility == "Eligible only":
            return True
        if self.consent_status in {"Opt-in", "Opt-out", "Unknown"}:
            return True
        if self.recency in {"Untouched 30d", "Untouched 60d", "Untouched 90d"}:
            return True
        return self.min_equity_pct_label in {"≥ 15%", "≥ 25%", "≥ 40%"}


class PortfolioPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criteria: PortfolioCriteria = Field(default_factory=PortfolioCriteria)


bind_portfolio_criteria(PortfolioCriteria)


HouseholdDedupeUnit = Literal["borrower", "household"]
HouseholdPrimaryStrategy = Literal["highest_opportunity_eligible"]


def _household_source_assets() -> list[str]:
    return ["mip.gold.household_rollup", "mip.gold.borrower_360"]


class HouseholdDedupConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    dedupe_unit: HouseholdDedupeUnit = "borrower"
    primary_contact_strategy: HouseholdPrimaryStrategy = "highest_opportunity_eligible"

    @model_validator(mode="after")
    def _normalize_dedupe_unit(self) -> "HouseholdDedupConfig":
        self.dedupe_unit = "household" if self.enabled else "borrower"
        return self


class HouseholdDedupSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    candidate_borrower_count: int = 0
    selected_primary_count: int = 0
    suppressed_co_owner_count: int = 0
    household_count: int = 0
    owner_link_household_count: int = 0
    mailing_address_household_count: int = 0
    singleton_household_count: int = 0
    primary_contact_strategy: HouseholdPrimaryStrategy = "highest_opportunity_eligible"
    source_assets: list[str] = Field(default_factory=_household_source_assets)

    @field_validator(
        "candidate_borrower_count",
        "selected_primary_count",
        "suppressed_co_owner_count",
        "household_count",
        "owner_link_household_count",
        "mailing_address_household_count",
        "singleton_household_count",
    )
    @classmethod
    def _validate_count(cls, value: int) -> int:
        if isinstance(value, bool) or value < 0 or value > 10_000_000:
            raise ValueError("household counts must be bounded non-negative integers")
        return int(value)

    @field_validator("source_assets")
    @classmethod
    def _validate_source_assets(cls, value: list[str]) -> list[str]:
        if not value or len(value) > 5:
            raise ValueError("source_assets must cite bounded governed UC assets")
        normalized = [str(item).strip() for item in value]
        for asset in normalized:
            if asset not in {"mip.gold.household_rollup", "mip.gold.borrower_360"}:
                raise ValueError("source_assets must cite household_rollup and borrower_360 only")
        return normalized


class PortfolioCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Portfolio build", min_length=1, max_length=80)
    criteria: PortfolioCriteria = Field(default_factory=PortfolioCriteria)
    suppression_policy: dict[str, object] = Field(default_factory=dict)
    message_variants: list[dict[str, object]] = Field(default_factory=list)
    channel_cascade: list[dict[str, object]] = Field(default_factory=list)
    send_window: dict[str, object] = Field(default_factory=dict)
    holdout: dict[str, object] | None = None
    roi_assumptions: dict[str, object] | None = None
    household_dedup: HouseholdDedupConfig = Field(default_factory=HouseholdDedupConfig)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return assert_public_campaign_text(value, field_name="campaign name", max_length=80)

    @field_validator("message_variants")
    @classmethod
    def _validate_message_variants(
        cls,
        value: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        if len(value) > 12:
            raise ValueError("message_variants supports at most 12 variants per campaign")
        normalized: list[dict[str, object]] = []
        seen_names: set[str] = set()
        for raw in value:
            raw_name = raw.get("variant_name", raw.get("name"))
            if raw_name is None or not str(raw_name).strip():
                raise ValueError("message variant_name must be nonblank")
            name = assert_public_campaign_text(
                raw_name,
                field_name="variant_name",
                max_length=64,
            )
            name_key = name.casefold()
            if name_key in seen_names:
                raise ValueError("message variant_name values must be unique")
            seen_names.add(name_key)
            channel = str(raw.get("channel") or "email").strip()
            if channel not in _OUTREACH_CHANNELS:
                raise ValueError("message variant channel must be email, sms, or direct_mail")
            subject = assert_public_campaign_text(
                raw.get("subject") or "",
                field_name="variant subject",
                max_length=120,
            )
            body = assert_public_campaign_text(
                raw.get("body") or "",
                field_name="variant body",
                max_length=1000,
            )
            if not body:
                raise ValueError("variant body must be nonblank")
            if channel == "email" and not subject:
                raise ValueError("email message variants require a nonblank subject")
            assert_borrower_campaign_copy(subject, field_name="variant subject")
            assert_borrower_campaign_copy(body, field_name="variant body")
            generation_mode = str(raw.get("generation_mode") or "operator").strip()
            if generation_mode not in {"supervisor", "reviewed_fallback", "operator"}:
                raise ValueError(
                    "variant generation_mode must be supervisor, reviewed_fallback, or operator"
                )
            generator_label = assert_public_campaign_text(
                raw.get("generator_label") or "Operator edited",
                field_name="variant generator_label",
                max_length=80,
            )
            provenance_token_raw = raw.get("provenance_token")
            provenance_token = (
                str(provenance_token_raw).strip() if provenance_token_raw is not None else None
            )
            if provenance_token is not None and not 32 <= len(provenance_token) <= 4096:
                raise ValueError("variant provenance_token must be a bounded server-issued token")
            weight_raw = raw.get("weight_pct", 100)
            try:
                weight = float(weight_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("variant weight_pct must be numeric") from exc
            if weight < 0 or weight > 100:
                raise ValueError("variant weight_pct must be between 0 and 100")
            normalized.append(
                {
                    "variant_name": name,
                    "channel": channel,
                    "subject": subject,
                    "body": body,
                    "weight_pct": weight,
                    "generation_mode": generation_mode,
                    "generator_label": generator_label,
                    "provenance_token": provenance_token,
                }
            )
        ab_names = {str(item["variant_name"]).casefold() for item in normalized}
        if ab_names & {"a", "b"} and not {"a", "b"}.issubset(ab_names):
            raise ValueError("A/B message variants require complete A and B variants")
        return normalized

    @field_validator("channel_cascade")
    @classmethod
    def _validate_channel_cascade(
        cls,
        value: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        if len(value) > 6:
            raise ValueError("channel_cascade supports at most 6 steps")
        seen_steps: set[int] = set()
        normalized: list[dict[str, object]] = []
        for raw in value:
            channel = str(raw.get("channel") or "").strip()
            if channel not in _OUTREACH_CHANNELS:
                raise ValueError("channel cascade channel must be email, sms, or direct_mail")
            try:
                step = int(raw.get("step") or 0)
                after_days = int(raw.get("after_days") or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError("channel cascade step and after_days must be integers") from exc
            if step <= 0 or step in seen_steps:
                raise ValueError("channel cascade steps must be positive and unique")
            if after_days < 0 or after_days > 365:
                raise ValueError("channel cascade after_days must be between 0 and 365")
            seen_steps.add(step)
            normalized.append({"channel": channel, "step": step, "after_days": after_days})
        return sorted(normalized, key=lambda item: int(item["step"]))

    @field_validator("send_window")
    @classmethod
    def _validate_send_window(cls, value: dict[str, object]) -> dict[str, object]:
        if not value:
            return value
        days_raw = value.get("days") or []
        if not isinstance(days_raw, list):
            raise ValueError("send_window.days must be a list")
        days: list[str] = []
        for raw_day in days_raw:
            day = str(raw_day).strip()
            if not day:
                continue
            days.append(_SEND_WINDOW_DAY_ALIASES.get(day.lower(), day))
        if not days or any(day not in _SEND_WINDOW_DAYS for day in days):
            raise ValueError("send_window.days must use reviewed day labels")
        start = str(value.get("start_local") or value.get("start") or "").strip()
        end = str(value.get("end_local") or value.get("end") or "").strip()
        if not re.fullmatch(r"\d{2}:\d{2}", start) or not re.fullmatch(r"\d{2}:\d{2}", end):
            raise ValueError("send_window start/end must be HH:MM")
        if start >= end:
            raise ValueError("send_window start_local must be before end_local")
        timezone = str(value.get("timezone") or "borrower_local").strip()
        assert_public_campaign_text(timezone, field_name="send_window timezone", max_length=64)
        return {
            "days": days,
            "timezone": timezone,
            "start_local": start,
            "end_local": end,
        }

    @field_validator("holdout")
    @classmethod
    def _validate_holdout(cls, value: dict[str, object] | None) -> dict[str, object] | None:
        if value is None:
            return None
        method = str(value.get("method") or "hash_modulo").strip()
        if method != "hash_modulo":
            raise ValueError("holdout.method must be hash_modulo")
        try:
            size_pct = float(value.get("size_pct", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("holdout.size_pct must be numeric") from exc
        if size_pct < 0 or size_pct > 50:
            raise ValueError("holdout.size_pct must be between 0 and 50")
        return {"method": method, "size_pct": size_pct}

    @field_validator("suppression_policy")
    @classmethod
    def _validate_public_json(cls, value: dict[str, object] | None) -> dict[str, object] | None:
        if value is None:
            return None
        assert_public_campaign_json(value, field_name="campaign metadata")
        return value

    @field_validator("roi_assumptions")
    @classmethod
    def _validate_roi_assumptions(
        cls,
        value: dict[str, object] | None,
    ) -> dict[str, object] | None:
        if value is None:
            return None
        assert_public_campaign_json(value, field_name="campaign ROI assumptions")

        def require_finite(number: float, *, field_name: str) -> float:
            if not math.isfinite(number):
                raise ValueError(f"{field_name} must be finite")
            return number

        def reject_non_finite_numbers(item: object, *, field_name: str) -> None:
            if isinstance(item, dict):
                for key, nested in item.items():
                    reject_non_finite_numbers(
                        nested,
                        field_name=f"{field_name}.{key}",
                    )
            elif isinstance(item, list):
                for index, nested in enumerate(item):
                    reject_non_finite_numbers(
                        nested,
                        field_name=f"{field_name}[{index}]",
                    )
            elif isinstance(item, int | float) and not isinstance(item, bool):
                require_finite(float(item), field_name=field_name)

        reject_non_finite_numbers(value, field_name="roi_assumptions")
        numeric_keys = {
            "budget_usd",
            "expected_conversion_rate",
            "expected_conversion_rate_pct",
            "lo_capacity",
        }
        normalized = dict(value)
        for key in numeric_keys & normalized.keys():
            # Re-audit #3 follow-up (2026-06-12, observed live): the builder's
            # Budget field is optional and the client sends budget_usd: null
            # when it is left blank — float(None) raised TypeError and the
            # whole save 422'd. An explicit null is "not provided", exactly
            # like omitting the key; drop it instead of rejecting the save.
            if normalized[key] is None:
                normalized.pop(key)
                continue
            try:
                numeric = require_finite(
                    float(normalized[key]),
                    field_name=f"roi_assumptions.{key}",
                )
            except (TypeError, ValueError) as exc:
                if "must be finite" in str(exc):
                    raise
                raise ValueError(f"roi_assumptions.{key} must be numeric") from exc
            if numeric < 0:
                raise ValueError(f"roi_assumptions.{key} must be non-negative")
            if (
                key in {"expected_conversion_rate", "expected_conversion_rate_pct"}
                and numeric > 100
            ):
                raise ValueError(f"roi_assumptions.{key} must be 100 or less")
            normalized[key] = numeric
        cost = normalized.get("cost_per_contact_usd")
        if isinstance(cost, dict):
            checked: dict[str, float] = {}
            for channel, amount in cost.items():
                channel_key = str(channel).strip()
                if channel_key not in _OUTREACH_CHANNELS:
                    raise ValueError("roi_assumptions.cost_per_contact_usd uses unknown channel")
                if amount is None:
                    continue
                try:
                    numeric = require_finite(
                        float(amount),
                        field_name=(
                            "roi_assumptions.cost_per_contact_usd " f"value for {channel_key}"
                        ),
                    )
                except (TypeError, ValueError) as exc:
                    if "must be finite" in str(exc):
                        raise
                    raise ValueError(
                        "roi_assumptions.cost_per_contact_usd values must be numeric"
                    ) from exc
                if numeric < 0:
                    raise ValueError(
                        "roi_assumptions.cost_per_contact_usd values must be non-negative"
                    )
                checked[channel_key] = numeric
            normalized["cost_per_contact_usd"] = checked
        elif cost is None:
            normalized.pop("cost_per_contact_usd", None)
        elif cost is not None:
            try:
                numeric_cost = require_finite(
                    float(cost),
                    field_name="roi_assumptions.cost_per_contact_usd",
                )
            except (TypeError, ValueError) as exc:
                if "must be finite" in str(exc):
                    raise
                raise ValueError("roi_assumptions.cost_per_contact_usd must be numeric") from exc
            if numeric_cost < 0:
                raise ValueError("roi_assumptions.cost_per_contact_usd must be non-negative")
            normalized["cost_per_contact_usd"] = numeric_cost
        return normalized


class PortfolioCreateResponse(BaseModel):
    portfolio_id: str
    campaign_id: str | None = None
    name: str
    marketable_population: int
    household_summary: HouseholdDedupSummary = Field(default_factory=HouseholdDedupSummary)
    audit_event_id: str | None = None


class CampaignSummary(BaseModel):
    campaign_id: str
    name: str
    owner_email: str
    status: CampaignStatus
    criteria: dict[str, object]
    suppression_policy: dict[str, object] = Field(default_factory=dict)
    message_variants: list[dict[str, object]] = Field(default_factory=list)
    channel_cascade: list[dict[str, object]] = Field(default_factory=list)
    send_window: dict[str, object] = Field(default_factory=dict)
    holdout: dict[str, object] | None = None
    roi_assumptions: dict[str, object] | None = None
    household_dedup: HouseholdDedupConfig = Field(default_factory=HouseholdDedupConfig)
    household_summary: HouseholdDedupSummary = Field(default_factory=HouseholdDedupSummary)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CampaignListResponse(BaseModel):
    campaigns: list[CampaignSummary]

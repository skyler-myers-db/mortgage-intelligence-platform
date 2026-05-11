from datetime import datetime
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.services.pii_redaction import normalize_public_lender_ref

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
_EQUITY_LABELS: frozenset[str] = frozenset({"≥ 15%", "≥ 25%", "≥ 40%", "Any"})
_OWNER_LINK_LABELS: frozenset[str] = frozenset(
    {"All", "Single-property owner", "Multi-property (2-4)", "Portfolio investor (5+)"}
)
_PURCHASE_INTENT_LABELS: frozenset[str] = frozenset(
    {"All", "Listed for sale", "Recent permit activity", "Both"}
)


def _allowed_geography_labels() -> set[str]:
    from backend.services.state_footprint import get_state_footprint_resolver

    resolver = get_state_footprint_resolver()
    if resolver.using_fallback():
        return {"all"}
    states = resolver.list()
    labels = {"all", *(s.state_name.lower() for s in states)}
    labels.add(f"all {len(states)} states")
    return labels


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
    # Headline KPIs — ALL from mip.gold.borrower_360 / funnel_snapshot_daily,
    # which are derived from Cotality Delta Share + public FRED data. No
    # lender CRM / campaign / app-activity signal contributes to these.
    marketable_population: int
    high_intent_leads: int
    top_tier_opportunities: int | None = None  # opportunity_score >= 75
    offers_recommended: int | None = None       # recommended_offer_code != 'nurture'
    avg_score: int | None = Field(default=None, ge=0, le=100)
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
    target_lender_ref: str | None = None
    min_equity_pct: float | None = None
    owner_link: str | None = None
    purchase_intent: str | None = None
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
        if stripped.lower() not in _allowed_geography_labels():
            raise ValueError("geography must be one of the reviewed Portfolio Builder options")
        return stripped

    @field_validator("states")
    @classmethod
    def _states_are_reviewed_codes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        from backend.services.state_footprint import get_state_footprint_resolver

        resolver = get_state_footprint_resolver()
        allowed = {s.state_code for s in resolver.list()} if not resolver.using_fallback() else set()
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
        return _validate_optional_label(
            value,
            allowed=_PURCHASE_INTENT_LABELS,
            field_name="purchase_intent",
        )

    @field_validator("target_lender_ref")
    @classmethod
    def _target_lender_ref_is_public_safe(cls, value: str | None) -> str | None:
        try:
            return normalize_public_lender_ref(value, allow_all=True)
        except ValueError as exc:
            raise ValueError("target_lender_ref must be a public-safe lender alias") from exc

    def has_effective_predicate(self) -> bool:
        """True when the reviewed labels would compile to a real SQL predicate."""

        geography = (self.geography or "").strip().lower()
        if self.states:
            return True
        if geography and not (geography == "all" or (geography.startswith("all ") and geography.endswith(" states"))):
            return True
        if self.occupancy in {"Owner-occupied", "Non-owner-occupied"}:
            return True
        lien_status = (self.lien_status or "").strip().lower()
        if lien_status in {"free & clear", "free and clear", "open 1st lien", "open first lien", "open heloc"}:
            return True
        owner_link = (self.owner_link or "").strip().lower()
        if owner_link in {"single-property owner", "multi-property (2-4)", "portfolio investor (5+)"}:
            return True
        purchase_intent = (self.purchase_intent or "").strip().lower()
        if purchase_intent in {"listed for sale", "recent permit activity", "both"}:
            return True
        relationship = (self.lender_relationship or "").strip().lower()
        if relationship in {"current customer", "former customer", "competitor customer", "competitor"}:
            return True
        target_lender_ref = (self.target_lender_ref or "").strip().lower()
        if target_lender_ref and target_lender_ref != "all":
            return True
        if self.product and self.product != "All products":
            return True
        if self.min_equity_pct is not None and self.min_equity_pct > 0:
            return True
        return self.min_equity_pct_label in {"≥ 15%", "≥ 25%", "≥ 40%"}


class PortfolioPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criteria: PortfolioCriteria = Field(default_factory=PortfolioCriteria)


class PortfolioCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Portfolio build", min_length=1, max_length=80)
    criteria: PortfolioCriteria = Field(default_factory=PortfolioCriteria)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        cleaned = re.sub(r"\s+", " ", value.strip())
        if not cleaned:
            raise ValueError("name is required")
        if len(cleaned) > 80:
            raise ValueError("name must be 80 characters or fewer")
        pii_patterns = (
            r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
            r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b",
            r"\b\d{9,}\b",
            r"\b\d{1,6}\s+[A-Za-z0-9.'-]+\s+(?:st|street|ave|avenue|rd|road|dr|drive|ln|lane|blvd|boulevard|ct|court|way)\b",
            r"\b(?:raw[_\s-]?clip|owner[_\s-]?name|street[_\s-]?address|mailing[_\s-]?address)\b",
        )
        if any(re.search(pattern, cleaned, re.IGNORECASE) for pattern in pii_patterns):
            raise ValueError("name cannot contain PII, raw identifiers, or street addresses")
        if re.search(r"\b[A-Z][a-z]{1,30}\s+(?:[A-Z]\s+)?[A-Z][a-z]{1,30}\b", cleaned):
            raise ValueError("name cannot contain PII, raw identifiers, or street addresses")
        return cleaned


class PortfolioCreateResponse(BaseModel):
    portfolio_id: str
    name: str
    marketable_population: int
    audit_event_id: str | None = None

"""Module 0 canonical scoring primitives (Python parity with SQL).

Each function here mirrors a Unity Catalog UDF and is pinned by a
golden-fixture JSON that the SQL side validates against the same inputs:

- ``lead_score``         -> ``sql/uc_functions/fn_lead_score.sql``
                            + ``tests/fixtures/lead_score_golden.json``
                            (case_05 / case_07 lock banker's rounding;
                            case_13 locks exact-decimal arithmetic in the
                            float-drift zone).
- ``rate_spread_bps``    -> ``sql/uc_functions/fn_rate_spread.sql``
                            + ``tests/fixtures/rate_spread_golden.json``
                            (case_05 pins round-half-to-even at 162.5).
- ``estimated_upb``      -> ``sql/uc_functions/fn_estimated_upb.sql``
                            + ``tests/fixtures/estimated_upb_golden.json``
                            (case_04 pins unknown-rate fallback; case_06
                            pins near-payoff behavior).
- ``estimated_upb_confidence_band``
                         -> ``sql/uc_functions/fn_estimated_upb_confidence_band.sql``
                            + ``tests/fixtures/estimated_upb_confidence_band_golden.json``
                            (pins lower/point/upper range from bounded
                            mortgage-rate uncertainty).
- ``in_the_money``       -> ``sql/uc_functions/fn_in_the_money.sql``
                            + ``tests/fixtures/in_the_money_golden.json``
                            (cases 04/05 pin the inclusive ``>=`` boundary).
- ``next_best_offer``    -> ``sql/uc_functions/fn_next_best_offer.sql``
                            + ``tests/fixtures/next_best_offer_golden.json``
                            (case_12 pins the fixture refi_plus_heloc shift;
                            cases 09/10 pin the HELOC-equity boundary).
- ``loan_product_type``  -> ``sql/uc_functions/fn_loan_product_type.sql``
                            + ``tests/fixtures/loan_product_type_golden.json``
                            (boundary case pins "exactly at the conforming
                            limit is conforming, not jumbo"; NULL code
                            returns None -- unknown never guesses).
- ``refi_propensity_heuristic``
                         -> ``sql/uc_functions/fn_refi_propensity_heuristic.sql``
                            + ``tests/fixtures/refi_propensity_heuristic_golden.json``
                            (S1.3 transparent points table; boundary cases pin
                            every published band edge).

Weights for ``lead_score`` (non-negotiable): 0.35 / 0.30 / 0.15 / 0.10 / 0.10.
NULL (``None``) score components coerce to 0. ``in_the_money`` returns
``False`` on any NULL input. ``next_best_offer`` coerces missing borrower
signals to 0/FALSE, but returns ``nurture`` when any threshold is NULL so
misconfigured decisioning cannot become a positive outreach lane.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

_INT32_MIN = -2_147_483_648
_INT32_MAX = 2_147_483_647
_STANDARD_MORTGAGE_TERM_MONTHS = 360
_MAX_AMORTIZATION_RATE = 10.0
_MIN_BOUNDED_MORTGAGE_RATE = 0.01
_MAX_BOUNDED_MORTGAGE_RATE = 0.15

# fn_lead_score weights as EXACT decimals. Spark parses the SQL literals
# (0.35 etc.) as DECIMAL, so the UDF computes the weighted sum exactly and
# BROUNDs the exact value. Python float arithmetic drifts on ~0.67% of the
# input lattice (e.g. (92,94,94,85,25): exact sum 85.5 -> 86, float sum
# 85.49999999999999 -> 85), which surfaced as false "integrity gap" warnings
# in the borrower proof drawer (2026-06-11 audit, P1-1). Decimal weights are
# the parity fix; tests/unit/test_scoring.py sweeps the full boundary.
_LEAD_SCORE_WEIGHTS = (
    Decimal("0.35"),  # economic_incentive
    Decimal("0.30"),  # intent_trigger
    Decimal("0.15"),  # fit
    Decimal("0.10"),  # relationship
    Decimal("0.10"),  # evidence
)
_DECIMAL_ONE = Decimal("1")

# ---------------------------------------------------------------------------
# Canonical opportunity-score threshold + display bands (S1).
# ---------------------------------------------------------------------------
# THE single pinned backend site for the high-opportunity threshold and the
# score display-band edges. Parity mirrors (change all together, never one
# alone):
#   - sql/uc_functions/fn_high_opportunity.sql   (UC layer, threshold)
#   - sql/uc_functions/fn_score_band.sql         (UC layer, band edges)
#   - frontend/src/lib/opportunityScore.ts       (frontend layer)
# tests/unit/test_score_threshold_guard.py greps the repo so a stray literal
# predicate fails CI and asserts the three layers agree; golden edge cases
# (74/75/76, 84/85/86, 64/65/66) live in tests/fixtures/score_band_golden.json.
# Every other backend consumer MUST import these — never re-declare the
# numbers.
HIGH_OPPORTUNITY_THRESHOLD: int = 75
SCORE_BAND_HIGH_MIN: int = 85
SCORE_BAND_MED_MIN: int = 65


def is_high_opportunity(opportunity_score: int | None) -> bool:
    """Return True iff the score clears the governed high-opportunity threshold.

    Mirrors ``mip.gold.fn_high_opportunity`` EXACTLY: ``None`` returns
    ``False`` (no score must not silently count as a top-tier opportunity
    on a contact-prioritization surface — same fail-closed rationale as
    ``in_the_money``).
    """
    if opportunity_score is None:
        return False
    return opportunity_score >= HIGH_OPPORTUNITY_THRESHOLD


def score_band(opportunity_score: int | None) -> str:
    """Return the canonical display band: ``'high'`` / ``'med'`` / ``'low'``.

    Mirrors ``mip.gold.fn_score_band`` EXACTLY (and the prototype ScoreBadge
    tiers): high at ``>= SCORE_BAND_HIGH_MIN``, med at ``>= SCORE_BAND_MED_MIN``,
    else low. ``None`` coerces to 0 (a completely-unscored row is 'low'),
    matching the SQL ``COALESCE(..., 0)`` contract.
    """
    score = opportunity_score or 0
    if score >= SCORE_BAND_HIGH_MIN:
        return "high"
    if score >= SCORE_BAND_MED_MIN:
        return "med"
    return "low"

# Canonical SQL-parity labels for the eight offer_codes. These mirror the
# governed fixture and are intentionally stable for scoring/audit contracts.
# User-facing APIs should call ``offer_display_label`` so legacy labels such as
# "Purchase Mortgage" do not leak into screens or outreach review.
NBO_PRODUCT_LABELS: dict[str, str] = {
    "purchase": "Purchase Mortgage",
    "refi_plus_heloc": "Refinance + HELOC",
    "heloc": "HELOC",
    "refi": "Refinance",
    "cash_out": "Cash-out Refi",
    "investor": "Investor Product",
    "retention": "Retention",
    "nurture": "Nurture",
}

# Canonical loan product-type vocabulary emitted by fn_loan_product_type /
# gold.borrower_360.loan_product_type. Order matters: it is the reviewed
# filter-option order on the segments / lead-queue surfaces.
LOAN_PRODUCT_TYPES: tuple[str, ...] = ("conventional", "jumbo", "fha", "va", "other")

LOAN_PRODUCT_DISPLAY_LABELS: dict[str, str] = {
    "conventional": "Conventional",
    "jumbo": "Jumbo",
    "fha": "FHA",
    "va": "VA",
    "other": "Other",
}

# Origination-channel vocabulary from the governed first-party LOS feed
# (mip.first_party.loan_applications.application_channel; funded rows only).
# NULL gold values render as "Unknown" -- the app never invents a channel.
ORIGINATION_CHANNELS: tuple[str, ...] = ("loan_officer", "digital", "branch", "call_center")

ORIGINATION_CHANNEL_DISPLAY_LABELS: dict[str, str] = {
    "loan_officer": "Loan officer",
    "digital": "Digital",
    "branch": "Branch",
    "call_center": "Call center",
}

OFFER_DISPLAY_LABELS: dict[str, str] = {
    "purchase": "Next-home purchase loan",
    "refi_plus_heloc": "Refinance + home-equity review",
    "heloc": "Home-equity line review",
    "refi": "Refinance review",
    "cash_out": "Cash-out refinance review",
    "investor": "Investor financing review",
    "retention": "Customer retention review",
    "nurture": "Monitor for later",
}


def offer_display_label(code: str | None, fallback: str | None = None) -> str:
    normalized = str(code or "").strip().lower()
    if normalized in OFFER_DISPLAY_LABELS:
        return OFFER_DISPLAY_LABELS[normalized]
    return fallback or "Mortgage review"


# ---------------------------------------------------------------------------
# Source-label registry (2026-04-22 persona-review fix).
# -----------------------------------------------------------------------------
# Maps the raw Unity Catalog object names we cite as evidence (and the
# legacy short aliases the LeadTable still hard-codes) to the business-
# friendly labels that render on compliance-visible surfaces. The drawer
# lineage link continues to use the raw UC name so a compliance reviewer
# can still trace to the exact object; only the chip text changes.
#
# Usage:
#
#     from backend.services.scoring import source_display_label
#     label = source_display_label("mip.gold.fn_in_the_money")
#     # -> "Refinance economics screen"
#
# Unknown entries fall back to the last dotted segment (the pre-existing
# `shortSourceLabel` behaviour) so adding a new UC object is non-breaking.
# ---------------------------------------------------------------------------
SOURCE_DISPLAY_LABELS: dict[str, str] = {
    # UC function evidence
    "mip.gold.fn_rate_spread":      "Market rate comparison",
    "mip.gold.fn_bounded_mortgage_rate": "Mortgage rate quality bound",
    "mip.gold.fn_estimated_upb":    "Estimated UPB amortization",
    "mip.gold.fn_estimated_upb_confidence_band": "Estimated UPB confidence band",
    "mip.gold.fn_in_the_money":     "Refinance economics screen",
    "mip.gold.fn_next_best_offer":  "Primary offer rules",
    "mip.gold.fn_lead_score":       "Opportunity score",
    "mip.gold.fn_high_opportunity": "High-opportunity threshold",
    "mip.gold.fn_score_band":       "Opportunity score band",
    "mip.gold.fn_loan_product_type": "Loan product classification",
    # UC table evidence
    "mip.gold.borrower_360":        "Borrower dossier",
    "mip.gold.borrower_dossier":    "Borrower dossier",
    "mip.gold.lead_population":     "Ranked lead population",
    "mip.gold.lead_scores":         "Lead scores",
    "mip.gold.evidence_events":     "Evidence stream",
    "mip.gold.property_owner_bridge": "Owner Link bridge",
    "mip.silver.listing_activity":  "MLS listing activity",
    "mip.silver.heloc_propensity":  "HELOC propensity",
    "mip.silver.refi_propensity":   "Refi propensity",
    "mip.semantics.portfolio_headline_metric_view": "Portfolio headline metrics",
    "mip.first_party.loan_applications": "First-party loan applications",
    # Short aliases used on RowPreview and app proof chips.
    "fn_rate_spread":               "Market rate comparison",
    "fn_bounded_mortgage_rate":     "Mortgage rate quality bound",
    "fn_estimated_upb":             "Estimated UPB amortization",
    "fn_estimated_upb_confidence_band": "Estimated UPB confidence band",
    "fn_in_the_money":              "Refinance economics screen",
    "fn_next_best_offer":           "Primary offer rules",
    "fn_lead_score":                "Opportunity score",
    "fn_loan_product_type":         "Loan product classification",
    "loan_applications":            "First-party loan applications",
    "rules.itm_v3":                 "Refinance economics screen",
    "mlflow.mtg_nbo_v3":            "Primary offer rules v3",
    "permits.building":             "Building permit signal",
    "listing_activity":             "MLS listing activity",
    "heloc_propensity":             "HELOC propensity",
    "refi_propensity":              "Refi propensity",
    "borrower_dossier":             "Borrower dossier",
}


def source_display_label(name: str | None) -> str:
    """Return the business-friendly label for a UC source name.

    Unknown names fall back to the last dotted segment (e.g.
    ``mip.silver.property_master`` -> ``"property_master"``). ``None`` or
    empty input returns an empty string so callers can pass raw data
    through without guarding.

    Multi-catalog note: the registry keys embed the default ``mip.*``
    prefix for legacy compatibility, but we retry the lookup against the
    ``schema.object`` suffix (e.g. ``gold.fn_in_the_money``) so a
    customer deploying with ``mip_prod.*`` still resolves the business
    label instead of leaking the raw object name.
    """
    if not name:
        return ""
    if name in SOURCE_DISPLAY_LABELS:
        return SOURCE_DISPLAY_LABELS[name]
    # Multi-catalog fallback: strip the leading catalog token and re-try
    # the lookup against ``schema.object`` (or just ``object`` for the
    # short-alias bucket). This keeps business labels stable even when
    # the workspace catalog is ``mip_prod`` / ``lender_uc`` / etc.
    parts = name.split(".")
    if len(parts) >= 2:
        schema_object = ".".join(parts[-2:])  # e.g. "gold.fn_in_the_money"
        prefixed = f"mip.{schema_object}"      # e.g. "mip.gold.fn_in_the_money"
        if prefixed in SOURCE_DISPLAY_LABELS:
            return SOURCE_DISPLAY_LABELS[prefixed]
    # Fall back to the frontend's legacy behaviour so an unmapped source
    # still renders something reasonable rather than the full FQN.
    last = name.rsplit(".", 1)[-1]
    if last in SOURCE_DISPLAY_LABELS:
        return SOURCE_DISPLAY_LABELS[last]
    return last


def lead_score(
    economic_incentive: int | None,
    intent_trigger: int | None,
    fit: int | None,
    relationship: int | None,
    evidence: int | None,
) -> int:
    """Return the integer opportunity score in [0, 100].

    Mirrors ``mip.gold.fn_lead_score`` EXACTLY: the SQL literals are
    DECIMAL in Spark, so the UDF computes the weighted sum in exact
    decimal arithmetic and ``BROUND``s (half-to-even) the exact value.
    Python must therefore use ``decimal.Decimal`` — ``round()`` on a
    binary float bankers-rounds a *different number* on exact-.5
    boundaries (float drift), which diverged from SQL on ~0.67% of the
    input lattice and rendered false integrity-gap warnings in the
    borrower proof drawer. Any ``None`` component is treated as 0 to
    match the SQL ``COALESCE(..., 0)`` contract.
    """
    components = (economic_incentive, intent_trigger, fit, relationship, evidence)
    weighted_sum = sum(
        (weight * Decimal(component or 0)
         for weight, component in zip(_LEAD_SCORE_WEIGHTS, components, strict=True)),
        start=Decimal(0),
    )
    rounded = int(weighted_sum.quantize(_DECIMAL_ONE, rounding=ROUND_HALF_EVEN))
    return max(0, min(100, rounded))


def rate_spread_bps(
    current_rate: float | None,
    market_rate: float | None,
) -> int:
    """Return basis-point spread of ``current_rate`` over ``market_rate``.

    Mirrors ``mip.gold.fn_rate_spread``. Rates are expressed as
    fractions (0.0575 == 5.75%), matching the SQL signature. A ``None``
    on either side returns 0 ("no signal == no opportunity", keeps
    downstream columns NOT NULL so ``fn_in_the_money``'s ``>=`` does not
    short-circuit). Result is rounded via Python ``round()`` for
    banker's-rounding parity with ``BROUND`` and clipped to INT32 range
    to mirror the SQL ``CAST AS INT``.
    """
    if current_rate is None or market_rate is None:
        return 0
    return max(_INT32_MIN, min(_INT32_MAX, round((current_rate - market_rate) * 10000)))


def estimated_upb(
    original_upb: int | float | None,
    estimated_rate: float | None,
    months_elapsed: int | None,
) -> int:
    """Return the amortized current unpaid principal balance estimate.

    Mirrors ``mip.gold.fn_estimated_upb``. Inputs are original first-lien
    UPB in dollars, annual note-rate estimate as a fraction, and elapsed
    whole months since origination. Module 0 uses a governed 30-year
    amortization convention because the primitive signature is intentionally
    limited to the three source fields available across the Cotality lien
    feed. Missing/invalid rates fall back to straight-line amortization,
    which is conservative and deterministic for unknown-rate rows. Missing
    original UPB returns 0; missing/negative elapsed months are treated as
    0; months at or beyond term return 0. The final dollar amount is
    banker's-rounded to stay in parity with SQL ``BROUND``.
    """
    import math

    if original_upb is None:
        return 0
    principal = float(original_upb)
    if not math.isfinite(principal) or principal <= 0:
        return 0

    try:
        elapsed_raw = 0 if months_elapsed is None else int(months_elapsed)
    except (TypeError, ValueError):
        elapsed_raw = 0
    elapsed = max(0, min(_STANDARD_MORTGAGE_TERM_MONTHS, elapsed_raw))
    if elapsed >= _STANDARD_MORTGAGE_TERM_MONTHS:
        return 0

    try:
        rate = None if estimated_rate is None else float(estimated_rate)
    except (TypeError, ValueError):
        rate = None

    def straight_line_balance() -> float:
        return principal * (_STANDARD_MORTGAGE_TERM_MONTHS - elapsed) / _STANDARD_MORTGAGE_TERM_MONTHS

    if rate is None or not math.isfinite(rate) or rate <= 0 or rate > _MAX_AMORTIZATION_RATE:
        balance = straight_line_balance()
    else:
        monthly_rate = rate / 12.0
        if not math.isfinite(monthly_rate) or monthly_rate <= 0:
            balance = straight_line_balance()
        else:
            try:
                term_growth = (1.0 + monthly_rate) ** _STANDARD_MORTGAGE_TERM_MONTHS
                elapsed_growth = (1.0 + monthly_rate) ** elapsed
            except OverflowError:
                balance = straight_line_balance()
            else:
                denominator = term_growth - 1.0
                if (
                    not math.isfinite(term_growth)
                    or not math.isfinite(elapsed_growth)
                    or not math.isfinite(denominator)
                    or denominator == 0.0
                ):
                    balance = straight_line_balance()
                else:
                    balance = principal * (term_growth - elapsed_growth) / denominator
                    if not math.isfinite(balance):
                        balance = straight_line_balance()

    return max(0, int(round(balance)))


def bounded_mortgage_rate(raw_rate: float | None) -> float | None:
    """Return the governed source-quality bound for mortgage rates.

    Mirrors ``mip.gold.fn_bounded_mortgage_rate``. Rates are fractional
    annual APR values. ``None`` and values below 1% return ``None`` because
    they are treated as missing/no-signal in gold. Values above 15% clamp to
    15% so source or synthetic generator outliers remain visible without
    dominating mortgage economics.
    """
    import math

    try:
        rate = None if raw_rate is None else float(raw_rate)
    except (TypeError, ValueError):
        return None
    if rate is None or not math.isfinite(rate) or rate < _MIN_BOUNDED_MORTGAGE_RATE:
        return None
    if rate > _MAX_BOUNDED_MORTGAGE_RATE:
        return _MAX_BOUNDED_MORTGAGE_RATE
    return rate


def estimated_upb_confidence_band(
    original_upb: int | float | None,
    estimated_rate: float | None,
    months_elapsed: int | None,
) -> dict[str, int]:
    """Return lower/point/upper estimated-UPB dollars.

    Mirrors ``mip.gold.fn_estimated_upb_confidence_band``. The point estimate
    is ``estimated_upb`` using the canonical bounded mortgage rate. The band
    recomputes ``estimated_upb`` at the same plausible rate floor/ceiling used
    by the gold rate-quality bound. The final range is expanded to include the
    point estimate so unknown-rate straight-line fallback rows still produce a
    valid ``lower <= estimate <= upper`` contract.
    """
    estimate = estimated_upb(
        original_upb,
        bounded_mortgage_rate(estimated_rate),
        months_elapsed,
    )
    lower_at_floor = estimated_upb(
        original_upb,
        bounded_mortgage_rate(_MIN_BOUNDED_MORTGAGE_RATE),
        months_elapsed,
    )
    upper_at_ceiling = estimated_upb(
        original_upb,
        bounded_mortgage_rate(_MAX_BOUNDED_MORTGAGE_RATE),
        months_elapsed,
    )
    return {
        "lower_upb": min(lower_at_floor, estimate, upper_at_ceiling),
        "estimate_upb": estimate,
        "upper_upb": max(lower_at_floor, estimate, upper_at_ceiling),
    }


def in_the_money(
    rate_spread_bps: int | None,
    equity_pct: int | None,
    min_spread_bps: int | None,
    min_equity_pct: int | None,
) -> bool:
    """Return True iff borrower clears BOTH the spread and equity thresholds.

    Mirrors ``mip.gold.fn_in_the_money``. Any ``None`` argument
    returns ``False`` (unknown must not silently become GO for outreach).
    The ``>=`` comparison is inclusive — a borrower exactly at the
    threshold IS in the money (golden cases 04/05).
    """
    if (
        rate_spread_bps is None
        or equity_pct is None
        or min_spread_bps is None
        or min_equity_pct is None
    ):
        return False
    return (rate_spread_bps >= min_spread_bps) and (equity_pct >= min_equity_pct)


def refi_propensity_heuristic(
    rate_spread_bps: int | None,
    loan_age_months: int | None,
    equity_pct: int | None,
    estimated_upb: int | None,
    listed_for_sale: bool | None,
) -> int:
    """Return the 0..100 transparent refi-propensity heuristic score.

    Mirrors ``mip.gold.fn_refi_propensity_heuristic`` (S1.3). This is a
    deterministic published points table over observable lien/AVM/MLS
    signals -- NOT the Cotality refi propensity model, which the app
    surfaces separately. The exact table lives in the UDF header and the
    glossary methodology entry; the ``refi_propensity`` overlay segment
    admits scores >= 60 (threshold applied in gold, not here). ``None``
    numeric inputs contribute 0 points; ``None`` listed_for_sale is
    treated as not-listed.
    """
    spread = rate_spread_bps or 0
    if spread >= 100:
        score = 40
    elif spread >= 75:
        score = 32
    elif spread >= 50:
        score = 22
    elif spread >= 25:
        score = 10
    else:
        score = 0
    if loan_age_months is not None:
        if 24 <= loan_age_months <= 84:
            score += 20
        elif 12 <= loan_age_months <= 23 or 85 <= loan_age_months <= 120:
            score += 10
    equity = equity_pct or 0
    if equity >= 20:
        score += 20
    elif equity >= 10:
        score += 10
    upb = estimated_upb or 0
    if upb >= 150_000:
        score += 10
    elif upb >= 75_000:
        score += 5
    if not listed_for_sale:
        score += 10
    return score


def next_best_offer(
    rate_spread_bps: int | None,
    equity_pct: int | None,
    has_permit: bool | None,
    listed_for_sale: bool | None,
    is_investor: bool | None,
    is_current_customer: bool | None,
    is_competitor_lien: bool | None,
    min_spread_bps: int | None,
    min_equity_pct: int | None,
    heloc_equity_min_pct: int | None,
    cashout_equity_min: int | None,
    retention_min_spread: int | None,
) -> str:
    """Return the lowercase offer code for the winning branch.

    Mirrors ``mip.gold.fn_next_best_offer``. First match wins across
    the priority-ordered decision tree documented in the SQL header:
    listed -> refi_plus_heloc -> heloc -> refi -> cash_out -> investor
    -> retention -> nurture. The ``has_permit`` argument is the legacy
    slot for HELOC-intent evidence; callers may pass filed permits OR a
    governed HELOC-propensity trigger, but the API must keep those source
    facts separate. Borrower-signal ``None`` coerces to 0/FALSE, but
    threshold ``None`` returns ``'nurture'`` before the tree runs.
    A missing threshold is a configuration failure, not permission to
    treat 0 >= 0 as a positive eligibility signal.
    """
    if (
        min_spread_bps is None
        or min_equity_pct is None
        or heloc_equity_min_pct is None
        or cashout_equity_min is None
        or retention_min_spread is None
    ):
        return "nurture"

    spread = rate_spread_bps or 0
    equity = equity_pct or 0
    heloc_intent = bool(has_permit)
    listed = bool(listed_for_sale)
    investor = bool(is_investor)
    customer = bool(is_current_customer)
    competitor_lien = bool(is_competitor_lien)
    min_sp = min_spread_bps
    min_eq = min_equity_pct
    heloc_min = heloc_equity_min_pct
    cashout_min = cashout_equity_min
    retention_min = retention_min_spread

    if listed:
        return "purchase"
    if spread >= min_sp and equity >= heloc_min:
        return "refi_plus_heloc"
    if heloc_intent and equity >= heloc_min:
        return "heloc"
    if spread >= min_sp and equity >= min_eq:
        return "refi"
    if equity >= cashout_min:
        return "cash_out"
    if investor:
        return "investor"
    if customer and (spread >= retention_min or competitor_lien):
        return "retention"
    return "nurture"


def loan_product_type(
    loan_type_code: str | None,
    original_loan_amount: int | None,
    conforming_limit_usd: int | None,
) -> str | None:
    """Return the canonical loan product-type bucket, or ``None`` if unknown.

    Mirrors ``mip.gold.fn_loan_product_type`` EXACTLY. The Cotality
    first-position loan type code (CONV / FHA / VA / other codes) maps to
    the lowercase vocabulary in ``LOAN_PRODUCT_TYPES``; a conventional
    first lien whose ORIGINAL amount is strictly greater than the governed
    conforming loan limit classifies as ``'jumbo'``. A loan exactly at the
    limit is conforming (FHFA definition -- golden fixture pins the
    boundary). ``None``/blank code returns ``None``: an unknown source
    code must read as "unknown", never a guessed product. ``None`` amount
    or limit only disables the jumbo branch.
    """
    if loan_type_code is None:
        return None
    code = loan_type_code.strip().upper()
    if not code:
        return None
    if code == "CONV":
        if (
            original_loan_amount is not None
            and conforming_limit_usd is not None
            and original_loan_amount > conforming_limit_usd
        ):
            return "jumbo"
        return "conventional"
    if code == "FHA":
        return "fha"
    if code == "VA":
        return "va"
    return "other"

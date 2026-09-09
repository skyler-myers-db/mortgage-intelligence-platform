"""Query-builder helpers for the Databricks portfolio repository: criteria
predicate compilation, KPI trend shaping, cache keys, and value coercion."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from backend.schemas.portfolio import (
    HouseholdDedupConfig,
    HouseholdDedupSummary,
    KpiTrend,
    PortfolioCriteria,
)
from backend.services.eligibility import eligible_sql_predicate, suppressed_sql_predicate

PORTFOLIO_PRODUCT_CODES: dict[str, list[str]] = {
    "refi": ["refi", "refi_plus_heloc"],
    "heloc": ["heloc", "refi_plus_heloc"],
    "cash-out": ["cash_out"],
    "purchase": ["purchase"],
    "retention": ["retention"],
}

PORTFOLIO_EQUITY_THRESHOLDS: dict[str, int] = {
    "≥ 15%": 15,
    "≥ 25%": 25,
    "≥ 40%": 40,
}


def build_preview_predicates(
    criteria: PortfolioCriteria | None,
    *,
    state_sets: dict[str, list[str]],
    product_codes: dict[str, list[str]] = PORTFOLIO_PRODUCT_CODES,
    equity_thresholds: dict[str, int] = PORTFOLIO_EQUITY_THRESHOLDS,
) -> tuple[str, dict[str, Any]]:
    """Convert validated PortfolioCriteria into a warehouse WHERE clause."""
    if criteria is None:
        return "", {}

    clauses: list[str] = []
    params: dict[str, Any] = {}

    states: list[str] = []
    if criteria.states:
        states.extend(criteria.states)
    if criteria.geography:
        key = criteria.geography.lower()
        if key == "all" or (key.startswith("all ") and key in state_sets):
            pass
        else:
            states.extend(state_sets.get(key) or [])
    states = list(dict.fromkeys(states))
    if states:
        placeholders = ", ".join(f":geo_state_{i}" for i in range(len(states)))
        clauses.append(f"state IN ({placeholders})")
        for i, state in enumerate(states):
            params[f"geo_state_{i}"] = state

    if criteria.occupancy == "Owner-occupied":
        clauses.append("is_owner_occupied = TRUE")
    elif criteria.occupancy == "Non-owner-occupied":
        clauses.append("is_owner_occupied = FALSE")

    lien_status = (criteria.lien_status or "").strip().lower()
    if lien_status in {"free & clear", "free and clear"}:
        clauses.append("current_lien_balance = 0")
    elif lien_status in {"open 1st lien", "open first lien"}:
        clauses.append("current_lien_balance > 0")
        clauses.append("COALESCE(second_pos_amount, 0) = 0")
    elif lien_status in {"open heloc", "open 2nd lien / heloc", "multiple liens"}:
        clauses.append("COALESCE(second_pos_amount, 0) > 0")

    owner_link = (criteria.owner_link or "").strip().lower()
    if owner_link == "single-property owner":
        clauses.append("COALESCE(related_property_count, 1) <= 1")
    elif owner_link == "multi-property (2-4)":
        clauses.append("COALESCE(related_property_count, 1) BETWEEN 2 AND 4")
    elif owner_link == "portfolio investor (5+)":
        clauses.append("COALESCE(related_property_count, 1) >= 5")

    purchase_intent = (criteria.purchase_intent or "").strip().lower()
    if purchase_intent == "listed for sale":
        clauses.append("listed_for_sale = TRUE")
    elif purchase_intent == "heloc intent":
        clauses.append("has_heloc_propensity_trigger = TRUE")
    elif purchase_intent == "both":
        clauses.append("listed_for_sale = TRUE")
        clauses.append("has_heloc_propensity_trigger = TRUE")

    relationship = (criteria.lender_relationship or "").strip().lower()
    if relationship == "current customer":
        clauses.append("is_current_customer = TRUE")
    elif relationship == "former customer":
        clauses.append("is_former_customer = TRUE")
    elif relationship in {"competitor customer", "competitor"}:
        clauses.append("is_competitor_lien = TRUE")

    target_lender_ref = (criteria.target_lender_ref or "").strip()
    if target_lender_ref and target_lender_ref.lower() != "all":
        clauses.append("current_lender_ref = :target_lender_ref")
        params["target_lender_ref"] = target_lender_ref

    if criteria.product and criteria.product != "All products":
        codes = product_codes.get(criteria.product.lower())
        if codes:
            placeholders = ", ".join(f":product_{i}" for i in range(len(codes)))
            clauses.append(f"recommended_offer_code IN ({placeholders})")
            for i, code in enumerate(codes):
                params[f"product_{i}"] = code

    # S1.6 loan product-type dimension. "Unknown" matches the NULL bucket
    # (missing Cotality loan type code); named labels match the frozen
    # fn_loan_product_type vocabulary exactly.
    loan_product = (criteria.loan_product or "").strip()
    if loan_product and loan_product != "All loan products":
        if loan_product == "Unknown":
            clauses.append("loan_product_type IS NULL")
        else:
            clauses.append("loan_product_type = :loan_product_type")
            params["loan_product_type"] = loan_product.lower()

    # S1.6 origination-channel dimension. "Unknown" matches borrowers with no
    # funded first-party application (NULL); named labels map to the governed
    # LOS feed vocabulary.
    origination_channel = (criteria.origination_channel or "").strip()
    if origination_channel and origination_channel != "All channels":
        if origination_channel == "Unknown":
            clauses.append("origination_channel IS NULL")
        else:
            clauses.append("origination_channel = :origination_channel")
            params["origination_channel"] = origination_channel.lower().replace(" ", "_")

    equity_floor: float | int | None = None
    if criteria.min_equity_pct is not None:
        equity_floor = criteria.min_equity_pct
    elif criteria.min_equity_pct_label:
        equity_floor = equity_thresholds.get(criteria.min_equity_pct_label)
    if equity_floor is not None and equity_floor > 0:
        clauses.append("equity_pct >= :equity_floor")
        params["equity_floor"] = equity_floor

    # S1.4: contactability predicates come from the single
    # EligibilityService module so a consent-source swap cannot fork the
    # set-based enforcement semantics from the row-level ones.
    marketing_eligibility = (criteria.marketing_eligibility or "").strip()
    if marketing_eligibility == "Eligible only":
        clauses.append(eligible_sql_predicate())
    elif marketing_eligibility == "Suppressed only":
        clauses.append(suppressed_sql_predicate())

    consent_status = (criteria.consent_status or "").strip()
    if consent_status == "Opt-in":
        clauses.append("consent_status = 'opt_in'")
    elif consent_status == "Opt-out":
        clauses.append("consent_status = 'opt_out'")
    elif consent_status == "Unknown":
        clauses.append("consent_status = 'unknown'")

    recency = (criteria.recency or "").strip()
    recency_days = {
        "Untouched 30d": 30,
        "Untouched 60d": 60,
        "Untouched 90d": 90,
    }.get(recency)
    if recency_days:
        clauses.append(
            f"(last_touch_at IS NULL OR last_touch_at < CURRENT_TIMESTAMP() - INTERVAL {recency_days} DAYS)"
        )

    if not clauses:
        return "", {}
    return "WHERE " + " AND ".join(clauses), params


def build_kpi_trend(points: list[tuple[str, float]]) -> KpiTrend:
    """Compute KpiTrend from oldest-first (date label, value) points."""
    notes: list[str] = []
    original_start = points[0][0] if points else None
    while len(points) > 1 and points[0][1] == 0 and any(p[1] != 0 for p in points[1:]):
        points = points[1:]
    if original_start and points and points[0][0] != original_start:
        notes.append(
            f"Comparison starts on {points[0][0]} because earlier snapshots predate this metric."
        )
    series = [value for _, value in points]
    comparison_label = f"vs {points[0][0]}" if len(points) >= 2 else None
    if len(series) < 2 or series[0] == 0:
        return KpiTrend(
            series=series,
            delta_pct=None,
            direction="flat",
            comparison_label=comparison_label,
            note=" ".join(notes) or None,
        )
    for index in range(1, len(points)):
        previous = points[index - 1][1]
        current = points[index][1]
        if previous == 0:
            continue
        step_pct = abs((current - previous) / previous) * 100.0
        if step_pct >= 8.0:
            notes.append(
                f"Trend includes a coverage or rules update on {points[index][0]}; counts remain source-backed and the comparison is shown for context."
            )
            break
    delta_pct = ((series[-1] - series[0]) / series[0]) * 100.0
    direction = "up" if delta_pct > 0.5 else "down" if delta_pct < -0.5 else "flat"
    return KpiTrend(
        series=series,
        delta_pct=round(delta_pct, 1),
        direction=direction,
        comparison_label=comparison_label,
        note=" ".join(notes) or None,
    )


def coerce_utc_datetime(value: Any) -> datetime | None:
    """Normalise warehouse timestamp-like values into tz-aware UTC datetimes."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    try:
        raw = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def preview_cache_key(
    prefix: str,
    where_clause: str,
    params: dict[str, Any],
    *,
    campaign_build_config: dict[str, Any] | None = None,
) -> str:
    """Build a deterministic bounded cache key for preview results."""
    canonical = json.dumps(
        {
            "where": where_clause,
            "params": params,
            "campaign_build_config": campaign_build_config,
        },
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]  # noqa: S324
    return f"{prefix}:{digest}"


def json_value(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return value


def household_dedup_config_from_value(value: Any) -> HouseholdDedupConfig:
    if not isinstance(value, dict):
        return HouseholdDedupConfig()
    return HouseholdDedupConfig.model_validate(value)


def household_dedup_summary_from_value(value: Any) -> HouseholdDedupSummary:
    if not isinstance(value, dict):
        return HouseholdDedupSummary()
    return HouseholdDedupSummary.model_validate(value)

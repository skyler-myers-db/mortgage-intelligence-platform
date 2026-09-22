"""Direct-answer branches for segment comparisons, offers, and lock-in cohorts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.services.databricks_sql import (
    DatabricksSqlError,
)
from backend.services.genie_answers import GenieMessageResponse
from backend.services.repositories.databricks_genie_canonical import (
    _CANONICAL_HELOC_RECOMMENDATION_BORROWERS_SQL,
    _CANONICAL_INVESTOR_SEGMENT_BY_STATE_SQL,
    _CANONICAL_ITM_OFFER_MIX_SQL,
    _CANONICAL_ITM_TOP_TIER_COMPARE_SQL,
    _CANONICAL_LISTED_BY_PRODUCT_RATE_SQL,
    _CANONICAL_LISTED_DAYS_ON_MARKET_BY_STATE_SQL,
    _CANONICAL_LOCKIN_BY_STATE_SQL,
    _CANONICAL_LOCKIN_COHORT_SIZE_SQL,
    _CANONICAL_LOCKIN_MEDIAN_RATE_SQL,
    _CANONICAL_MEAN_RATE_SPREAD_BY_SEGMENT_SQL,
    _CANONICAL_REFI_DRIVER_SQL,
    _CANONICAL_REFI_EQUITY_SIGNAL_COMPARE_SQL,
    _CANONICAL_SEGMENT_APPROVAL_RATE_SQL,
    _CANONICAL_TOP_COHORTS_SQL,
)
from backend.services.repositories.databricks_genie_direct_responses import (
    _data_gap_response,
    _segment_display_label,
)
from backend.services.repositories.databricks_genie_policy_helpers import (
    _emit_genie_warning,
    _redact_genie_rows,
)
from backend.services.scoring import (
    HIGH_OPPORTUNITY_THRESHOLD,
    offer_display_label,
)

if TYPE_CHECKING:
    from backend.services.repositories.databricks_genie_direct import _DirectContext


def _direct_refi_equity_signal_compare(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Refi versus equity signal comparison."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    trusted_assets = ctx.trusted_assets
    try:
        row = sql_client.execute_one(_CANONICAL_REFI_EQUITY_SIGNAL_COMPARE_SQL) or {}
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_refi_equity_compare_failed", exc=exc)
        return None
    rows = [
        {
            "marketable_borrowers": int(row.get("marketable_borrowers") or 0),
            "refinance_candidates": int(row.get("refinance_candidates") or 0),
            "home_equity_candidates": int(row.get("home_equity_candidates") or 0),
            "refi_plus_home_equity_candidates": int(
                row.get("refi_plus_home_equity_candidates") or 0
            ),
            "avg_refi_rate_spread_bps": row.get("avg_refi_rate_spread_bps"),
            "avg_home_equity_pct": row.get("avg_home_equity_pct"),
            "avg_heloc_propensity_score": row.get("avg_heloc_propensity_score"),
            "refi_propensity_triggers": int(row.get("refi_propensity_triggers") or 0),
            "heloc_propensity_triggers": int(row.get("heloc_propensity_triggers") or 0),
            "refreshed_at": row.get("refreshed_at"),
        }
    ]
    answer = (
        "Compare refinance and home-equity outreach on four signals: rate spread, "
        "available equity, Cotality propensity, and the winning offer branch. "
        f"In the current marketable borrower set, {rows[0]['refinance_candidates']:,} "
        f"borrowers are in a refinance lane and {rows[0]['home_equity_candidates']:,} "
        "are in a home-equity extraction lane. The overlap to review first is "
        f"{rows[0]['refi_plus_home_equity_candidates']:,} borrowers where the rule selected "
        "Refinance + HELOC. Use rate spread to justify refinance, equity percentage and "
        "HELOC propensity to justify home-equity, and keep filed building permits separate "
        "because that source is not the live HELOC signal."
    )
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_REFI_EQUITY_SIGNAL_COMPARE_SQL,
        trusted_assets=trusted_assets,
        rows=rows,
        answer=answer,
    )


def _direct_refi_driver(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Which trigger drives the refinance segment."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
    evidence_asset = ctx.evidence_asset
    try:
        rows = _redact_genie_rows(sql_client.execute(_CANONICAL_REFI_DRIVER_SQL)) or []
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_refi_driver_failed", exc=exc)
        return None
    if rows:
        top = rows[0]
        answer = (
            "The refinance lane is calculated from governed borrower economics. "
            f"The leading current signal is `{top.get('signal_type')}`, present for "
            f"{int(top.get('borrowers') or 0):,} marketing-eligible borrowers in refinance "
            f"or refinance-plus-HELOC offer lanes. Review the table by signal type: rate "
            "spread is the economic reason to refinance, equity supports cross-sell, and "
            "refi propensity adds Cotality intent context."
        )
    else:
        answer = (
            "The governed evidence table returned no refinance-driver rows for the current "
            "marketable refinance lanes. That means I will not invent a driver ranking."
        )
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_REFI_DRIVER_SQL,
        trusted_assets=[borrower_asset, evidence_asset],
        rows=rows,
        answer=answer,
    )


def _direct_itm_top_tier_compare(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Top-tier versus rest-of-population comparison."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    trusted_assets = ctx.trusted_assets
    try:
        row = sql_client.execute_one(_CANONICAL_ITM_TOP_TIER_COMPARE_SQL) or {}
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_itm_top_tier_failed", exc=exc)
        return None
    rows = [
        {
            "marketable_borrowers": int(row.get("marketable_borrowers") or 0),
            "in_the_money_borrowers": int(row.get("in_the_money_borrowers") or 0),
            "top_tier_borrowers": int(row.get("top_tier_borrowers") or 0),
            "overlap_borrowers": int(row.get("overlap_borrowers") or 0),
            "avg_in_the_money_rate_spread_bps": row.get(
                "avg_in_the_money_rate_spread_bps"
            ),
            "avg_top_tier_score": row.get("avg_top_tier_score"),
            "refreshed_at": row.get("refreshed_at"),
        }
    ]
    answer = (
        "They are related but not the same. In-the-money is a refinance-economics "
        "screen: the borrower clears the configured rate-spread and equity thresholds. "
        f"Top-tier opportunity means opportunity_score >= {HIGH_OPPORTUNITY_THRESHOLD}, which blends economics, "
        "intent, fit, relationship, and evidence. In the current marketable set, "
        f"{rows[0]['in_the_money_borrowers']:,} borrowers are in-the-money, "
        f"{rows[0]['top_tier_borrowers']:,} are top-tier, and "
        f"{rows[0]['overlap_borrowers']:,} are both. Use the overlap for the cleanest "
        "refinance story; use top-tier outside in-the-money when another offer lane is stronger."
    )
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_ITM_TOP_TIER_COMPARE_SQL,
        trusted_assets=trusted_assets,
        rows=rows,
        answer=answer,
    )


def _direct_investor_segment_by_state(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Investor segment size by state."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    segment_population_asset = ctx.segment_population_asset
    try:
        rows = (
            _redact_genie_rows(sql_client.execute(_CANONICAL_INVESTOR_SEGMENT_BY_STATE_SQL))
            or []
        )
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_investor_segment_failed", exc=exc)
        return None
    if rows:
        top = rows[0]
        answer = (
            "I broke the Investor / Multi-Property segment down by state from "
            f"{segment_population_asset}. "
            f"{top.get('state')} currently leads with "
            f"{int(top.get('investor_borrowers') or 0):,} segment borrowers."
        )
    else:
        answer = (
            "The trusted segment population returned no Investor / Multi-Property "
            "state rows for the current refreshed data coverage."
        )
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_INVESTOR_SEGMENT_BY_STATE_SQL,
        trusted_assets=[segment_population_asset],
        rows=rows,
        answer=answer,
    )


def _direct_mean_rate_spread_by_segment(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Mean rate spread by segment."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
    try:
        rows = (
            _redact_genie_rows(sql_client.execute(_CANONICAL_MEAN_RATE_SPREAD_BY_SEGMENT_SQL))
            or []
        )
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_mean_spread_segment_failed", exc=exc)
        return None
    answer = (
        f"I calculated mean rate spread by segment from {borrower_asset}. "
        "Positive spread means the first-position rate is above the current market rate."
        if rows
        else "The trusted borrower table returned no segment rows with rate-spread values."
    )
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_MEAN_RATE_SPREAD_BY_SEGMENT_SQL,
        trusted_assets=[borrower_asset],
        rows=rows,
        answer=answer,
    )


def _direct_segment_approval_rate(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Approval rate by segment."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    segment_performance_asset = ctx.segment_performance_asset
    try:
        rows = (
            _redact_genie_rows(sql_client.execute(_CANONICAL_SEGMENT_APPROVAL_RATE_SQL))
            or []
        )
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_segment_approval_rate_failed", exc=exc)
        return None
    if rows:
        top = rows[0]
        rate = top.get("approval_rate")
        answer = (
            f"I ranked segment approval rate from {segment_performance_asset}. "
            f"The current leader is {_segment_display_label(top.get('segment_code'))} "
            f"with approval rate {float(rate or 0):,.1f}%."
        )
    else:
        answer = (
            "The segment performance view returned no national approval-rate rows for "
            "the current refreshed coverage."
        )
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_SEGMENT_APPROVAL_RATE_SQL,
        trusted_assets=[segment_performance_asset],
        rows=rows,
        answer=answer,
    )


def _direct_itm_offer_mix(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Recommended-offer mix across in-the-money borrowers."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
    try:
        rows = _redact_genie_rows(sql_client.execute(_CANONICAL_ITM_OFFER_MIX_SQL)) or []
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_itm_offer_mix_failed", exc=exc)
        return None
    if rows:
        top = rows[0]
        top_offer = offer_display_label(
            str(top.get("recommended_offer_code") or ""),
            str(top.get("recommended_offer") or ""),
        )
        answer = (
            f"I grouped the In-the-Money segment by recommended offer from {borrower_asset}. "
            f"The largest current offer lane is {top_offer} with "
            f"{int(top.get('borrowers') or 0):,} borrowers."
        )
    else:
        answer = "The trusted borrower table returned no In-the-Money offer-mix rows."
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_ITM_OFFER_MIX_SQL,
        trusted_assets=[borrower_asset],
        rows=rows,
        answer=answer,
    )


def _direct_projected_monthly_savings_gap(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Governed data gap for projected monthly savings."""
    question = ctx.question
    source_readiness_asset = ctx.source_readiness_asset
    answer = (
        "No trusted asset currently contains projected monthly savings for approved refis. "
        "The app can cite rate spread, current rate, market rate, offer code, and approval "
        "state, but it must not substitute those as a savings estimate until a governed "
        "`projected_monthly_savings_usd` measure is added."
    )
    return _data_gap_response(
        question=question,
        answer=answer,
        trusted_assets=[source_readiness_asset],
        known_data_gaps=[
            "projected_monthly_savings_usd is not present in the trusted Module 0 asset inventory"
        ],
    )


def _direct_heloc_recommendation_borrowers(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Borrowers recommended for a HELOC offer."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
    try:
        rows = (
            _redact_genie_rows(
                sql_client.execute(_CANONICAL_HELOC_RECOMMENDATION_BORROWERS_SQL)
            )
            or []
        )
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_heloc_recommendations_failed", exc=exc)
        return None
    if rows:
        answer = (
            f"I listed up to {len(rows)} marketing-eligible borrowers whose recommended "
            f"offer is HELOC or Refinance + HELOC from {borrower_asset}. These are "
            "masked borrower IDs only, not names or contact details."
        )
    else:
        answer = (
            "The trusted borrower table returned no marketing-eligible HELOC recommendation "
            "rows for the current refreshed coverage."
        )
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_HELOC_RECOMMENDATION_BORROWERS_SQL,
        trusted_assets=[borrower_asset],
        rows=rows,
        answer=answer,
    )


def _direct_listed_by_product_rate(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Listed borrowers by product and rate."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
    try:
        rows = (
            _redact_genie_rows(sql_client.execute(_CANONICAL_LISTED_BY_PRODUCT_RATE_SQL))
            or []
        )
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_listed_product_rate_failed", exc=exc)
        return None
    answer = (
        f"I broke the Listed-for-Sale segment down by loan product and average current "
        f"rate from {borrower_asset}."
        if rows
        else "The trusted borrower table returned no listed-for-sale rows for this breakdown."
    )
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_LISTED_BY_PRODUCT_RATE_SQL,
        trusted_assets=[borrower_asset],
        rows=rows,
        answer=answer,
    )


def _direct_listed_days_on_market_by_state(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Listed days-on-market by state."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
    try:
        rows = (
            _redact_genie_rows(
                sql_client.execute(_CANONICAL_LISTED_DAYS_ON_MARKET_BY_STATE_SQL)
            )
            or []
        )
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_listed_days_by_state_failed", exc=exc)
        return None
    if rows:
        top = rows[0]
        avg_dom = top.get("avg_listing_days_on_market")
        avg_dom_text = f"{float(avg_dom):.1f}" if avg_dom is not None else "unknown"
        answer = (
            f"I grouped the live Listed-for-Sale segment by state from {borrower_asset}. "
            f"The top state by listed borrower count is {top.get('state')} with "
            f"{int(top.get('listed_borrowers') or 0):,} listed borrowers and "
            f"{avg_dom_text} average listing days on market."
        )
    else:
        answer = (
            "The trusted borrower table returned no listed-for-sale rows with state "
            "coverage for this days-on-market breakdown."
        )
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_LISTED_DAYS_ON_MARKET_BY_STATE_SQL,
        trusted_assets=[borrower_asset],
        rows=rows,
        answer=answer,
    )


def _direct_lockin_size(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Rate lock-in cohort size."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    lockin_asset = ctx.lockin_asset
    try:
        row = sql_client.execute_one(_CANONICAL_LOCKIN_COHORT_SIZE_SQL) or {}
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_lockin_size_failed", exc=exc)
        return None
    count_int = int(row.get("lockin_borrowers") or 0)
    rows = [{"lockin_borrowers": count_int, "refreshed_at": row.get("refreshed_at")}]
    answer = (
        f"The 2020-2022 sub-3% lock-in cohort has {count_int:,} borrowers in "
        f"{lockin_asset}."
    )
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_LOCKIN_COHORT_SIZE_SQL,
        trusted_assets=[lockin_asset],
        rows=rows,
        answer=answer,
        metric_value=f"{count_int:,}",
    )


def _direct_lockin_median_rate(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Rate lock-in cohort median rate."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    lockin_asset = ctx.lockin_asset
    try:
        row = sql_client.execute_one(_CANONICAL_LOCKIN_MEDIAN_RATE_SQL) or {}
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_lockin_median_failed", exc=exc)
        return None
    median = row.get("median_rate_pct")
    rows = [
        {
            "median_rate_pct": median,
            "lockin_borrowers": int(row.get("lockin_borrowers") or 0),
            "refreshed_at": row.get("refreshed_at"),
        }
    ]
    median_text = f"{float(median):,.3f}%" if median is not None else "not available"
    answer = f"The median origination rate in {lockin_asset} is {median_text}."
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_LOCKIN_MEDIAN_RATE_SQL,
        trusted_assets=[lockin_asset],
        rows=rows,
        answer=answer,
        metric_value=median_text,
    )


def _direct_lockin_by_state(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Rate lock-in cohort by state."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    lockin_asset = ctx.lockin_asset
    try:
        rows = _redact_genie_rows(sql_client.execute(_CANONICAL_LOCKIN_BY_STATE_SQL)) or []
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_lockin_by_state_failed", exc=exc)
        return None
    if rows:
        top = rows[0]
        answer = (
            f"I broke down the lock-in cohort by state from {lockin_asset}. "
            f"{top.get('state')} currently leads with "
            f"{int(top.get('lockin_borrowers') or 0):,} borrowers."
        )
    else:
        answer = f"{lockin_asset} returned no state rows for the current refreshed coverage."
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_LOCKIN_BY_STATE_SQL,
        trusted_assets=[lockin_asset],
        rows=rows,
        answer=answer,
    )


def _direct_top_cohorts(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Largest segment cohorts."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    segment_population_asset = ctx.segment_population_asset
    try:
        rows = _redact_genie_rows(sql_client.execute(_CANONICAL_TOP_COHORTS_SQL)) or []
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_top_cohorts_failed", exc=exc)
        return None
    if rows:
        top = rows[0]
        has_legacy_permit_segment = any(
            str(row.get("segment_code") or "").lower() == "permit" for row in rows
        )
        permit_note = (
            " The HELOC Intent cohort comes from Cotality HELOC propensity; "
            "it is not filed building-permit data."
            if has_legacy_permit_segment
            else ""
        )
        answer = (
            f"I ranked the top cohorts from {segment_population_asset}. "
            f"The largest current cohort is {_segment_display_label(top.get('segment_code'))} "
            f"with {int(top.get('borrowers') or 0):,} borrowers.{permit_note}"
        )
    else:
        answer = f"{segment_population_asset} returned no national cohort rows."
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_TOP_COHORTS_SQL,
        trusted_assets=[segment_population_asset],
        rows=rows,
        answer=answer,
    )

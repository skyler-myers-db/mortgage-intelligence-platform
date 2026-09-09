"""Direct trusted-SQL answers for narrow canonical Genie questions."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.services.databricks_sql import DatabricksSqlClient, DatabricksSqlError
from backend.services.databricks_sql_helpers import qualify
from backend.services.genie_answers import (
    GenieMessageResponse,
    GenieProof,
    default_follow_up_questions,
)
from backend.services.repositories.databricks_genie_actions import (
    _suggest_genie_actions,
)
from backend.services.repositories.databricks_genie_canonical import (
    _CANONICAL_ITM_COUNT_BY_CITY_SQL,
    _CANONICAL_ITM_COUNT_BY_STATE_SQL,
    _CANONICAL_ITM_COUNT_SQL,
    _CANONICAL_MSA_SCORE_SQL,
    _canonical_addressable_market_scope,
    _canonical_approval_trend_30d_scope,
    _canonical_cash_out_state_scope,
    _canonical_equity_threshold_scope,
    _canonical_evidence_events_quarter_scope,
    _canonical_evidence_events_yesterday_scope,
    _canonical_heloc_count_scope,
    _canonical_heloc_recommendation_borrowers_scope,
    _canonical_heloc_zip_scope,
    _canonical_home_equity_distribution_scope,
    _canonical_in_the_money_count_scope,
    _canonical_investor_count_scope,
    _canonical_investor_segment_by_state_scope,
    _canonical_investor_top_by_related_property_scope,
    _canonical_itm_city_scope,
    _canonical_itm_count_avg_spread_scope,
    _canonical_itm_offer_mix_scope,
    _canonical_itm_share_scope,
    _canonical_itm_state_breakdown_scope,
    _canonical_itm_top_tier_compare_scope,
    _canonical_itm_zip_scope,
    _canonical_lead_score_weekly_distribution_scope,
    _canonical_listed_by_product_rate_scope,
    _canonical_listed_count_scope,
    _canonical_listed_days_on_market_by_state_scope,
    _canonical_listed_purchase_scope,
    _canonical_lockin_by_state_scope,
    _canonical_lockin_median_rate_scope,
    _canonical_lockin_size_scope,
    _canonical_mean_lead_score_by_state_scope,
    _canonical_mean_rate_spread_by_segment_scope,
    _canonical_msa_score_scope,
    _canonical_negative_equity_scope,
    _canonical_ranked_lead_population_scope,
    _canonical_refi_driver_scope,
    _canonical_refi_equity_signal_compare_scope,
    _canonical_segment_approval_rate_scope,
    _canonical_specific_top_borrowers_global_scope,
    _canonical_specific_top_borrowers_state_scope,
    _canonical_strategy_board_scope,
    _canonical_top_borrowers_all_segments_scope,
    _canonical_top_borrowers_global_scope,
    _canonical_top_borrowers_state_scope,
    _canonical_top_cash_out_by_equity_scope,
    _canonical_top_cohorts_scope,
    _projected_monthly_savings_gap_scope,
    _retention_competitor_lien_list_question,
    _retention_risk_question,
)
from backend.services.repositories.databricks_genie_direct_geo import (
    _direct_itm_state_breakdown,
    _direct_itm_zip,
)
from backend.services.repositories.databricks_genie_direct_metrics import (
    _direct_approval_trend_30d,
    _direct_evidence_events_quarter,
    _direct_evidence_events_yesterday,
    _direct_lead_score_weekly_distribution,
    _direct_mean_lead_score_by_state,
)
from backend.services.repositories.databricks_genie_direct_population import (
    _direct_addressable_market,
    _direct_equity_threshold_count,
    _direct_heloc_count,
    _direct_home_equity_distribution,
    _direct_investor_count,
    _direct_itm_count_avg_spread,
    _direct_itm_share,
    _direct_listed_count,
    _direct_negative_equity_count,
    _direct_ranked_lead_population,
)
from backend.services.repositories.databricks_genie_direct_rankings import (
    _direct_cash_out_state,
    _direct_heloc_zip,
    _direct_investor_top_by_related_property,
    _direct_listed_purchase,
    _direct_specific_top_borrowers_global,
    _direct_specific_top_borrowers_state,
    _direct_strategy_board,
    _direct_top_borrowers_all_segments,
    _direct_top_borrowers_global,
    _direct_top_borrowers_state,
    _direct_top_cash_out_by_equity,
)
from backend.services.repositories.databricks_genie_direct_retention import (
    _direct_retention_competitor_lien_list,
    _direct_retention_risk,
)
from backend.services.repositories.databricks_genie_direct_segments import (
    _direct_heloc_recommendation_borrowers,
    _direct_investor_segment_by_state,
    _direct_itm_offer_mix,
    _direct_itm_top_tier_compare,
    _direct_listed_by_product_rate,
    _direct_listed_days_on_market_by_state,
    _direct_lockin_by_state,
    _direct_lockin_median_rate,
    _direct_lockin_size,
    _direct_mean_rate_spread_by_segment,
    _direct_projected_monthly_savings_gap,
    _direct_refi_driver,
    _direct_refi_equity_signal_compare,
    _direct_segment_approval_rate,
    _direct_top_cohorts,
)
from backend.services.repositories.databricks_genie_policy_helpers import (
    _emit_genie_warning,
    _redact_genie_rows,
)
from backend.services.repositories.databricks_genie_trust import (
    _build_genie_proof,
    _genie_question_hash,
)
from backend.services.repositories.databricks_genie_visualization import (
    _plan_genie_visualization,
)


@dataclass(frozen=True)
class _DirectContext:
    """Prologue values that ``direct_canonical_response`` branch helpers read.

    Built once, after the guide gate and the ``sql_client is None`` guard, so an
    extracted branch sees exactly the locals it saw when every branch lived in
    one function.
    """

    question: str
    sql_client: DatabricksSqlClient
    trusted_response: Callable[..., GenieMessageResponse]
    borrower_asset: str
    evidence_asset: str
    funnel_asset: str
    lead_population_asset: str
    lockin_asset: str
    segment_population_asset: str
    segment_performance_asset: str
    source_readiness_asset: str
    trusted_assets: list[str]


def _trusted_sql_response(
    *,
    question: str,
    sql_query: str,
    trusted_assets: list[str],
    rows: list[dict[str, Any]],
    answer: str,
    metric_value: str | None = None,
    started_at: float | None = None,
    suppress_actions: bool = False,
    cohort_segments: tuple[str, ...] = (),
) -> GenieMessageResponse:
    question_hash = _genie_question_hash(question)
    message_id = f"trusted-sql-{question_hash}"
    elapsed_ms = int((time.monotonic() - started_at) * 1000) if started_at is not None else 0
    proof = _build_genie_proof(
        sql_query=sql_query,
        trusted_assets=trusted_assets,
        rows=rows,
        question=question,
        conversation_id="",
        message_id=message_id,
        elapsed_ms=elapsed_ms,
    )
    visualization = _plan_genie_visualization(question, rows)
    actions = [] if suppress_actions else _suggest_genie_actions(
        question=question,
        rows=rows,
        trusted_assets=trusted_assets,
        visualization=visualization,
        conversation_id="",
        message_id=message_id,
        question_hash=question_hash,
        sql_query=sql_query,
        source="trusted_sql",
        declared_segments=cohort_segments,
    )
    return GenieMessageResponse(
        conversation_id="",
        message_id=message_id,
        elapsed_ms=elapsed_ms,
        question_hash=question_hash,
        question=question,
        answer=answer,
        source="trusted_sql",
        trusted_assets=trusted_assets,
        sql_query=sql_query,
        row_count=len(rows),
        proof=proof,
        visualization=visualization,
        actions=actions,
        metric_value=metric_value,
        table_rows=rows,
    )


def _guide_response(question: str) -> GenieMessageResponse | None:
    q = " ".join(question.lower().split())
    if not (
        any(term in q for term in ("what can i ask", "what should i ask", "help me ask"))
        or ("question" in q and "can i ask" in q)
        or (
            "question" in q
            and any(term in q for term in ("example", "examples", "suggest", "suggestion"))
        )
    ):
        return None
    question_hash = _genie_question_hash(question)
    follow_ups = default_follow_up_questions(limit=5)
    answer = (
        "Ask about borrower segments, ranked leads, geography, trigger evidence, "
        "recommended offers, or governed data gaps. Good questions usually name the "
        "cohort, the decision you are trying to make, and the proof you need. For example: "
        "\"Which ZIPs should a loan officer work first for refinance savings?\" or "
        "\"Which borrower signals should I compare before choosing between refinance "
        "and home-equity outreach?\""
    )
    return GenieMessageResponse(
        conversation_id="",
        message_id=f"guide-{question_hash}",
        elapsed_ms=0,
        question_hash=question_hash,
        question=question,
        answer=answer,
        source="guide",
        trusted_assets=[],
        row_count=0,
        proof=GenieProof(
            source_assets=[],
            row_count=0,
            trusted=False,
            filters=[],
            known_data_gaps=[],
            conversation_id=None,
            message_id=f"guide-{question_hash}",
        ),
        follow_up_questions=follow_ups,
        table_rows=[],
    )


def direct_canonical_response(
    question: str,
    sql_client: DatabricksSqlClient | None,
) -> GenieMessageResponse | None:
    """Return live trusted-SQL proof for narrow gold-grain count prompts."""
    started_at = time.monotonic()

    def trusted_response(**kwargs: Any) -> GenieMessageResponse:
        return _trusted_sql_response(started_at=started_at, **kwargs)

    guide = _guide_response(question)
    if guide is not None:
        return guide
    if sql_client is None:
        return None
    borrower_asset = qualify("gold", "borrower_360")
    evidence_asset = qualify("gold", "evidence_events")
    funnel_asset = qualify("gold", "funnel_snapshot_daily")
    lead_population_asset = qualify("gold", "lead_population")
    lockin_asset = qualify("gold", "lockin_cohort")
    segment_population_asset = qualify("gold", "segment_population")
    segment_performance_asset = qualify("semantics", "segment_performance_metric_view")
    source_readiness_asset = qualify("gold", "source_readiness")
    trusted_assets = [borrower_asset]
    ctx = _DirectContext(
        question=question,
        sql_client=sql_client,
        trusted_response=trusted_response,
        borrower_asset=borrower_asset,
        evidence_asset=evidence_asset,
        funnel_asset=funnel_asset,
        lead_population_asset=lead_population_asset,
        lockin_asset=lockin_asset,
        segment_population_asset=segment_population_asset,
        segment_performance_asset=segment_performance_asset,
        source_readiness_asset=source_readiness_asset,
        trusted_assets=trusted_assets,
    )

    if _canonical_itm_count_avg_spread_scope(question):
        return _direct_itm_count_avg_spread(ctx)

    if _canonical_itm_share_scope(question):
        return _direct_itm_share(ctx)

    equity_scope = _canonical_equity_threshold_scope(question)
    if equity_scope is not None:
        return _direct_equity_threshold_count(ctx, equity_scope)

    negative_equity_scope = _canonical_negative_equity_scope(question)
    if negative_equity_scope is not None:
        return _direct_negative_equity_count(ctx, negative_equity_scope)

    listed_count_scope = _canonical_listed_count_scope(question)
    if listed_count_scope is not None:
        return _direct_listed_count(ctx, listed_count_scope)

    if _canonical_investor_count_scope(question):
        return _direct_investor_count(ctx)

    if _canonical_heloc_count_scope(question):
        return _direct_heloc_count(ctx)

    if _canonical_home_equity_distribution_scope(question):
        return _direct_home_equity_distribution(ctx)

    if _canonical_addressable_market_scope(question):
        return _direct_addressable_market(ctx)

    if _canonical_ranked_lead_population_scope(question):
        return _direct_ranked_lead_population(ctx)

    specific_top_borrowers_state_scope = _canonical_specific_top_borrowers_state_scope(question)
    if specific_top_borrowers_state_scope is not None:
        return _direct_specific_top_borrowers_state(ctx, specific_top_borrowers_state_scope)

    specific_top_borrowers_global_scope = _canonical_specific_top_borrowers_global_scope(question)
    if specific_top_borrowers_global_scope is not None:
        return _direct_specific_top_borrowers_global(ctx, specific_top_borrowers_global_scope)

    top_borrower_state_scope = _canonical_top_borrowers_state_scope(question)
    if top_borrower_state_scope is not None:
        return _direct_top_borrowers_state(ctx, top_borrower_state_scope)

    if _canonical_top_borrowers_all_segments_scope(question):
        return _direct_top_borrowers_all_segments(ctx)

    if _canonical_top_borrowers_global_scope(question):
        return _direct_top_borrowers_global(ctx)

    if _canonical_heloc_zip_scope(question):
        return _direct_heloc_zip(ctx)

    if _canonical_strategy_board_scope(question):
        return _direct_strategy_board(ctx)

    if _canonical_cash_out_state_scope(question):
        return _direct_cash_out_state(ctx)

    if _canonical_top_cash_out_by_equity_scope(question):
        return _direct_top_cash_out_by_equity(ctx)

    if _canonical_listed_purchase_scope(question):
        return _direct_listed_purchase(ctx)

    if _canonical_investor_top_by_related_property_scope(question):
        return _direct_investor_top_by_related_property(ctx)

    if _canonical_refi_equity_signal_compare_scope(question):
        return _direct_refi_equity_signal_compare(ctx)

    if _canonical_refi_driver_scope(question):
        return _direct_refi_driver(ctx)

    if _canonical_itm_top_tier_compare_scope(question):
        return _direct_itm_top_tier_compare(ctx)

    if _canonical_investor_segment_by_state_scope(question):
        return _direct_investor_segment_by_state(ctx)

    if _canonical_mean_rate_spread_by_segment_scope(question):
        return _direct_mean_rate_spread_by_segment(ctx)

    if _canonical_segment_approval_rate_scope(question):
        return _direct_segment_approval_rate(ctx)

    if _canonical_mean_lead_score_by_state_scope(question):
        return _direct_mean_lead_score_by_state(ctx)

    if _canonical_evidence_events_yesterday_scope(question):
        return _direct_evidence_events_yesterday(ctx)

    if _canonical_lead_score_weekly_distribution_scope(question):
        return _direct_lead_score_weekly_distribution(ctx)

    if _canonical_approval_trend_30d_scope(question):
        return _direct_approval_trend_30d(ctx)

    if _canonical_evidence_events_quarter_scope(question):
        return _direct_evidence_events_quarter(ctx)

    if _canonical_itm_offer_mix_scope(question):
        return _direct_itm_offer_mix(ctx)

    if _projected_monthly_savings_gap_scope(question):
        return _direct_projected_monthly_savings_gap(ctx)

    if _canonical_heloc_recommendation_borrowers_scope(question):
        return _direct_heloc_recommendation_borrowers(ctx)

    if _canonical_listed_by_product_rate_scope(question):
        return _direct_listed_by_product_rate(ctx)

    if _canonical_listed_days_on_market_by_state_scope(question):
        return _direct_listed_days_on_market_by_state(ctx)

    if _canonical_lockin_size_scope(question):
        return _direct_lockin_size(ctx)

    if _canonical_lockin_median_rate_scope(question):
        return _direct_lockin_median_rate(ctx)

    if _canonical_lockin_by_state_scope(question):
        return _direct_lockin_by_state(ctx)

    if _canonical_top_cohorts_scope(question):
        return _direct_top_cohorts(ctx)

    if _retention_competitor_lien_list_question(question):
        return _direct_retention_competitor_lien_list(ctx)

    if _retention_risk_question(question):
        return _direct_retention_risk(ctx)

    if _canonical_itm_zip_scope(question):
        return _direct_itm_zip(ctx)

    if _canonical_itm_state_breakdown_scope(question):
        return _direct_itm_state_breakdown(ctx)

    scope = _canonical_in_the_money_count_scope(question)
    if scope is False or scope is None:
        city_scope = _canonical_itm_city_scope(question)
        if not city_scope:
            if _canonical_msa_score_scope(question):
                try:
                    rows = _redact_genie_rows(sql_client.execute(_CANONICAL_MSA_SCORE_SQL)) or []
                except DatabricksSqlError as exc:
                    _emit_genie_warning("direct_canonical_genie_msa_score_failed", exc=exc)
                    return None
                if rows:
                    answer = (
                        "I used Cotality's `situs_cbsa_code` as the MSA identifier "
                        "and ranked the top five markets by borrower volume, then "
                        f"calculated mean lead score at the unique borrower grain from {borrower_asset}."
                    )
                else:
                    answer = (
                        "The current gold borrower table did not return CBSA-coded "
                        "market rows. Module 0 has `situs_cbsa_code` for MSA-style "
                        "grouping, but no separate MSA-name lookup is loaded."
                    )
                return trusted_response(
                    question=question,
                    sql_query=_CANONICAL_MSA_SCORE_SQL,
                    trusted_assets=trusted_assets,
                    rows=rows,
                    answer=answer,
                )
            return None
        try:
            row = (
                sql_client.execute_one(
                    _CANONICAL_ITM_COUNT_BY_CITY_SQL,
                    {"city": city_scope},
                )
                or {}
            )
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_city_metric_failed", exc=exc)
            return None
        raw_count = row.get("in_the_money_borrowers")
        if raw_count is None:
            _emit_genie_warning(
                "direct_canonical_genie_city_metric_bad_count",
                value_type="NoneType",
            )
            return None
        try:
            count_int = int(raw_count)
        except (TypeError, ValueError):
            _emit_genie_warning(
                "direct_canonical_genie_city_metric_bad_count",
                value_type=type(raw_count).__name__,
            )
            return None
        rows = [
            {
                "city": city_scope,
                "in_the_money_borrowers": count_int,
                "refreshed_at": row.get("refreshed_at"),
            }
        ]
        answer = (
            f"There are {count_int:,} borrowers passing the refinance-economics screen in {city_scope} "
            f"within the current gold evaluation-share scope from {borrower_asset}. "
            "This is a city-scoped unique borrower count, not the overall share total."
        )
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_ITM_COUNT_BY_CITY_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            answer=answer,
            metric_value=f"{count_int:,}",
        )

    state_scope = scope if isinstance(scope, tuple) else None
    sql_query = _CANONICAL_ITM_COUNT_BY_STATE_SQL if state_scope else _CANONICAL_ITM_COUNT_SQL
    params: dict[str, Any] | None = {"state": state_scope[1]} if state_scope else None
    try:
        row = sql_client.execute_one(sql_query, params) or {}
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_metric_failed", exc=exc)
        return None
    raw_count = row.get("in_the_money_borrowers")
    if raw_count is None:
        _emit_genie_warning(
            "direct_canonical_genie_metric_bad_count",
            value_type="NoneType",
        )
        return None
    try:
        count_int = int(raw_count)
    except (TypeError, ValueError):
        _emit_genie_warning(
            "direct_canonical_genie_metric_bad_count",
            value_type=type(row.get("in_the_money_borrowers")).__name__,
        )
        return None

    count_rows: list[dict[str, Any]] = [
        {"in_the_money_borrowers": count_int, "refreshed_at": row.get("refreshed_at")}
    ]
    if state_scope:
        count_rows[0]["state"] = state_scope[1]
    geo_text = f" in {state_scope[0]} ({state_scope[1]})" if state_scope else ""
    answer = (
        f"There are {count_int:,} borrowers passing the refinance-economics screen{geo_text}. "
        f"This is a unique borrower count from {borrower_asset} at the "
        "gold borrower grain, so multi-segment borrowers are counted once. It is broader "
        "than the marketing-eligible Lead Queue or any eligible-only Segment page filter."
    )
    return trusted_response(
        question=question,
        sql_query=sql_query,
        trusted_assets=trusted_assets,
        rows=count_rows,
        answer=answer,
        metric_value=f"{count_int:,}",
    )

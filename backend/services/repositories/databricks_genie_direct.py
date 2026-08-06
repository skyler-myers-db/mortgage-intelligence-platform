"""Direct trusted-SQL answers for narrow canonical Genie questions."""

from __future__ import annotations

import time
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
    _total_matching_from_rows,
)
from backend.services.repositories.databricks_genie_canonical import (
    _CANONICAL_ADDRESSABLE_MARKET_SQL,
    _CANONICAL_APPROVAL_TREND_30D_SQL,
    _CANONICAL_CASH_OUT_TOP_STATE_SQL,
    _CANONICAL_CURRENT_CUSTOMER_RETENTION_RISK_SQL,
    _CANONICAL_EQUITY_THRESHOLD_COUNT_SQL,
    _CANONICAL_EQUITY_THRESHOLD_STRICT_COUNT_SQL,
    _CANONICAL_EVIDENCE_EVENTS_THIS_QUARTER_SQL,
    _CANONICAL_EVIDENCE_EVENTS_YESTERDAY_SQL,
    _CANONICAL_HELOC_COUNT_SQL,
    _CANONICAL_HELOC_RECOMMENDATION_BORROWERS_SQL,
    _CANONICAL_HELOC_TOP_ZIPS_SQL,
    _CANONICAL_HOME_EQUITY_DISTRIBUTION_SQL,
    _CANONICAL_INVESTOR_COUNT_SQL,
    _CANONICAL_INVESTOR_SEGMENT_BY_STATE_SQL,
    _CANONICAL_INVESTOR_TOP_BY_RELATED_PROPERTY_SQL,
    _CANONICAL_ITM_BY_STATE_SQL,
    _CANONICAL_ITM_COUNT_AVG_SPREAD_SQL,
    _CANONICAL_ITM_COUNT_BY_CITY_SQL,
    _CANONICAL_ITM_COUNT_BY_STATE_SQL,
    _CANONICAL_ITM_COUNT_SQL,
    _CANONICAL_ITM_OFFER_MIX_SQL,
    _CANONICAL_ITM_SHARE_SQL,
    _CANONICAL_ITM_TOP_LEAD_QUEUE_ZIPS_SQL,
    _CANONICAL_ITM_TOP_TIER_COMPARE_SQL,
    _CANONICAL_ITM_TOP_ZIPS_SQL,
    _CANONICAL_LEAD_SCORE_WEEKLY_DISTRIBUTION_SQL,
    _CANONICAL_LISTED_BY_PRODUCT_RATE_SQL,
    _CANONICAL_LISTED_COUNT_BY_STATE_SQL,
    _CANONICAL_LISTED_COUNT_SQL,
    _CANONICAL_LISTED_DAYS_ON_MARKET_BY_STATE_SQL,
    _CANONICAL_LISTED_PURCHASE_TOP_SQL,
    _CANONICAL_LOCKIN_BY_STATE_SQL,
    _CANONICAL_LOCKIN_COHORT_SIZE_SQL,
    _CANONICAL_LOCKIN_MEDIAN_RATE_SQL,
    _CANONICAL_MEAN_LEAD_SCORE_BY_STATE_SQL,
    _CANONICAL_MEAN_RATE_SPREAD_BY_SEGMENT_SQL,
    _CANONICAL_MSA_SCORE_SQL,
    _CANONICAL_NEGATIVE_EQUITY_COUNT_SQL,
    _CANONICAL_RANKED_LEAD_POPULATION_SQL,
    _CANONICAL_REFI_DRIVER_SQL,
    _CANONICAL_REFI_EQUITY_SIGNAL_COMPARE_SQL,
    _CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_BY_STATE_SQL,
    _CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_SQL,
    _CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_BY_STATE_SQL,
    _CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_GLOBAL_SQL,
    _CANONICAL_SEGMENT_APPROVAL_RATE_SQL,
    _CANONICAL_STRATEGY_BOARD_SQL,
    _CANONICAL_TOP_BORROWERS_ALL_SEGMENTS_SQL,
    _CANONICAL_TOP_BORROWERS_BY_STATE_INTENT_SQL,
    _CANONICAL_TOP_BORROWERS_BY_STATE_SQL,
    _CANONICAL_TOP_BORROWERS_GLOBAL_INTENT_SQL,
    _CANONICAL_TOP_BORROWERS_GLOBAL_SQL,
    _CANONICAL_TOP_CASH_OUT_BY_EQUITY_SQL,
    _CANONICAL_TOP_COHORTS_SQL,
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
    _canonical_itm_lead_queue_zip_scope,
    _canonical_itm_offer_mix_scope,
    _canonical_itm_share_scope,
    _canonical_itm_state_breakdown_scope,
    _canonical_itm_state_scope,
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
    _format_pct_threshold,
    _projected_monthly_savings_gap_scope,
    _retention_competitor_lien_list_question,
    _retention_eligibility_fallback_from_summary,
    _retention_risk_question,
    _specific_top_borrower_intent_label,
    _specific_top_borrower_intent_note,
    _specific_top_borrower_sort_label,
    compose_all_segments_brief,
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
from backend.services.scoring import HIGH_OPPORTUNITY_THRESHOLD, offer_display_label

_SEGMENT_DISPLAY_LABELS = {
    "itm": "Prime Refi Candidates",
    "equity": "Home Equity Candidate",
    "investor": "Investor / Multi-Property",
    "retention": "Retention Risk",
    "listed": "Listed for Sale",
    "permit": "HELOC Intent",
}


def _segment_display_label(value: object) -> str:
    raw = str(value or "").strip()
    return _SEGMENT_DISPLAY_LABELS.get(raw, raw or "all segments")


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


def _data_gap_response(
    *,
    question: str,
    answer: str,
    trusted_assets: list[str],
    known_data_gaps: list[str],
) -> GenieMessageResponse:
    question_hash = _genie_question_hash(question)
    message_id = f"data-gap-{question_hash}"
    return GenieMessageResponse(
        conversation_id="",
        message_id=message_id,
        elapsed_ms=0,
        question_hash=question_hash,
        question=question,
        answer=answer,
        source="data_gap",
        trusted_assets=trusted_assets,
        row_count=0,
        proof=GenieProof(
            source_assets=trusted_assets,
            row_count=0,
            trusted=False,
            known_data_gaps=known_data_gaps,
            conversation_id=None,
            message_id=message_id,
        ),
        table_rows=[],
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

    if _canonical_itm_count_avg_spread_scope(question):
        try:
            row = sql_client.execute_one(_CANONICAL_ITM_COUNT_AVG_SPREAD_SQL) or {}
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_itm_avg_spread_failed", exc=exc)
            return None
        raw_count = row.get("in_the_money_borrowers")
        if raw_count is None:
            _emit_genie_warning(
                "direct_canonical_genie_itm_avg_spread_bad_count",
                value_type="NoneType",
            )
            return None
        try:
            count_int = int(raw_count)
        except (TypeError, ValueError):
            _emit_genie_warning(
                "direct_canonical_genie_itm_avg_spread_bad_count",
                value_type=type(raw_count).__name__,
            )
            return None
        avg_spread = row.get("avg_rate_spread_bps")
        try:
            avg_spread_float = float(avg_spread) if avg_spread is not None else None
        except (TypeError, ValueError):
            avg_spread_float = None
        rows = [
            {
                "in_the_money_borrowers": count_int,
                "avg_rate_spread_bps": avg_spread_float,
                "refreshed_at": row.get("refreshed_at"),
            }
        ]
        spread_text = (
            f"{avg_spread_float:,.1f} bps" if avg_spread_float is not None else "not available"
        )
        answer = (
            f"Across the current Cotality coverage, {count_int:,} borrowers pass the "
            f"refinance-economics screen. Their average rate spread is {spread_text}. "
            f"This is calculated at the unique borrower grain from {borrower_asset}; "
            "it is broader than the marketing-eligible Lead Queue or any eligible-only "
            "Segment page filter."
        )
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_ITM_COUNT_AVG_SPREAD_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            answer=answer,
            metric_value=f"{count_int:,}",
        )

    if _canonical_itm_share_scope(question):
        try:
            row = sql_client.execute_one(_CANONICAL_ITM_SHARE_SQL) or {}
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_itm_share_failed", exc=exc)
            return None
        raw_count = row.get("in_the_money_borrowers")
        raw_total = row.get("total_borrowers")
        if raw_count is None or raw_total is None:
            _emit_genie_warning("direct_canonical_genie_itm_share_bad_count")
            return None
        try:
            count_int = int(raw_count)
            total_int = int(raw_total)
        except (TypeError, ValueError):
            _emit_genie_warning("direct_canonical_genie_itm_share_bad_count")
            return None
        raw_share = row.get("borrower_share_pct")
        if raw_share is None:
            share_float = None
        else:
            try:
                share_float = float(raw_share)
            except (TypeError, ValueError):
                share_float = None
        rows = [
            {
                "in_the_money_borrowers": count_int,
                "total_borrowers": total_int,
                "borrower_share_pct": share_float,
                "refreshed_at": row.get("refreshed_at"),
            }
        ]
        share_text = f"{share_float:,.2f}%" if share_float is not None else "not available"
        answer = (
            f"{count_int:,} of {total_int:,} borrowers pass the refinance-economics "
            f"screen, or {share_text} of the current borrower coverage. This uses "
            f"{borrower_asset} at unique borrower grain and is broader than the "
            "marketing-eligible Lead Queue subset."
        )
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_ITM_SHARE_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            answer=answer,
            metric_value=share_text,
        )

    equity_scope = _canonical_equity_threshold_scope(question)
    if equity_scope is not None:
        sql_query = (
            _CANONICAL_EQUITY_THRESHOLD_STRICT_COUNT_SQL
            if equity_scope.strict_greater
            else _CANONICAL_EQUITY_THRESHOLD_COUNT_SQL
        )
        equity_params = {"min_equity_pct": equity_scope.threshold_pct}
        try:
            row = sql_client.execute_one(sql_query, equity_params) or {}
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_equity_threshold_failed", exc=exc)
            return None
        raw_count = row.get("equity_capacity_borrowers")
        if raw_count is None:
            _emit_genie_warning("direct_canonical_genie_equity_threshold_bad_count")
            return None
        try:
            count_int = int(raw_count)
            total_int = int(row.get("total_borrowers") or 0)
        except (TypeError, ValueError):
            _emit_genie_warning(
                "direct_canonical_genie_equity_threshold_bad_count",
                value_type=type(raw_count).__name__,
            )
            return None
        raw_share = row.get("borrower_share_pct")
        if raw_share is None:
            share_float = None
        else:
            try:
                share_float = float(raw_share)
            except (TypeError, ValueError):
                share_float = None
        raw_avg_equity = row.get("avg_equity_pct")
        try:
            avg_equity_float = float(raw_avg_equity) if raw_avg_equity is not None else None
        except (TypeError, ValueError):
            avg_equity_float = None
        rows = [
            {
                "equity_capacity_borrowers": count_int,
                "total_borrowers": total_int,
                "borrower_share_pct": share_float,
                "avg_equity_pct": avg_equity_float,
                "min_equity_pct": equity_scope.threshold_pct,
                "comparison": ">" if equity_scope.strict_greater else ">=",
                "refreshed_at": row.get("refreshed_at"),
            }
        ]
        comparison_text = "more than" if equity_scope.strict_greater else "at least"
        share_text = f"{share_float:,.2f}%" if share_float is not None else "not available"
        avg_equity_text = (
            f"{avg_equity_float:,.1f}%" if avg_equity_float is not None else "not available"
        )
        equity_metric_value = share_text if equity_scope.asks_share else f"{count_int:,}"
        population_text = (
            f" ({share_text} of {total_int:,} borrowers)" if total_int > 0 else ""
        )
        threshold_text = _format_pct_threshold(equity_scope.threshold_pct)
        answer = (
            f"{count_int:,} borrowers have {comparison_text} "
            f"{threshold_text}% modeled home equity"
            f"{population_text}. Their average modeled equity is "
            f"{avg_equity_text}. This is an equity-capacity screen from {borrower_asset}, "
            "not a filed-permit count."
        )
        return trusted_response(
            question=question,
            sql_query=sql_query,
            trusted_assets=trusted_assets,
            rows=rows,
            answer=answer,
            metric_value=equity_metric_value,
        )

    negative_equity_scope = _canonical_negative_equity_scope(question)
    if negative_equity_scope is not None:
        try:
            row = sql_client.execute_one(_CANONICAL_NEGATIVE_EQUITY_COUNT_SQL) or {}
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_negative_equity_failed", exc=exc)
            return None
        raw_count = row.get("underwater_borrowers")
        if raw_count is None:
            _emit_genie_warning("direct_canonical_genie_negative_equity_bad_count")
            return None
        try:
            count_int = int(raw_count)
            total_int = int(row.get("total_borrowers") or 0)
        except (TypeError, ValueError):
            _emit_genie_warning(
                "direct_canonical_genie_negative_equity_bad_count",
                value_type=type(raw_count).__name__,
            )
            return None
        raw_share = row.get("borrower_share_pct")
        try:
            share_float = float(raw_share) if raw_share is not None else None
        except (TypeError, ValueError):
            share_float = None
        raw_median_ltv = row.get("median_underwater_ltv_pct")
        try:
            median_ltv_float = float(raw_median_ltv) if raw_median_ltv is not None else None
        except (TypeError, ValueError):
            median_ltv_float = None
        raw_high_tail = row.get("high_ltv_tail_borrowers")
        try:
            high_tail_int = int(raw_high_tail) if raw_high_tail is not None else None
        except (TypeError, ValueError):
            high_tail_int = None
        share_text = f"{share_float:,.2f}%" if share_float is not None else "not available"
        median_ltv_text = (
            f"{median_ltv_float:,.1f}%" if median_ltv_float is not None else "not available"
        )
        tail_text = (
            f" {high_tail_int:,} records exceed 500% modeled LTV, so I am showing the median rather than an average over the long tail."
            if high_tail_int and high_tail_int > 0
            else ""
        )
        rows = [
            {
                "underwater_borrowers": count_int,
                "total_borrowers": total_int,
                "borrower_share_pct": share_float,
                "median_underwater_ltv_pct": median_ltv_float,
                "high_ltv_tail_borrowers": high_tail_int,
                "refreshed_at": row.get("refreshed_at"),
            }
        ]
        population_text = (
            f" ({share_text} of {total_int:,} borrowers)" if total_int > 0 else ""
        )
        answer = (
            f"{count_int:,} borrowers are underwater with modeled LTV above 100%"
            f"{population_text}. The median modeled LTV for those borrowers is "
            f"{median_ltv_text}.{tail_text} This uses {borrower_asset} at borrower "
            "grain; it is a portfolio-risk screen, not an outreach-ready Lead Queue count."
        )
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_NEGATIVE_EQUITY_COUNT_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            answer=answer,
            metric_value=share_text if negative_equity_scope.asks_share else f"{count_int:,}",
        )

    listed_count_scope = _canonical_listed_count_scope(question)
    if listed_count_scope is not None:
        sql_query = (
            _CANONICAL_LISTED_COUNT_BY_STATE_SQL
            if listed_count_scope.state_code
            else _CANONICAL_LISTED_COUNT_SQL
        )
        listed_params = (
            {"state": listed_count_scope.state_code}
            if listed_count_scope.state_code
            else None
        )
        try:
            row = sql_client.execute_one(sql_query, listed_params) or {}
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_listed_count_failed", exc=exc)
            return None
        raw_count = row.get("listed_borrowers")
        if raw_count is None:
            _emit_genie_warning("direct_canonical_genie_listed_count_bad_count")
            return None
        try:
            count_int = int(raw_count)
        except (TypeError, ValueError):
            _emit_genie_warning(
                "direct_canonical_genie_listed_count_bad_count",
                value_type=type(raw_count).__name__,
            )
            return None
        scope_text = (
            f" in {listed_count_scope.state_name} ({listed_count_scope.state_code})"
            if listed_count_scope.state_code and listed_count_scope.state_name
            else ""
        )
        rows = [
            {
                "listed_borrowers": count_int,
                "state": listed_count_scope.state_code,
                "refreshed_at": row.get("refreshed_at"),
            }
        ]
        answer = (
            f"{count_int:,} borrowers{scope_text} currently have a live listed-for-sale "
            f"signal in {borrower_asset}. This is the broad MLS/listing trigger count; "
            "Lead Queue actions may be smaller after marketing-eligibility and consent filters."
        )
        return trusted_response(
            question=question,
            sql_query=sql_query,
            trusted_assets=trusted_assets,
            rows=rows,
            answer=answer,
            metric_value=f"{count_int:,}",
        )

    if _canonical_investor_count_scope(question):
        try:
            row = sql_client.execute_one(_CANONICAL_INVESTOR_COUNT_SQL) or {}
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_investor_count_failed", exc=exc)
            return None
        raw_count = row.get("investor_borrowers")
        if raw_count is None:
            _emit_genie_warning("direct_canonical_genie_investor_count_bad_count")
            return None
        try:
            count_int = int(raw_count)
        except (TypeError, ValueError):
            _emit_genie_warning(
                "direct_canonical_genie_investor_count_bad_count",
                value_type=type(raw_count).__name__,
            )
            return None
        rows = [
            {
                "investor_borrowers": count_int,
                "refreshed_at": row.get("refreshed_at"),
            }
        ]
        answer = (
            f"{count_int:,} borrowers are in the Investor / Multi-Property segment "
            f"from {borrower_asset}. This uses Owner Link-derived segment membership "
            "at unique borrower grain."
        )
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_INVESTOR_COUNT_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            answer=answer,
            metric_value=f"{count_int:,}",
        )

    if _canonical_heloc_count_scope(question):
        try:
            row = sql_client.execute_one(_CANONICAL_HELOC_COUNT_SQL) or {}
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_heloc_count_failed", exc=exc)
            return None
        raw_count = row.get("equity_capacity_borrowers")
        if raw_count is None:
            _emit_genie_warning(
                "direct_canonical_genie_heloc_count_bad_count",
                value_type="NoneType",
            )
            return None
        try:
            count_int = int(raw_count)
        except (TypeError, ValueError):
            _emit_genie_warning(
                "direct_canonical_genie_heloc_count_bad_count",
                value_type=type(raw_count).__name__,
            )
            return None
        avg_equity = row.get("avg_equity_pct")
        try:
            avg_equity_float = float(avg_equity) if avg_equity is not None else None
        except (TypeError, ValueError):
            avg_equity_float = None
        rows = [
            {
                "equity_capacity_borrowers": count_int,
                "avg_equity_pct": avg_equity_float,
                "refreshed_at": row.get("refreshed_at"),
            }
        ]
        equity_text = (
            f"{avg_equity_float:,.1f}%" if avg_equity_float is not None else "not available"
        )
        answer = (
            f"Interpreting this as an equity-capacity screen, there are {count_int:,} "
            f"borrowers with at least 35% modeled home equity. Their average equity "
            f"is {equity_text}. This is not a filed-permit or HELOC-intent count; "
            f"it comes from {borrower_asset} and Building Permits are only used when "
            "that source is live."
        )
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_HELOC_COUNT_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            answer=answer,
            metric_value=f"{count_int:,}",
        )

    if _canonical_home_equity_distribution_scope(question):
        try:
            rows = _redact_genie_rows(
                sql_client.execute(_CANONICAL_HOME_EQUITY_DISTRIBUTION_SQL)
            ) or []
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_equity_distribution_failed", exc=exc)
            return None
        if rows:
            strongest = next(
                (row for row in rows if str(row.get("equity_band") or "") == "75%+"),
                rows[-1],
            )
            answer = (
                f"I grouped borrowers from {borrower_asset} into modeled home-equity bands. "
                "The 15% threshold is the baseline Portfolio Builder equity screen; "
                "35% and higher is the home-equity capacity screen used for HELOC/cash-out "
                "analysis. "
                f"The strongest-equity band shown is {strongest.get('equity_band')} with "
                f"{int(strongest.get('borrowers') or 0):,} borrowers."
            )
        else:
            answer = (
                f"{borrower_asset} returned no home-equity distribution rows for the "
                "current refreshed coverage."
            )
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_HOME_EQUITY_DISTRIBUTION_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            answer=answer,
        )

    if _canonical_addressable_market_scope(question):
        try:
            row = sql_client.execute_one(_CANONICAL_ADDRESSABLE_MARKET_SQL) or {}
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_addressable_market_failed", exc=exc)
            return None
        raw_count = row.get("marketable_population")
        if raw_count is None:
            _emit_genie_warning(
                "direct_canonical_genie_addressable_market_bad_count",
                value_type="NoneType",
            )
            return None
        try:
            count_int = int(raw_count)
        except (TypeError, ValueError):
            _emit_genie_warning(
                "direct_canonical_genie_addressable_market_bad_count",
                value_type=type(raw_count).__name__,
            )
            return None
        rows = [
            {
                "marketable_population": count_int,
                "definition": (
                    "Portfolio Builder default: owner-occupied, open first lien, "
                    "marketing eligible, at least 15% modeled equity"
                ),
                "refreshed_at": row.get("refreshed_at"),
            }
        ]
        answer = (
            f"The current addressable market is {count_int:,} borrowers at the "
            f"Portfolio Builder grain in {borrower_asset}. This matches the default "
            "Portfolio Builder denominator: owner-occupied properties, open first "
            "lien, marketing eligible, and at least 15% modeled equity. It is not "
            f"the narrower ranked Lead Queue subset in {lead_population_asset}."
        )
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_ADDRESSABLE_MARKET_SQL,
            trusted_assets=[borrower_asset],
            rows=rows,
            answer=answer,
            metric_value=f"{count_int:,}",
        )

    if _canonical_ranked_lead_population_scope(question):
        try:
            row = sql_client.execute_one(_CANONICAL_RANKED_LEAD_POPULATION_SQL) or {}
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_ranked_lead_population_failed", exc=exc)
            return None
        raw_count = row.get("ranked_leads")
        if not isinstance(raw_count, int | float | str):
            _emit_genie_warning(
                "direct_canonical_genie_ranked_lead_population_bad_count",
                value_type=type(raw_count).__name__,
            )
            return None
        try:
            count_int = int(raw_count)
        except ValueError:
            _emit_genie_warning(
                "direct_canonical_genie_ranked_lead_population_bad_count",
                value_type=type(raw_count).__name__,
            )
            return None
        rows = [{"ranked_leads": count_int, "refreshed_at": row.get("refreshed_at")}]
        answer = (
            f"The ranked Lead Queue subset has {count_int:,} marketing-eligible "
            f"leads in {lead_population_asset}. Use this number for operational "
            "queue sizing; use the addressable-market answer for the broader "
            "Portfolio Builder denominator."
        )
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_RANKED_LEAD_POPULATION_SQL,
            trusted_assets=[lead_population_asset],
            rows=rows,
            answer=answer,
            metric_value=f"{count_int:,}",
        )

    specific_top_borrowers_state_scope = _canonical_specific_top_borrowers_state_scope(question)
    if specific_top_borrowers_state_scope is not None:
        intent, state_name, state_code = specific_top_borrowers_state_scope
        sql_query = _CANONICAL_TOP_BORROWERS_BY_STATE_INTENT_SQL[intent]
        intent_label = _specific_top_borrower_intent_label(intent)
        sort_label = _specific_top_borrower_sort_label(intent)
        try:
            rows = _redact_genie_rows(sql_client.execute(sql_query, {"state": state_code})) or []
        except DatabricksSqlError as exc:
            _emit_genie_warning(
                "direct_canonical_genie_specific_top_borrowers_state_failed",
                intent=intent,
                exc=exc,
            )
            return None
        if rows:
            top = rows[0]
            intent_note = _specific_top_borrower_intent_note(question, intent)
            answer = (
                f"I ranked the top {len(rows)} {state_name} ({state_code}) "
                f"{intent_label} borrowers from {borrower_asset}, ordered by "
                f"{sort_label}. The current first borrower is masked "
                f"{top.get('borrower_id')} with opportunity score "
                f"{int(top.get('opportunity_score') or 0):,}.{intent_note}"
            )
            response_sql_query = sql_query
            response_rows = rows
            suppress_actions = False
            response_metric_value = None
        else:
            response_sql_query = sql_query
            response_rows = rows
            suppress_actions = False
            response_metric_value = None
            retention_fallback = None
            if intent == "retention":
                try:
                    summary_rows = (
                        _redact_genie_rows(
                            sql_client.execute(
                                _CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_BY_STATE_SQL,
                                {"state": state_code},
                            )
                        )
                        or []
                    )
                except DatabricksSqlError as exc:
                    _emit_genie_warning(
                        "direct_canonical_genie_retention_eligibility_summary_state_failed",
                        exc=exc,
                    )
                    summary_rows = []
                retention_fallback = _retention_eligibility_fallback_from_summary(
                    summary_rows,
                    state_name=state_name,
                    state_code=state_code,
                )
                if retention_fallback is not None:
                    answer = retention_fallback.answer
                    response_sql_query = retention_fallback.sql_query
                    response_rows = retention_fallback.rows
                    suppress_actions = retention_fallback.suppress_actions
                    response_metric_value = retention_fallback.metric_value
                else:
                    answer = (
                        f"The trusted borrower table returned no marketing-eligible "
                        f"{intent_label} borrowers in {state_name} ({state_code}) for "
                        "the current refreshed coverage."
                    )
            else:
                answer = (
                    f"The trusted borrower table returned no marketing-eligible "
                    f"{intent_label} borrowers in {state_name} ({state_code}) for "
                    "the current refreshed coverage."
                )
        return trusted_response(
            question=question,
            sql_query=response_sql_query,
            trusted_assets=[borrower_asset],
            rows=response_rows,
            answer=answer,
            metric_value=response_metric_value,
            suppress_actions=suppress_actions,
        )

    specific_top_borrowers_global_scope = _canonical_specific_top_borrowers_global_scope(question)
    if specific_top_borrowers_global_scope is not None:
        intent = specific_top_borrowers_global_scope
        sql_query = _CANONICAL_TOP_BORROWERS_GLOBAL_INTENT_SQL[intent]
        intent_label = _specific_top_borrower_intent_label(intent)
        sort_label = _specific_top_borrower_sort_label(intent)
        try:
            rows = _redact_genie_rows(sql_client.execute(sql_query)) or []
        except DatabricksSqlError as exc:
            _emit_genie_warning(
                "direct_canonical_genie_specific_top_borrowers_global_failed",
                intent=intent,
                exc=exc,
            )
            return None
        if rows:
            top = rows[0]
            intent_note = _specific_top_borrower_intent_note(question, intent)
            answer = (
                f"I ranked the top {len(rows)} {intent_label} borrowers across the "
                f"current refreshed coverage from {borrower_asset}, ordered by "
                f"{sort_label}. The current first borrower is masked "
                f"{top.get('borrower_id')} with opportunity score "
                f"{int(top.get('opportunity_score') or 0):,}.{intent_note}"
            )
            response_sql_query = sql_query
            response_rows = rows
            suppress_actions = False
            response_metric_value = None
        else:
            response_sql_query = sql_query
            response_rows = rows
            suppress_actions = False
            response_metric_value = None
            retention_fallback = None
            if intent == "retention":
                try:
                    summary_rows = (
                        _redact_genie_rows(
                            sql_client.execute(_CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_GLOBAL_SQL)
                        )
                        or []
                    )
                except DatabricksSqlError as exc:
                    _emit_genie_warning(
                        "direct_canonical_genie_retention_eligibility_summary_global_failed",
                        exc=exc,
                    )
                    summary_rows = []
                retention_fallback = _retention_eligibility_fallback_from_summary(summary_rows)
                if retention_fallback is not None:
                    answer = retention_fallback.answer
                    response_sql_query = retention_fallback.sql_query
                    response_rows = retention_fallback.rows
                    suppress_actions = retention_fallback.suppress_actions
                    response_metric_value = retention_fallback.metric_value
                else:
                    answer = (
                        f"The trusted borrower table returned no marketing-eligible "
                        f"{intent_label} borrowers for the current refreshed coverage."
                    )
            else:
                answer = (
                    f"The trusted borrower table returned no marketing-eligible "
                    f"{intent_label} borrowers for the current refreshed coverage."
                )
        return trusted_response(
            question=question,
            sql_query=response_sql_query,
            trusted_assets=[borrower_asset],
            rows=response_rows,
            answer=answer,
            metric_value=response_metric_value,
            suppress_actions=suppress_actions,
        )

    top_borrower_state_scope = _canonical_top_borrowers_state_scope(question)
    if top_borrower_state_scope is not None:
        state_name, state_code = top_borrower_state_scope
        try:
            rows = (
                _redact_genie_rows(
                    sql_client.execute(
                        _CANONICAL_TOP_BORROWERS_BY_STATE_SQL,
                        {"state": state_code},
                    )
                )
                or []
            )
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_top_borrowers_state_failed", exc=exc)
            return None
        if rows:
            top = rows[0]
            answer = (
                f"I ranked the top {len(rows)} {state_name} ({state_code}) borrowers "
                f"by lead score from {lead_population_asset}. "
                f"The current leader is masked borrower {top.get('borrower_id')} "
                f"with lead score {int(top.get('lead_score') or 0):,}."
            )
        else:
            answer = (
                f"The trusted lead population returned no {state_name} ({state_code}) "
                "borrowers for the current refreshed data coverage."
            )
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_TOP_BORROWERS_BY_STATE_SQL,
            trusted_assets=[lead_population_asset],
            rows=rows,
            answer=answer,
        )

    if _canonical_top_borrowers_all_segments_scope(question):
        try:
            rows = (
                _redact_genie_rows(sql_client.execute(_CANONICAL_TOP_BORROWERS_ALL_SEGMENTS_SQL))
                or []
            )
        except DatabricksSqlError as exc:
            _emit_genie_warning(
                "direct_canonical_genie_top_borrowers_all_segments_failed", exc=exc
            )
            return None
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_TOP_BORROWERS_ALL_SEGMENTS_SQL,
            trusted_assets=[borrower_asset],
            rows=rows,
            answer=compose_all_segments_brief(rows, borrower_asset),
        )

    if _canonical_top_borrowers_global_scope(question):
        try:
            rows = (
                _redact_genie_rows(sql_client.execute(_CANONICAL_TOP_BORROWERS_GLOBAL_SQL))
                or []
            )
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_top_borrowers_global_failed", exc=exc)
            return None
        if rows:
            top = rows[0]
            answer = (
                f"I ranked the top {len(rows)} marketing-eligible borrowers by lead score "
                f"from {lead_population_asset}. The current first borrower is masked "
                f"{top.get('borrower_id')} with lead score {int(top.get('lead_score') or 0):,}."
            )
        else:
            answer = (
                "The ranked lead population returned no marketing-eligible borrower rows "
                "for the current refreshed coverage."
            )
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_TOP_BORROWERS_GLOBAL_SQL,
            trusted_assets=[lead_population_asset],
            rows=rows,
            answer=answer,
        )

    if _canonical_heloc_zip_scope(question):
        try:
            rows = _redact_genie_rows(sql_client.execute(_CANONICAL_HELOC_TOP_ZIPS_SQL)) or []
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_heloc_zips_failed", exc=exc)
            return None
        if rows:
            top = rows[0]
            answer = (
                "I ranked ZIP codes by borrowers with modeled equity at or above "
                f"35% from {borrower_asset}. "
                f"The current leader is ZIP {top.get('zip')} ({top.get('state')}) "
                f"with {int(top.get('equity_capacity_borrowers') or 0):,} borrowers. "
                "This is an equity-capacity view, not a filed-permit or HELOC-intent "
                "count; Building Permits are only used when that source is live."
            )
        else:
            answer = (
                "The trusted borrower table returned no ZIP rows with modeled "
                "equity at or above 35% for the current refreshed data coverage. "
                "Building Permits signals remain pending and are not treated as "
                "zero demand."
            )
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_HELOC_TOP_ZIPS_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            answer=answer,
        )

    if _canonical_strategy_board_scope(question):
        try:
            rows = _redact_genie_rows(sql_client.execute(_CANONICAL_STRATEGY_BOARD_SQL)) or []
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_strategy_board_failed", exc=exc)
            return None
        if rows:
            top = rows[0]
            top_segment = _segment_display_label(top.get("segment_code"))
            top_offer = offer_display_label(
                str(top.get("leading_offer_code") or ""),
                str(top.get("leading_recommended_offer") or ""),
            )
            answer = (
                f"Use {borrower_asset} to prioritize the next 10,000 outreach touches "
                "by state, segment, and offer. "
                f"The top lane is state {top.get('state')}, {top_segment}, with "
                f"{int(top.get('marketable_borrowers') or 0):,} marketable borrowers "
                f"and primary offer {top_offer}. "
                "The table ranks the remaining state-segment-offer lanes by average "
                "opportunity score and marketable borrower volume."
            )
        else:
            answer = (
                "The trusted borrower table returned no opt-in, marketing-eligible "
                "state-segment-offer lanes for the current refreshed data coverage."
            )
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_STRATEGY_BOARD_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            answer=answer,
        )

    if _canonical_cash_out_state_scope(question):
        try:
            rows = _redact_genie_rows(sql_client.execute(_CANONICAL_CASH_OUT_TOP_STATE_SQL)) or []
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_cash_out_state_failed", exc=exc)
            return None
        metric_value: str | None = None
        if rows:
            top = rows[0]
            count_int = int(top.get("cash_out_borrowers") or 0)
            metric_value = f"{count_int:,}"
            answer = (
                f"{top.get('state')} has the most cash-out opportunity right now "
                f"with {count_int:,} borrowers. This counts borrowers whose "
                f"primary offer is a cash-out refinance review at the unique "
                f"borrower grain from {borrower_asset}."
            )
        else:
            answer = (
                "The trusted borrower table returned no cash-out state rows for "
                "the current refreshed data coverage."
            )
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_CASH_OUT_TOP_STATE_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            answer=answer,
            metric_value=metric_value,
        )

    if _canonical_top_cash_out_by_equity_scope(question):
        try:
            rows = (
                _redact_genie_rows(sql_client.execute(_CANONICAL_TOP_CASH_OUT_BY_EQUITY_SQL))
                or []
            )
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_top_cash_out_by_equity_failed", exc=exc)
            return None
        if rows:
            top = rows[0]
            answer = (
                f"I ranked the top {len(rows)} cash-out or home-equity candidates by "
                f"estimated equity from {borrower_asset}. The first masked borrower is "
                f"{top.get('borrower_id')} with ${int(top.get('equity_estimate') or 0):,} "
                "estimated equity."
            )
        else:
            answer = (
                "The trusted borrower table returned no marketing-eligible cash-out or "
                "home-equity candidates for the current refreshed coverage."
            )
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_TOP_CASH_OUT_BY_EQUITY_SQL,
            trusted_assets=[borrower_asset],
            rows=rows,
            answer=answer,
        )

    if _canonical_listed_purchase_scope(question):
        try:
            rows = (
                _redact_genie_rows(sql_client.execute(_CANONICAL_LISTED_PURCHASE_TOP_SQL))
                or []
            )
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_listed_purchase_failed", exc=exc)
            return None
        listed_assets = [borrower_asset]
        if rows:
            top = rows[0]
            top_offer = offer_display_label(
                str(top.get("recommended_offer_code") or ""),
                str(top.get("recommended_offer") or ""),
            )
            answer = (
                f"I ranked the top {len(rows)} marketing-eligible listed-for-sale borrowers "
                f"from {borrower_asset}. The current first borrower is masked "
                f"{top.get('borrower_id')} in {top.get('city')}, {top.get('state')} "
                f"with opportunity score {int(top.get('opportunity_score') or 0):,}. "
                f"Lead with {top_offer} only after review in the governed outreach workflow."
            )
        else:
            answer = (
                "The trusted borrower table returned no marketing-eligible listed-for-sale "
                "borrowers for the current refreshed coverage."
            )
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_LISTED_PURCHASE_TOP_SQL,
            trusted_assets=listed_assets,
            rows=rows,
            answer=answer,
        )

    if _canonical_investor_top_by_related_property_scope(question):
        try:
            rows = (
                _redact_genie_rows(
                    sql_client.execute(_CANONICAL_INVESTOR_TOP_BY_RELATED_PROPERTY_SQL)
                )
                or []
            )
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_investor_top_properties_failed", exc=exc)
            return None
        if rows:
            top = rows[0]
            answer = (
                f"I ranked the top {len(rows)} Investor / Multi-Property borrowers by related "
                f"property count from {borrower_asset}. The first masked borrower is "
                f"{top.get('borrower_id')} with {int(top.get('related_property_count') or 0):,} "
                "related properties."
            )
        else:
            answer = (
                "The trusted borrower table returned no marketing-eligible Investor / "
                "Multi-Property rows with related property count >= 2."
            )
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_INVESTOR_TOP_BY_RELATED_PROPERTY_SQL,
            trusted_assets=[borrower_asset],
            rows=rows,
            answer=answer,
        )

    if _canonical_refi_equity_signal_compare_scope(question):
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

    if _canonical_refi_driver_scope(question):
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

    if _canonical_itm_top_tier_compare_scope(question):
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

    if _canonical_investor_segment_by_state_scope(question):
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

    if _canonical_mean_rate_spread_by_segment_scope(question):
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

    if _canonical_segment_approval_rate_scope(question):
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

    if _canonical_mean_lead_score_by_state_scope(question):
        try:
            rows = (
                _redact_genie_rows(sql_client.execute(_CANONICAL_MEAN_LEAD_SCORE_BY_STATE_SQL))
                or []
            )
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_mean_score_state_failed", exc=exc)
            return None
        if rows:
            top = rows[0]
            answer = (
                f"I compared mean lead score by state from {borrower_asset}. "
                f"{top.get('state')} currently leads with average score "
                f"{float(top.get('avg_lead_score') or 0):,.1f}."
            )
        else:
            answer = "The trusted borrower table returned no state rows for lead-score comparison."
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_MEAN_LEAD_SCORE_BY_STATE_SQL,
            trusted_assets=[borrower_asset],
            rows=rows,
            answer=answer,
        )

    if _canonical_evidence_events_yesterday_scope(question):
        try:
            rows = (
                _redact_genie_rows(sql_client.execute(_CANONICAL_EVIDENCE_EVENTS_YESTERDAY_SQL))
                or []
            )
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_evidence_yesterday_failed", exc=exc)
            return None
        answer = (
            f"I grouped yesterday's evidence events by trigger type from {evidence_asset}."
            if rows
            else f"{evidence_asset} recorded no evidence events yesterday; I am not treating "
            "that as zero borrower demand, only as a trigger-volume readout for that date."
        )
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_EVIDENCE_EVENTS_YESTERDAY_SQL,
            trusted_assets=[evidence_asset],
            rows=rows,
            answer=answer,
        )

    if _canonical_lead_score_weekly_distribution_scope(question):
        try:
            rows = (
                _redact_genie_rows(
                    sql_client.execute(_CANONICAL_LEAD_SCORE_WEEKLY_DISTRIBUTION_SQL)
                )
                or []
            )
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_weekly_score_distribution_failed", exc=exc)
            return None
        if len(rows) >= 2:
            answer = (
                f"I compared this week's and last week's average opportunity score from "
                f"{funnel_asset}. Review the table for the two weekly buckets."
            )
        else:
            answer = (
                f"{funnel_asset} does not yet have two weekly national snapshots in the "
                "last 14 days, so I cannot make a week-over-week distribution claim."
            )
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_LEAD_SCORE_WEEKLY_DISTRIBUTION_SQL,
            trusted_assets=[funnel_asset],
            rows=rows,
            answer=answer,
        )

    if _canonical_approval_trend_30d_scope(question):
        try:
            rows = (
                _redact_genie_rows(sql_client.execute(_CANONICAL_APPROVAL_TREND_30D_SQL))
                or []
            )
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_approval_trend_failed", exc=exc)
            return None
        if rows:
            answer = (
                f"I pulled the approval trend from {funnel_asset} for the last 30 days. "
                "The table shows daily approvals at the national funnel grain."
            )
        else:
            answer = f"{funnel_asset} returned no national approval snapshots in the last 30 days."
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_APPROVAL_TREND_30D_SQL,
            trusted_assets=[funnel_asset],
            rows=rows,
            answer=answer,
        )

    if _canonical_evidence_events_quarter_scope(question):
        try:
            rows = (
                _redact_genie_rows(
                    sql_client.execute(_CANONICAL_EVIDENCE_EVENTS_THIS_QUARTER_SQL)
                )
                or []
            )
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_evidence_quarter_failed", exc=exc)
            return None
        answer = (
            f"I grouped quarter-to-date evidence events by trigger type from {evidence_asset}."
            if rows
            else f"{evidence_asset} returned no quarter-to-date evidence events."
        )
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_EVIDENCE_EVENTS_THIS_QUARTER_SQL,
            trusted_assets=[evidence_asset],
            rows=rows,
            answer=answer,
        )

    if _canonical_itm_offer_mix_scope(question):
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

    if _projected_monthly_savings_gap_scope(question):
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

    if _canonical_heloc_recommendation_borrowers_scope(question):
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

    if _canonical_listed_by_product_rate_scope(question):
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

    if _canonical_listed_days_on_market_by_state_scope(question):
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

    if _canonical_lockin_size_scope(question):
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

    if _canonical_lockin_median_rate_scope(question):
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

    if _canonical_lockin_by_state_scope(question):
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

    if _canonical_top_cohorts_scope(question):
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

    if _retention_competitor_lien_list_question(question):
        state_scope = _canonical_itm_state_scope(question)
        sql_query = (
            _CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_BY_STATE_SQL
            if state_scope is not None
            else _CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_SQL
        )
        parameters = {"state": state_scope[1]} if state_scope is not None else None
        scope_phrase = f" in {state_scope[0]}" if state_scope is not None else ""
        try:
            rows = (
                _redact_genie_rows(
                    sql_client.execute(sql_query, parameters)
                )
                or []
            )
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_retention_competitor_lien_failed", exc=exc)
            return None
        total_matching = _total_matching_from_rows(rows)
        shown_count = len(rows)
        if rows and total_matching > shown_count:
            answer = (
                f"There are {total_matching:,} retention-list borrowers{scope_phrase} with "
                f"competitor-lien evidence in the last 30 days; showing the first "
                f"{shown_count:,} by latest evidence timestamp and opportunity score. "
                f"The result uses the governed `competitor_lien` signal_type from {evidence_asset}."
            )
        elif rows:
            answer = (
                f"I found {shown_count:,} retention-list borrowers{scope_phrase} with competitor-lien "
                f"evidence in the last 30 days from {evidence_asset}."
            )
        else:
            answer = (
                f"No retention-list borrowers{scope_phrase} have governed competitor-lien evidence "
                "in the last 30 days. This is a live result from the modeled "
                "`competitor_lien` signal_type, not a stale `lien-change` alias."
            )
        return trusted_response(
            question=question,
            sql_query=sql_query,
            trusted_assets=[borrower_asset, evidence_asset],
            rows=rows,
            answer=answer,
            metric_value=f"{total_matching:,}",
        )

    if _retention_risk_question(question):
        try:
            row = sql_client.execute_one(_CANONICAL_CURRENT_CUSTOMER_RETENTION_RISK_SQL) or {}
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_retention_risk_failed", exc=exc)
            return None
        raw_count = row.get("retention_risk_borrowers")
        if raw_count is None:
            _emit_genie_warning(
                "direct_canonical_genie_retention_risk_bad_count",
                value_type="NoneType",
            )
            return None
        try:
            count_int = int(raw_count)
        except (TypeError, ValueError):
            _emit_genie_warning(
                "direct_canonical_genie_retention_risk_bad_count",
                value_type=type(raw_count).__name__,
            )
            return None
        rows = [
            {
                "retention_risk_borrowers": count_int,
                "refreshed_at": row.get("refreshed_at"),
            }
        ]
        answer = (
            f"There are {count_int:,} current customers in the retention-risk cohort. "
            f"This uses the modeled retention signal in {borrower_asset}."
        )
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_CURRENT_CUSTOMER_RETENTION_RISK_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            answer=answer,
            metric_value=f"{count_int:,}",
        )

    if _canonical_itm_zip_scope(question):
        lead_queue_scope = _canonical_itm_lead_queue_zip_scope(question)
        zip_sql = (
            _CANONICAL_ITM_TOP_LEAD_QUEUE_ZIPS_SQL
            if lead_queue_scope
            else _CANONICAL_ITM_TOP_ZIPS_SQL
        )
        try:
            rows = _redact_genie_rows(sql_client.execute(zip_sql)) or []
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_itm_zips_failed", exc=exc)
            return None
        zip_trusted_assets = [lead_population_asset] if lead_queue_scope else [borrower_asset]
        if rows:
            top = rows[0]
            count_key = "in_the_money_leads" if lead_queue_scope else "in_the_money_borrowers"
            grain_text = (
                f"the ranked Lead Queue subset in {lead_population_asset}"
                if lead_queue_scope
                else f"the current borrower coverage in {borrower_asset}"
            )
            answer = (
                "I ranked ZIP codes by unique records passing the refinance-economics screen "
                f"from {grain_text}. "
                f"The current leader is ZIP {top.get('zip')} ({top.get('state')}) "
                f"with {int(top.get(count_key) or 0):,} "
                f"{'leads' if lead_queue_scope else 'borrowers'}."
            )
        else:
            answer = (
                "The trusted population returned no refinance-economics ZIP rows for "
                "the requested grain."
            )
        return trusted_response(
            question=question,
            sql_query=zip_sql,
            trusted_assets=zip_trusted_assets,
            rows=rows,
            answer=answer,
        )

    if _canonical_itm_state_breakdown_scope(question):
        try:
            rows = _redact_genie_rows(sql_client.execute(_CANONICAL_ITM_BY_STATE_SQL)) or []
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_itm_state_breakdown_failed", exc=exc)
            return None
        if rows:
            top = rows[0]
            broad_total = sum(int(row.get("in_the_money_borrowers") or 0) for row in rows)
            lead_queue_total = sum(int(row.get("lead_queue_borrowers") or 0) for row in rows)
            answer = (
                "I broke down borrowers passing the refinance-economics screen by state from "
                f"{borrower_asset}. "
                f"{top.get('state')} currently leads with "
                f"{int(top.get('in_the_money_borrowers') or 0):,} borrowers. "
                f"Across the returned states, {broad_total:,} borrowers pass the broad "
                f"economic screen; the Lead Queue action opens the {lead_queue_total:,} "
                "marketing-eligible subset after operational eligibility filters."
            )
        else:
            answer = (
                "The trusted borrower table returned no refinance-economics state rows "
                "for the current refreshed data coverage."
            )
        return trusted_response(
            question=question,
            sql_query=_CANONICAL_ITM_BY_STATE_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            answer=answer,
        )

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

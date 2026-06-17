"""Direct trusted-SQL answers for narrow canonical Genie questions."""

from __future__ import annotations

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
    _CANONICAL_CASH_OUT_TOP_STATE_SQL,
    _CANONICAL_CURRENT_CUSTOMER_RETENTION_RISK_SQL,
    _CANONICAL_HELOC_COUNT_SQL,
    _CANONICAL_HELOC_TOP_ZIPS_SQL,
    _CANONICAL_INVESTOR_SEGMENT_BY_STATE_SQL,
    _CANONICAL_ITM_BY_STATE_SQL,
    _CANONICAL_ITM_COUNT_AVG_SPREAD_SQL,
    _CANONICAL_ITM_COUNT_BY_CITY_SQL,
    _CANONICAL_ITM_COUNT_BY_STATE_SQL,
    _CANONICAL_ITM_COUNT_SQL,
    _CANONICAL_ITM_TOP_TIER_COMPARE_SQL,
    _CANONICAL_ITM_TOP_ZIPS_SQL,
    _CANONICAL_LISTED_PURCHASE_TOP_SQL,
    _CANONICAL_MSA_SCORE_SQL,
    _CANONICAL_REFI_DRIVER_SQL,
    _CANONICAL_REFI_EQUITY_SIGNAL_COMPARE_SQL,
    _CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_SQL,
    _CANONICAL_STRATEGY_BOARD_SQL,
    _CANONICAL_TOP_BORROWERS_BY_STATE_SQL,
    _canonical_addressable_market_scope,
    _canonical_cash_out_state_scope,
    _canonical_heloc_count_scope,
    _canonical_heloc_zip_scope,
    _canonical_in_the_money_count_scope,
    _canonical_investor_segment_by_state_scope,
    _canonical_itm_city_scope,
    _canonical_itm_count_avg_spread_scope,
    _canonical_itm_state_breakdown_scope,
    _canonical_itm_top_tier_compare_scope,
    _canonical_itm_zip_scope,
    _canonical_listed_purchase_scope,
    _canonical_msa_score_scope,
    _canonical_refi_driver_scope,
    _canonical_refi_equity_signal_compare_scope,
    _canonical_strategy_board_scope,
    _canonical_top_borrowers_state_scope,
    _retention_competitor_lien_list_question,
    _retention_risk_question,
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
from backend.services.scoring import offer_display_label

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
) -> GenieMessageResponse:
    question_hash = _genie_question_hash(question)
    message_id = f"trusted-sql-{question_hash}"
    proof = _build_genie_proof(
        sql_query=sql_query,
        trusted_assets=trusted_assets,
        rows=rows,
        question=question,
        conversation_id="",
        message_id=message_id,
        elapsed_ms=0,
    )
    visualization = _plan_genie_visualization(question, rows)
    actions = _suggest_genie_actions(
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
        elapsed_ms=0,
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
    guide = _guide_response(question)
    if guide is not None:
        return guide
    if sql_client is None:
        return None
    borrower_asset = qualify("gold", "borrower_360")
    evidence_asset = qualify("gold", "evidence_events")
    lead_population_asset = qualify("gold", "lead_population")
    segment_population_asset = qualify("gold", "segment_population")
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
            f"This is calculated at the unique borrower grain from {borrower_asset}."
        )
        return _trusted_sql_response(
            question=question,
            sql_query=_CANONICAL_ITM_COUNT_AVG_SPREAD_SQL,
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
            f"borrowers with more than 35% modeled home equity. Their average equity "
            f"is {equity_text}. This is not a filed-permit or HELOC-intent count; "
            f"it comes from {borrower_asset} and Building Permits are only used when "
            "that source is live."
        )
        return _trusted_sql_response(
            question=question,
            sql_query=_CANONICAL_HELOC_COUNT_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            answer=answer,
            metric_value=f"{count_int:,}",
        )

    if _canonical_addressable_market_scope(question):
        try:
            row = sql_client.execute_one(_CANONICAL_ADDRESSABLE_MARKET_SQL) or {}
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_addressable_market_failed", exc=exc)
            return None
        raw_count = row.get("eligible_borrowers")
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
                "eligible_borrowers": count_int,
                "refreshed_at": row.get("refreshed_at"),
            }
        ]
        answer = (
            f"The current addressable market is {count_int:,} marketing-eligible, "
            f"opt-in borrowers in {lead_population_asset}. This is the action-ready "
            "lead population after suppression and consent filters, not the raw "
            "public-record universe."
        )
        return _trusted_sql_response(
            question=question,
            sql_query=_CANONICAL_ADDRESSABLE_MARKET_SQL,
            trusted_assets=[lead_population_asset],
            rows=rows,
            answer=answer,
            metric_value=f"{count_int:,}",
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
        return _trusted_sql_response(
            question=question,
            sql_query=_CANONICAL_TOP_BORROWERS_BY_STATE_SQL,
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
        return _trusted_sql_response(
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
        return _trusted_sql_response(
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
        return _trusted_sql_response(
            question=question,
            sql_query=_CANONICAL_CASH_OUT_TOP_STATE_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            answer=answer,
            metric_value=metric_value,
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
        return _trusted_sql_response(
            question=question,
            sql_query=_CANONICAL_LISTED_PURCHASE_TOP_SQL,
            trusted_assets=listed_assets,
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
        return _trusted_sql_response(
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
                "The refinance lane is driven by governed evidence, not a generic model summary. "
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
        return _trusted_sql_response(
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
            "Top-tier opportunity means opportunity_score >= 75, which blends economics, "
            "intent, fit, relationship, and evidence. In the current marketable set, "
            f"{rows[0]['in_the_money_borrowers']:,} borrowers are in-the-money, "
            f"{rows[0]['top_tier_borrowers']:,} are top-tier, and "
            f"{rows[0]['overlap_borrowers']:,} are both. Use the overlap for the cleanest "
            "refinance story; use top-tier outside in-the-money when another offer lane is stronger."
        )
        return _trusted_sql_response(
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
        return _trusted_sql_response(
            question=question,
            sql_query=_CANONICAL_INVESTOR_SEGMENT_BY_STATE_SQL,
            trusted_assets=[segment_population_asset],
            rows=rows,
            answer=answer,
        )

    if _retention_competitor_lien_list_question(question):
        try:
            rows = (
                _redact_genie_rows(
                    sql_client.execute(_CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_SQL)
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
                f"There are {total_matching:,} retention-list borrowers with "
                f"competitor-lien evidence in the last 30 days; showing the first "
                f"{shown_count:,} by latest evidence timestamp and opportunity score. "
                f"The result uses the governed `competitor_lien` signal_type from {evidence_asset}."
            )
        elif rows:
            answer = (
                f"I found {shown_count:,} retention-list borrowers with competitor-lien "
                f"evidence in the last 30 days from {evidence_asset}."
            )
        else:
            answer = (
                "No retention-list borrowers have governed competitor-lien evidence "
                "in the last 30 days. This is a live result from the modeled "
                "`competitor_lien` signal_type, not a stale `lien-change` alias."
            )
        return _trusted_sql_response(
            question=question,
            sql_query=_CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_SQL,
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
        return _trusted_sql_response(
            question=question,
            sql_query=_CANONICAL_CURRENT_CUSTOMER_RETENTION_RISK_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            answer=answer,
            metric_value=f"{count_int:,}",
        )

    if _canonical_itm_zip_scope(question):
        try:
            rows = _redact_genie_rows(sql_client.execute(_CANONICAL_ITM_TOP_ZIPS_SQL)) or []
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_itm_zips_failed", exc=exc)
            return None
        zip_trusted_assets = [lead_population_asset]
        if rows:
            top = rows[0]
            answer = (
                "I ranked ZIP codes by unique borrowers passing the refinance-economics screen "
                f"from {lead_population_asset}. "
                f"The current leader is ZIP {top.get('zip')} ({top.get('state')}) "
                f"with {int(top.get('in_the_money_borrowers') or 0):,} borrowers; "
                "the cohort action below carries these ZIP filters into Lead Queue."
            )
        else:
            answer = (
                "The ranked lead population returned no refinance-economics ZIP rows for "
                "the current refreshed, marketing-eligible coverage."
            )
        return _trusted_sql_response(
            question=question,
            sql_query=_CANONICAL_ITM_TOP_ZIPS_SQL,
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
            answer = (
                "I broke down borrowers passing the refinance-economics screen by state from "
                f"{borrower_asset}. "
                f"{top.get('state')} currently leads with "
                f"{int(top.get('in_the_money_borrowers') or 0):,} borrowers."
            )
        else:
            answer = (
                "The trusted borrower table returned no refinance-economics state rows "
                "for the current refreshed data coverage."
            )
        return _trusted_sql_response(
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
                return _trusted_sql_response(
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
        return _trusted_sql_response(
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
        "gold borrower grain, so multi-segment borrowers are counted once."
    )
    return _trusted_sql_response(
        question=question,
        sql_query=sql_query,
        trusted_assets=trusted_assets,
        rows=count_rows,
        answer=answer,
        metric_value=f"{count_int:,}",
    )

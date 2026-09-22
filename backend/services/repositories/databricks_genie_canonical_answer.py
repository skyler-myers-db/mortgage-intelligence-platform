"""Canonical-answer dispatch for grain-sensitive Genie questions.

Single responsibility: decide which reviewed canonical answer a question maps
to, and hand the branch helper the prologue locals it needs. The branch bodies
themselves live in ``databricks_genie_canonical_answer_rankings``, ``_geo``,
and ``_retention``; the adapter that calls this dispatcher lives in
``databricks_genie``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.config.settings import settings
from backend.services.databricks_sql import (
    DatabricksSqlClient,
    DatabricksSqlError,
)
from backend.services.databricks_sql_helpers import qualify
from backend.services.genie_answers import GenieMessageResponse
from backend.services.genie_client import GenieResponse
from backend.services.repositories.databricks_genie_actions import _suggest_genie_actions
from backend.services.repositories.databricks_genie_canonical import (
    _CANONICAL_ITM_COUNT_BY_STATE_SQL,
    _CANONICAL_ITM_COUNT_SQL,
    _canonical_cash_out_state_scope,
    _canonical_heloc_zip_scope,
    _canonical_in_the_money_count_scope,
    _canonical_itm_zip_scope,
    _canonical_listed_purchase_scope,
    _canonical_msa_score_scope,
    _canonical_segment_performance_rescue_scope,
    _canonical_specific_top_borrowers_global_scope,
    _canonical_specific_top_borrowers_state_scope,
    _canonical_top_borrowers_all_segments_scope,
    _canonical_top_borrowers_global_scope,
    _canonical_top_borrowers_state_scope,
    _retention_competitor_lien_list_question,
    _retention_risk_question,
)
from backend.services.repositories.databricks_genie_canonical_answer_geo import (
    _answer_cash_out_top_states,
    _answer_heloc_top_zips,
    _answer_in_the_money_count_by_city,
    _answer_in_the_money_top_zips,
    _answer_listed_purchase_top,
    _answer_msa_scores,
)
from backend.services.repositories.databricks_genie_canonical_answer_rankings import (
    _answer_specific_top_borrowers_by_state,
    _answer_specific_top_borrowers_global,
    _answer_top_borrowers_all_segments,
    _answer_top_borrowers_by_state,
    _answer_top_borrowers_global,
)
from backend.services.repositories.databricks_genie_canonical_answer_retention import (
    _answer_retention_competitor_lien_list,
    _answer_retention_risk,
    _answer_segment_performance_rescue,
)
from backend.services.repositories.databricks_genie_policy_helpers import _emit_genie_warning
from backend.services.repositories.databricks_genie_strategy import _canonical_strategy_board_answer
from backend.services.repositories.databricks_genie_trust import (
    _build_genie_proof,
    _genie_question_hash,
)
from backend.services.repositories.databricks_genie_visualization import _plan_genie_visualization


@dataclass(frozen=True)
class _CanonicalAnswerContext:
    """The prologue locals every extracted canonical branch reads.

    Built once, after the ``sql_client is None`` guard, so each branch helper
    receives exactly the values it used to close over as locals. ``sql_client``
    is non-optional here: the guard above the construction already returned.
    """

    question: str
    result: GenieResponse
    sql_client: DatabricksSqlClient
    borrower_asset: str
    lead_population_asset: str
    evidence_asset: str
    lender_name: str


def _canonical_genie_answer(
    *,
    question: str,
    result: GenieResponse,
    sql_client: DatabricksSqlClient | None,
) -> GenieMessageResponse | None:
    """Return hard-gated trusted answers for known grain-sensitive metrics.

    The Genie space is allowed to read metric views, but some executive
    questions have canonical gold-grain SQL that we use as a governed repair
    when the live Genie turn returns text without SQL proof. Unsafe live SQL
    or PII-bearing answers are blocked before this path so canonical repair
    cannot mask a policy failure.
    """
    if sql_client is None:
        # The ONLY exit in this function that says nothing. Every other one
        # emits a warning, so a rescue that silently declines is invisible in
        # the logs and indistinguishable from "no canonical statement matched"
        # -- which cost two wrong diagnoses of a live refusal (2026-08-12).
        _emit_genie_warning("canonical_genie_no_sql_client")
        return None
    borrower_asset = qualify("gold", "borrower_360")
    lead_population_asset = qualify("gold", "lead_population")
    evidence_asset = qualify("gold", "evidence_events")
    lender_name = (settings.mip_lender_name or "configured lender").strip() or "configured lender"
    ctx = _CanonicalAnswerContext(
        question=question,
        result=result,
        sql_client=sql_client,
        borrower_asset=borrower_asset,
        lead_population_asset=lead_population_asset,
        evidence_asset=evidence_asset,
        lender_name=lender_name,
    )
    strategy_answer = _canonical_strategy_board_answer(
        question=question,
        result=result,
        sql_client=sql_client,
        borrower_asset=borrower_asset,
    )
    if strategy_answer is not None:
        return strategy_answer
    specific_top_borrowers_state_scope = _canonical_specific_top_borrowers_state_scope(question)
    if specific_top_borrowers_state_scope is not None:
        return _answer_specific_top_borrowers_by_state(ctx, specific_top_borrowers_state_scope)
    specific_top_borrowers_global_scope = _canonical_specific_top_borrowers_global_scope(question)
    if specific_top_borrowers_global_scope is not None:
        return _answer_specific_top_borrowers_global(ctx, specific_top_borrowers_global_scope)
    if _canonical_top_borrowers_all_segments_scope(question):
        return _answer_top_borrowers_all_segments(ctx)
    top_borrower_state_scope = _canonical_top_borrowers_state_scope(question)
    if top_borrower_state_scope is not None:
        return _answer_top_borrowers_by_state(ctx, top_borrower_state_scope)
    if _canonical_top_borrowers_global_scope(question):
        return _answer_top_borrowers_global(ctx)
    if _retention_competitor_lien_list_question(question):
        return _answer_retention_competitor_lien_list(ctx)
    if _retention_risk_question(question):
        return _answer_retention_risk(ctx)
    if _canonical_itm_zip_scope(question):
        return _answer_in_the_money_top_zips(ctx)
    if _canonical_heloc_zip_scope(question):
        return _answer_heloc_top_zips(ctx)
    if _canonical_cash_out_state_scope(question):
        return _answer_cash_out_top_states(ctx)
    if _canonical_listed_purchase_scope(question):
        return _answer_listed_purchase_top(ctx)
    if _canonical_msa_score_scope(question):
        return _answer_msa_scores(ctx)

    if _canonical_segment_performance_rescue_scope(question):
        return _answer_segment_performance_rescue(ctx)

    scope = _canonical_in_the_money_count_scope(question)
    if not scope:
        return _answer_in_the_money_count_by_city(ctx)
    state_scope = scope if isinstance(scope, tuple) else None
    sql_query = _CANONICAL_ITM_COUNT_BY_STATE_SQL if state_scope else _CANONICAL_ITM_COUNT_SQL
    params = {"state": state_scope[1]} if state_scope else None
    try:
        row = sql_client.execute_one(sql_query, params) or {}
    except DatabricksSqlError as exc:
        _emit_genie_warning("canonical_genie_metric_failed", exc=exc)
        return None
    count: Any = row.get("in_the_money_borrowers")
    try:
        count_int = int(count)
    except (TypeError, ValueError):
        _emit_genie_warning("canonical_genie_metric_bad_count", value_type=type(count).__name__)
        return None

    rows = [{"in_the_money_borrowers": count_int, "refreshed_at": row.get("refreshed_at")}]
    if state_scope:
        rows[0]["state"] = state_scope[1]
    trusted_assets = [borrower_asset]
    question_hash = _genie_question_hash(question)
    proof = _build_genie_proof(
        sql_query=sql_query,
        trusted_assets=trusted_assets,
        rows=rows,
        question=question,
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        elapsed_ms=result.elapsed_ms,
    )
    visualization = _plan_genie_visualization(question, rows)
    actions = _suggest_genie_actions(
        question=question,
        rows=rows,
        trusted_assets=trusted_assets,
        visualization=visualization,
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        question_hash=question_hash,
        sql_query=sql_query,
        source="trusted_sql",
    )
    geo_text = f" in {state_scope[0]} ({state_scope[1]})" if state_scope else ""
    answer = (
        f"There are {count_int:,} borrowers passing the refinance-economics screen{geo_text} — meaning their current mortgage rate sits far enough above today\'s market, with enough home equity, that refinancing typically pays for itself. "
        f"This is a unique borrower count from {borrower_asset} at the "
        "gold borrower grain, so multi-segment borrowers are counted once."
    )
    return GenieMessageResponse(
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        elapsed_ms=result.elapsed_ms,
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
        metric_value=f"{count_int:,}",
        table_rows=rows,
    )

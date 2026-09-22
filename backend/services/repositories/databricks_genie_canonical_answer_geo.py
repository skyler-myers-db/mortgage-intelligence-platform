"""Canonical geography-scoped answers.

Single responsibility: the reviewed governed-SQL answers keyed by place --
in-the-money ZIP codes and city counts, HELOC ZIP codes, cash-out states,
listed-for-sale purchase opportunities, and metro-area scores. Each helper is
one extracted branch of ``_canonical_genie_answer``; the dispatcher that
selects between them lives in ``databricks_genie_canonical_answer``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.services.databricks_sql import DatabricksSqlError
from backend.services.genie_answers import GenieMessageResponse
from backend.services.repositories.databricks_genie_actions import _suggest_genie_actions
from backend.services.repositories.databricks_genie_canonical import (
    _CANONICAL_CASH_OUT_TOP_STATE_SQL,
    _CANONICAL_HELOC_TOP_ZIPS_SQL,
    _CANONICAL_ITM_COUNT_BY_CITY_SQL,
    _CANONICAL_ITM_TOP_ZIPS_SQL,
    _CANONICAL_LISTED_PURCHASE_TOP_SQL,
    _CANONICAL_MSA_SCORE_SQL,
    _canonical_itm_city_scope,
    _current_footprint_label,
    compose_cohort_ranking_brief,
)
from backend.services.repositories.databricks_genie_policy_helpers import (
    _emit_genie_warning,
    _redact_genie_rows,
)
from backend.services.repositories.databricks_genie_trust import (
    _build_genie_proof,
    _genie_question_hash,
)
from backend.services.repositories.databricks_genie_visualization import _plan_genie_visualization

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    # The dispatcher imports these helpers, so the context type is only
    # imported for type checking; ``from __future__ import annotations`` keeps
    # the annotation lazy at runtime.
    from backend.services.repositories.databricks_genie_canonical_answer import (
        _CanonicalAnswerContext,
    )


def _answer_in_the_money_top_zips(ctx: _CanonicalAnswerContext) -> GenieMessageResponse | None:
    """Top in-the-money ZIP codes."""

    question = ctx.question
    result = ctx.result
    sql_client = ctx.sql_client
    borrower_asset = ctx.borrower_asset

    try:
        rows = sql_client.execute(_CANONICAL_ITM_TOP_ZIPS_SQL)
    except DatabricksSqlError as exc:
        _emit_genie_warning("canonical_genie_itm_zips_failed", exc=exc)
        return None
    rows = _redact_genie_rows(rows) or []
    trusted_assets = [borrower_asset]
    question_hash = _genie_question_hash(question)
    proof = _build_genie_proof(
        sql_query=_CANONICAL_ITM_TOP_ZIPS_SQL,
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
        sql_query=_CANONICAL_ITM_TOP_ZIPS_SQL,
        source="trusted_sql",
    )
    if rows:
        top = rows[0]
        answer = (
            "I ranked ZIP codes by unique borrowers passing the refinance-economics screen "
            f"from {borrower_asset}. "
            f"The current leader is ZIP {top.get('zip')} ({top.get('state')}) "
            f"with {int(top.get('in_the_money_borrowers') or 0):,} borrowers; "
            "the cohort action below carries these ZIP filters into Lead Queue."
        )
    else:
        answer = (
            "The ranked lead population returned no refinance-economics ZIP rows for "
            "the current refreshed, marketing-eligible coverage."
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
        sql_query=_CANONICAL_ITM_TOP_ZIPS_SQL,
        row_count=len(rows),
        proof=proof,
        visualization=visualization,
        actions=actions,
        table_rows=rows,
    )


def _answer_heloc_top_zips(ctx: _CanonicalAnswerContext) -> GenieMessageResponse | None:
    """Top HELOC-candidate ZIP codes."""

    question = ctx.question
    result = ctx.result
    sql_client = ctx.sql_client
    borrower_asset = ctx.borrower_asset

    try:
        rows = sql_client.execute(_CANONICAL_HELOC_TOP_ZIPS_SQL)
    except DatabricksSqlError as exc:
        _emit_genie_warning("canonical_genie_heloc_zips_failed", exc=exc)
        return None
    rows = _redact_genie_rows(rows) or []
    trusted_assets = [borrower_asset]
    question_hash = _genie_question_hash(question)
    proof = _build_genie_proof(
        sql_query=_CANONICAL_HELOC_TOP_ZIPS_SQL,
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
        sql_query=_CANONICAL_HELOC_TOP_ZIPS_SQL,
        source="trusted_sql",
    )
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
    return GenieMessageResponse(
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        elapsed_ms=result.elapsed_ms,
        question_hash=question_hash,
        question=question,
        answer=answer,
        source="trusted_sql",
        trusted_assets=trusted_assets,
        sql_query=_CANONICAL_HELOC_TOP_ZIPS_SQL,
        row_count=len(rows),
        proof=proof,
        visualization=visualization,
        actions=actions,
        table_rows=rows,
    )


def _answer_cash_out_top_states(ctx: _CanonicalAnswerContext) -> GenieMessageResponse | None:
    """Top cash-out states."""

    question = ctx.question
    result = ctx.result
    sql_client = ctx.sql_client
    borrower_asset = ctx.borrower_asset

    try:
        rows = sql_client.execute(_CANONICAL_CASH_OUT_TOP_STATE_SQL)
    except DatabricksSqlError as exc:
        _emit_genie_warning("canonical_genie_cash_out_state_failed", exc=exc)
        return None
    rows = _redact_genie_rows(rows) or []
    trusted_assets = [borrower_asset]
    question_hash = _genie_question_hash(question)
    proof = _build_genie_proof(
        sql_query=_CANONICAL_CASH_OUT_TOP_STATE_SQL,
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
        sql_query=_CANONICAL_CASH_OUT_TOP_STATE_SQL,
        source="trusted_sql",
    )
    if rows:
        top = rows[0]
        count_int = int(top.get("cash_out_borrowers") or 0)
        answer = (
            f"{top.get('state')} has the most cash-out opportunity right now "
            f"with {count_int:,} borrowers. This counts borrowers whose "
            f"primary offer is a cash-out refinance review at the unique "
            f"borrower grain from {borrower_asset}."
        )
        metric_value = f"{count_int:,}"
    else:
        answer = (
            "The trusted borrower table returned no cash-out state rows for "
            "the current refreshed data coverage."
        )
        metric_value = None
    return GenieMessageResponse(
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        elapsed_ms=result.elapsed_ms,
        question_hash=question_hash,
        question=question,
        answer=answer,
        source="trusted_sql",
        trusted_assets=trusted_assets,
        sql_query=_CANONICAL_CASH_OUT_TOP_STATE_SQL,
        row_count=len(rows),
        proof=proof,
        visualization=visualization,
        actions=actions,
        metric_value=metric_value,
        table_rows=rows,
    )


def _answer_listed_purchase_top(ctx: _CanonicalAnswerContext) -> GenieMessageResponse | None:
    """Top listed-for-sale purchase opportunities."""

    question = ctx.question
    result = ctx.result
    sql_client = ctx.sql_client
    borrower_asset = ctx.borrower_asset

    try:
        rows = sql_client.execute(_CANONICAL_LISTED_PURCHASE_TOP_SQL)
    except DatabricksSqlError as exc:
        _emit_genie_warning("canonical_genie_listed_purchase_failed", exc=exc)
        return None
    rows = _redact_genie_rows(rows) or []
    trusted_assets = [borrower_asset]
    question_hash = _genie_question_hash(question)
    proof = _build_genie_proof(
        sql_query=_CANONICAL_LISTED_PURCHASE_TOP_SQL,
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
        sql_query=_CANONICAL_LISTED_PURCHASE_TOP_SQL,
        source="trusted_sql",
    )
    answer = compose_cohort_ranking_brief(
        rows,
        cohort_label="marketing-eligible listed-for-sale borrowers",
        ordering_label="opportunity score among active listing signals",
        source_asset=borrower_asset,
    ) if rows else (
        "The trusted borrower table returned no marketing-eligible listed-for-sale "
        "borrowers for the current refreshed coverage."
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
        sql_query=_CANONICAL_LISTED_PURCHASE_TOP_SQL,
        row_count=len(rows),
        proof=proof,
        visualization=visualization,
        actions=actions,
        table_rows=rows,
    )


def _answer_msa_scores(ctx: _CanonicalAnswerContext) -> GenieMessageResponse | None:
    """Opportunity scores by metro area."""

    question = ctx.question
    result = ctx.result
    sql_client = ctx.sql_client
    borrower_asset = ctx.borrower_asset

    try:
        rows = sql_client.execute(_CANONICAL_MSA_SCORE_SQL)
    except DatabricksSqlError as exc:
        _emit_genie_warning("canonical_genie_msa_score_failed", exc=exc)
        return None
    rows = _redact_genie_rows(rows) or []
    trusted_assets = [borrower_asset]
    question_hash = _genie_question_hash(question)
    proof = _build_genie_proof(
        sql_query=_CANONICAL_MSA_SCORE_SQL,
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
        sql_query=_CANONICAL_MSA_SCORE_SQL,
        source="trusted_sql",
    )
    if rows:
        answer = (
            "I used Cotality's `situs_cbsa_code` as the MSA identifier and "
            "ranked the top five markets by borrower volume, then calculated "
            f"mean lead score at the unique borrower grain from {borrower_asset}."
        )
    else:
        answer = (
            "The current gold borrower table did not return CBSA-coded market rows. "
            "Module 0 has `situs_cbsa_code` for MSA-style grouping, but no separate "
            "MSA-name lookup is loaded."
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
        sql_query=_CANONICAL_MSA_SCORE_SQL,
        row_count=len(rows),
        proof=proof,
        visualization=visualization,
        actions=actions,
        table_rows=rows,
    )


def _answer_in_the_money_count_by_city(ctx: _CanonicalAnswerContext) -> GenieMessageResponse | None:
    """In-the-money counts for a city scope."""

    question = ctx.question
    result = ctx.result
    sql_client = ctx.sql_client
    borrower_asset = ctx.borrower_asset

    city_scope = _canonical_itm_city_scope(question)
    if not city_scope:
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
        _emit_genie_warning("canonical_genie_metric_failed", exc=exc)
        return None
    count: Any = row.get("in_the_money_borrowers")
    try:
        count_int = int(count)
    except (TypeError, ValueError):
        _emit_genie_warning("canonical_genie_metric_bad_count", value_type=type(count).__name__)
        return None
    rows = [
        {
            "city": city_scope,
            "in_the_money_borrowers": count_int,
            "refreshed_at": row.get("refreshed_at"),
        }
    ]
    trusted_assets = [borrower_asset]
    question_hash = _genie_question_hash(question)
    proof = _build_genie_proof(
        sql_query=_CANONICAL_ITM_COUNT_BY_CITY_SQL,
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
        sql_query=_CANONICAL_ITM_COUNT_BY_CITY_SQL,
        source="trusted_sql",
    )
    answer = (
        f"There are {count_int:,} borrowers passing the refinance-economics screen in {city_scope} — meaning their current mortgage rate sits far enough above today\'s market, with enough home equity, that refinancing typically pays for itself — "
        f"within the current {_current_footprint_label()} evaluation-share scope. "
        f"This is a city-scoped unique borrower count from {borrower_asset}; "
        "it is not the overall share total."
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
        sql_query=_CANONICAL_ITM_COUNT_BY_CITY_SQL,
        row_count=len(rows),
        proof=proof,
        visualization=visualization,
        actions=actions,
        metric_value=f"{count_int:,}",
        table_rows=rows,
    )

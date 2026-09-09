"""Canonical ranked-borrower answers.

Single responsibility: the reviewed governed-SQL answers for "who are the top
borrowers" questions -- named-intent rankings inside a state or across the
footprint, the all-segments ranking, and the plain state / global rankings.
Each helper is one extracted branch of ``_canonical_genie_answer``; the
dispatcher that selects between them lives in
``databricks_genie_canonical_answer``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from backend.services.databricks_sql import DatabricksSqlError
from backend.services.genie_answers import GenieMessageResponse
from backend.services.repositories.databricks_genie_actions import _suggest_genie_actions
from backend.services.repositories.databricks_genie_canonical import (
    _CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_BY_STATE_SQL,
    _CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_GLOBAL_SQL,
    _CANONICAL_TOP_BORROWERS_ALL_SEGMENTS_SQL,
    _CANONICAL_TOP_BORROWERS_BY_STATE_INTENT_SQL,
    _CANONICAL_TOP_BORROWERS_BY_STATE_SQL,
    _CANONICAL_TOP_BORROWERS_GLOBAL_INTENT_SQL,
    _CANONICAL_TOP_BORROWERS_GLOBAL_SQL,
    _retention_eligibility_fallback_from_summary,
    _specific_top_borrower_intent_label,
    _specific_top_borrower_intent_note,
    _specific_top_borrower_sort_label,
    compose_all_segments_brief,
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


def _answer_specific_top_borrowers_by_state(
    ctx: _CanonicalAnswerContext,
    specific_top_borrowers_state_scope: tuple[str, str, str],
) -> GenieMessageResponse | None:
    """Top borrowers for a named intent inside one state."""

    question = ctx.question
    result = ctx.result
    sql_client = ctx.sql_client
    borrower_asset = ctx.borrower_asset

    intent, state_name, state_code = specific_top_borrowers_state_scope
    sql_query = _CANONICAL_TOP_BORROWERS_BY_STATE_INTENT_SQL[intent]
    intent_label = _specific_top_borrower_intent_label(intent)
    sort_label = _specific_top_borrower_sort_label(intent)
    try:
        rows = sql_client.execute(sql_query, {"state": state_code})
    except DatabricksSqlError as exc:
        _emit_genie_warning(
            "canonical_genie_specific_top_borrowers_state_failed",
            intent=intent,
            exc=exc,
        )
        return None
    rows = _redact_genie_rows(rows) or []
    response_sql_query = sql_query
    response_rows = rows
    suppress_actions = False
    metric_value = None
    retention_fallback = None
    if not rows and intent == "retention":
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
                "canonical_genie_retention_eligibility_summary_state_failed",
                exc=exc,
            )
            summary_rows = []
        retention_fallback = _retention_eligibility_fallback_from_summary(
            summary_rows,
            state_name=state_name,
            state_code=state_code,
        )
        if retention_fallback is not None:
            response_sql_query = retention_fallback.sql_query
            response_rows = retention_fallback.rows
            suppress_actions = retention_fallback.suppress_actions
            metric_value = retention_fallback.metric_value
    trusted_assets = [borrower_asset]
    question_hash = _genie_question_hash(question)
    proof = _build_genie_proof(
        sql_query=response_sql_query,
        trusted_assets=trusted_assets,
        rows=response_rows,
        question=question,
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        elapsed_ms=result.elapsed_ms,
    )
    visualization = _plan_genie_visualization(question, response_rows)
    actions = [] if suppress_actions else _suggest_genie_actions(
        question=question,
        rows=response_rows,
        trusted_assets=trusted_assets,
        visualization=visualization,
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        question_hash=question_hash,
        sql_query=response_sql_query,
        source="trusted_sql",
    )
    if rows:
        intent_note = _specific_top_borrower_intent_note(question, intent)
        answer = compose_cohort_ranking_brief(
            rows,
            cohort_label=f"{state_name} ({state_code}) {intent_label} borrowers",
            ordering_label=sort_label,
            source_asset=borrower_asset,
            scope_note=intent_note.strip(),
        )
    elif retention_fallback is not None:
        answer = retention_fallback.answer
    else:
        answer = (
            f"The trusted borrower table returned no marketing-eligible "
            f"{intent_label} borrowers in {state_name} ({state_code}) for "
            "the current refreshed coverage."
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
        sql_query=response_sql_query,
        row_count=len(response_rows),
        proof=proof,
        visualization=visualization,
        actions=actions,
        metric_value=metric_value,
        table_rows=response_rows,
    )


def _answer_specific_top_borrowers_global(
    ctx: _CanonicalAnswerContext,
    specific_top_borrowers_global_scope: str,
) -> GenieMessageResponse | None:
    """Top borrowers for a named intent across the footprint."""

    question = ctx.question
    result = ctx.result
    sql_client = ctx.sql_client
    borrower_asset = ctx.borrower_asset

    intent = specific_top_borrowers_global_scope
    sql_query = _CANONICAL_TOP_BORROWERS_GLOBAL_INTENT_SQL[intent]
    intent_label = _specific_top_borrower_intent_label(intent)
    sort_label = _specific_top_borrower_sort_label(intent)
    try:
        rows = sql_client.execute(sql_query)
    except DatabricksSqlError as exc:
        _emit_genie_warning(
            "canonical_genie_specific_top_borrowers_global_failed",
            intent=intent,
            exc=exc,
        )
        return None
    rows = _redact_genie_rows(rows) or []
    response_sql_query = sql_query
    response_rows = rows
    suppress_actions = False
    metric_value = None
    retention_fallback = None
    if not rows and intent == "retention":
        try:
            summary_rows = (
                _redact_genie_rows(
                    sql_client.execute(_CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_GLOBAL_SQL)
                )
                or []
            )
        except DatabricksSqlError as exc:
            _emit_genie_warning(
                "canonical_genie_retention_eligibility_summary_global_failed",
                exc=exc,
            )
            summary_rows = []
        retention_fallback = _retention_eligibility_fallback_from_summary(summary_rows)
        if retention_fallback is not None:
            response_sql_query = retention_fallback.sql_query
            response_rows = retention_fallback.rows
            suppress_actions = retention_fallback.suppress_actions
            metric_value = retention_fallback.metric_value
    trusted_assets = [borrower_asset]
    question_hash = _genie_question_hash(question)
    proof = _build_genie_proof(
        sql_query=response_sql_query,
        trusted_assets=trusted_assets,
        rows=response_rows,
        question=question,
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        elapsed_ms=result.elapsed_ms,
    )
    visualization = _plan_genie_visualization(question, response_rows)
    actions = [] if suppress_actions else _suggest_genie_actions(
        question=question,
        rows=response_rows,
        trusted_assets=trusted_assets,
        visualization=visualization,
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        question_hash=question_hash,
        sql_query=response_sql_query,
        source="trusted_sql",
    )
    if rows:
        intent_note = _specific_top_borrower_intent_note(question, intent)
        answer = compose_cohort_ranking_brief(
            rows,
            cohort_label=f"{intent_label} borrowers across the current refreshed coverage",
            ordering_label=sort_label,
            source_asset=borrower_asset,
            scope_note=intent_note.strip(),
        )
    elif retention_fallback is not None:
        answer = retention_fallback.answer
    else:
        answer = (
            f"The trusted borrower table returned no marketing-eligible "
            f"{intent_label} borrowers for the current refreshed coverage."
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
        sql_query=response_sql_query,
        row_count=len(response_rows),
        proof=proof,
        visualization=visualization,
        actions=actions,
        metric_value=metric_value,
        table_rows=response_rows,
    )


def _answer_top_borrowers_all_segments(ctx: _CanonicalAnswerContext) -> GenieMessageResponse | None:
    """Top borrowers ranked across every segment."""

    question = ctx.question
    result = ctx.result
    sql_client = ctx.sql_client
    borrower_asset = ctx.borrower_asset

    try:
        rows = sql_client.execute(_CANONICAL_TOP_BORROWERS_ALL_SEGMENTS_SQL)
    except DatabricksSqlError as exc:
        _emit_genie_warning("canonical_genie_top_borrowers_all_segments_failed", exc=exc)
        return None
    rows = _redact_genie_rows(rows) or []
    trusted_assets = [borrower_asset]
    question_hash = _genie_question_hash(question)
    proof = _build_genie_proof(
        sql_query=_CANONICAL_TOP_BORROWERS_ALL_SEGMENTS_SQL,
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
        sql_query=_CANONICAL_TOP_BORROWERS_ALL_SEGMENTS_SQL,
        source="trusted_sql",
    )
    return GenieMessageResponse(
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        elapsed_ms=result.elapsed_ms,
        question_hash=question_hash,
        question=question,
        answer=compose_all_segments_brief(rows, borrower_asset),
        source="trusted_sql",
        trusted_assets=trusted_assets,
        sql_query=_CANONICAL_TOP_BORROWERS_ALL_SEGMENTS_SQL,
        row_count=len(rows),
        proof=proof,
        visualization=visualization,
        actions=actions,
        table_rows=rows,
    )


def _answer_top_borrowers_by_state(
    ctx: _CanonicalAnswerContext,
    top_borrower_state_scope: tuple[str, str],
) -> GenieMessageResponse | None:
    """Top borrowers inside one state."""

    question = ctx.question
    result = ctx.result
    sql_client = ctx.sql_client
    borrower_asset = ctx.borrower_asset
    lead_population_asset = ctx.lead_population_asset

    state_name, state_code = top_borrower_state_scope
    try:
        rows = sql_client.execute(
            _CANONICAL_TOP_BORROWERS_BY_STATE_SQL,
            {"state": state_code},
        )
    except DatabricksSqlError as exc:
        _emit_genie_warning("canonical_genie_top_borrowers_state_failed", exc=exc)
        return None
    rows = _redact_genie_rows(rows) or []
    trusted_assets = [lead_population_asset, borrower_asset]
    question_hash = _genie_question_hash(question)
    proof = _build_genie_proof(
        sql_query=_CANONICAL_TOP_BORROWERS_BY_STATE_SQL,
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
        sql_query=_CANONICAL_TOP_BORROWERS_BY_STATE_SQL,
        source="trusted_sql",
    )
    answer = compose_cohort_ranking_brief(
        rows,
        cohort_label=f"{state_name} ({state_code}) borrowers in the ranked Lead Queue",
        ordering_label="lead score",
        source_asset=lead_population_asset,
    ) if rows else (
        f"The trusted lead population returned no {state_name} ({state_code}) "
        "borrowers for the current refreshed data coverage."
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
        sql_query=_CANONICAL_TOP_BORROWERS_BY_STATE_SQL,
        row_count=len(rows),
        proof=proof,
        visualization=visualization,
        actions=actions,
        table_rows=rows,
    )


def _answer_top_borrowers_global(ctx: _CanonicalAnswerContext) -> GenieMessageResponse | None:
    """Top borrowers across the footprint."""

    question = ctx.question
    result = ctx.result
    sql_client = ctx.sql_client
    borrower_asset = ctx.borrower_asset
    lead_population_asset = ctx.lead_population_asset

    try:
        rows = sql_client.execute(_CANONICAL_TOP_BORROWERS_GLOBAL_SQL)
    except DatabricksSqlError as exc:
        _emit_genie_warning("canonical_genie_top_borrowers_global_failed", exc=exc)
        return None
    rows = _redact_genie_rows(rows) or []
    trusted_assets = [lead_population_asset, borrower_asset]
    question_hash = _genie_question_hash(question)
    proof = _build_genie_proof(
        sql_query=_CANONICAL_TOP_BORROWERS_GLOBAL_SQL,
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
        sql_query=_CANONICAL_TOP_BORROWERS_GLOBAL_SQL,
        source="trusted_sql",
    )
    answer = compose_cohort_ranking_brief(
        rows,
        cohort_label="marketing-eligible borrowers in the ranked Lead Queue",
        ordering_label="lead score",
        source_asset=lead_population_asset,
    ) if rows else (
        "The ranked lead population returned no marketing-eligible borrower "
        "rows for the current refreshed coverage."
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
        sql_query=_CANONICAL_TOP_BORROWERS_GLOBAL_SQL,
        row_count=len(rows),
        proof=proof,
        visualization=visualization,
        actions=actions,
        table_rows=rows,
    )

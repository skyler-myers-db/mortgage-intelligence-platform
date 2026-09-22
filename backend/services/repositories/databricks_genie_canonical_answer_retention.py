"""Canonical retention answers.

Single responsibility: the reviewed governed-SQL answers for the retention
book -- competitor-lien lists, current-customer retention risk, and the
segment approval-rate rescue. Each helper is one extracted branch of
``_canonical_genie_answer``; the dispatcher that selects between them lives in
``databricks_genie_canonical_answer``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.services.databricks_sql import DatabricksSqlError
from backend.services.databricks_sql_helpers import qualify
from backend.services.genie_answers import GenieMessageResponse
from backend.services.repositories.databricks_genie_actions import (
    _suggest_genie_actions,
    _total_matching_from_rows,
)
from backend.services.repositories.databricks_genie_canonical import (
    _CANONICAL_CURRENT_CUSTOMER_RETENTION_RISK_SQL,
    _CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_BY_STATE_SQL,
    _CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_SQL,
    _CANONICAL_SEGMENT_APPROVAL_RATE_SQL,
    _canonical_itm_state_scope,
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


def _answer_retention_competitor_lien_list(
    ctx: _CanonicalAnswerContext,
) -> GenieMessageResponse | None:
    """Borrowers with a competitor lien, listed for retention outreach."""

    question = ctx.question
    result = ctx.result
    sql_client = ctx.sql_client
    borrower_asset = ctx.borrower_asset
    evidence_asset = ctx.evidence_asset

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
        _emit_genie_warning("canonical_genie_retention_competitor_lien_failed", exc=exc)
        return None
    trusted_assets = [
        borrower_asset,
        evidence_asset,
    ]
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
    total_matching = _total_matching_from_rows(rows)
    shown_count = len(rows)
    if rows:
        if total_matching > shown_count:
            answer = (
                f"There are {total_matching:,} retention-list borrowers{scope_phrase} with "
                f"competitor-lien evidence in the last 30 days; showing the first "
                f"{shown_count:,} by latest evidence timestamp and opportunity score. "
                "The result uses the governed `competitor_lien` signal_type from "
                f"{evidence_asset}."
            )
        else:
            answer = (
                f"I found {shown_count:,} retention-list borrowers{scope_phrase} with competitor-lien "
                "evidence in the last 30 days. The result uses the governed "
                f"`competitor_lien` signal_type from {evidence_asset}."
            )
    else:
        answer = (
            f"No retention-list borrowers{scope_phrase} have governed competitor-lien evidence "
            "in the last 30 days. This is a live result from the modeled "
            "`competitor_lien` signal_type, not a stale `lien-change` alias."
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
        metric_value=f"{total_matching:,}",
        table_rows=rows,
    )


def _answer_retention_risk(ctx: _CanonicalAnswerContext) -> GenieMessageResponse | None:
    """Current customers at retention risk."""

    question = ctx.question
    result = ctx.result
    sql_client = ctx.sql_client
    borrower_asset = ctx.borrower_asset
    lender_name = ctx.lender_name

    try:
        row = sql_client.execute_one(_CANONICAL_CURRENT_CUSTOMER_RETENTION_RISK_SQL) or {}
    except DatabricksSqlError as exc:
        _emit_genie_warning("canonical_genie_retention_risk_failed", exc=exc)
        return None
    count: Any = row.get("retention_risk_borrowers")
    try:
        count_int = int(count)
    except (TypeError, ValueError):
        _emit_genie_warning("canonical_genie_retention_risk_bad_count", value_type=type(count).__name__)
        return None
    rows = [
        {
            "retention_risk_borrowers": count_int,
            "refreshed_at": row.get("refreshed_at"),
        }
    ]
    trusted_assets = [borrower_asset]
    question_hash = _genie_question_hash(question)
    proof = _build_genie_proof(
        sql_query=_CANONICAL_CURRENT_CUSTOMER_RETENTION_RISK_SQL,
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
        sql_query=_CANONICAL_CURRENT_CUSTOMER_RETENTION_RISK_SQL,
        source="trusted_sql",
    )
    answer = (
        f"There are {count_int:,} current {lender_name} customers in the retention-risk "
        f"cohort. This uses the modeled retention signal in {borrower_asset} "
        "rather than the mutually exclusive current-customer and competitor-lien "
        "flags."
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
        sql_query=_CANONICAL_CURRENT_CUSTOMER_RETENTION_RISK_SQL,
        row_count=len(rows),
        proof=proof,
        visualization=visualization,
        actions=actions,
        metric_value=f"{count_int:,}",
        table_rows=rows,
    )


def _answer_segment_performance_rescue(ctx: _CanonicalAnswerContext) -> GenieMessageResponse | None:
    """Segment approval-rate rescue when the live turn had no SQL proof."""

    question = ctx.question
    result = ctx.result
    sql_client = ctx.sql_client

    # RESCUE ONLY. Genie has already failed to return trusted SQL for this
    # turn, so the choice here is this statement or a refusal -- not this
    # statement or a live answer. See
    # ``_canonical_segment_performance_rescue_scope``.
    try:
        rows = sql_client.execute(_CANONICAL_SEGMENT_APPROVAL_RATE_SQL)
    except DatabricksSqlError as exc:
        _emit_genie_warning("canonical_genie_segment_performance_failed", exc=exc)
        return None
    rows = _redact_genie_rows(rows) or []
    segment_asset = qualify("semantics", "segment_performance_metric_view")
    trusted_assets = [segment_asset]
    question_hash = _genie_question_hash(question)
    proof = _build_genie_proof(
        sql_query=_CANONICAL_SEGMENT_APPROVAL_RATE_SQL,
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
        sql_query=_CANONICAL_SEGMENT_APPROVAL_RATE_SQL,
        source="trusted_sql",
    )
    if rows:
        answer = (
            "I compared every segment side by side from "
            f"{segment_asset}: approval rate, outreach rate, borrower count and "
            "average opportunity score, ordered by approval rate. Approval and "
            "outreach rates are governed campaign outcomes, so a segment with no "
            "completed campaigns shows a rate of zero rather than a missing row."
        )
    else:
        answer = (
            f"The governed segment performance view returned no rows from {segment_asset} "
            "for the current refreshed coverage, so there is no segment comparison to show."
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
        sql_query=_CANONICAL_SEGMENT_APPROVAL_RATE_SQL,
        row_count=len(rows),
        table_rows=rows,
        proof=proof,
        visualization=visualization,
        actions=actions,
    )

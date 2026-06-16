"""Canonical strategy-board response helpers for Databricks Genie."""
from __future__ import annotations

from backend.services.databricks_sql import DatabricksSqlClient, DatabricksSqlError
from backend.services.genie_answers import GenieMessageResponse
from backend.services.genie_client import GenieResponse
from backend.services.repositories.databricks_genie_actions import _suggest_genie_actions
from backend.services.repositories.databricks_genie_canonical import (
    _CANONICAL_STRATEGY_BOARD_SQL,
    _canonical_strategy_board_scope,
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


def _canonical_strategy_board_answer(
    *,
    question: str,
    result: GenieResponse,
    sql_client: DatabricksSqlClient,
    borrower_asset: str,
) -> GenieMessageResponse | None:
    if not _canonical_strategy_board_scope(question):
        return None
    try:
        rows = sql_client.execute(_CANONICAL_STRATEGY_BOARD_SQL)
    except DatabricksSqlError as exc:
        _emit_genie_warning("canonical_genie_strategy_board_failed", exc=exc)
        return None
    rows = _redact_genie_rows(rows) or []
    trusted_assets = [borrower_asset]
    question_hash = _genie_question_hash(question)
    proof = _build_genie_proof(
        sql_query=_CANONICAL_STRATEGY_BOARD_SQL,
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
        sql_query=_CANONICAL_STRATEGY_BOARD_SQL,
        source="trusted_sql",
    )
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
    return GenieMessageResponse(
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        elapsed_ms=result.elapsed_ms,
        question_hash=question_hash,
        question=question,
        answer=answer,
        source="trusted_sql",
        trusted_assets=trusted_assets,
        sql_query=_CANONICAL_STRATEGY_BOARD_SQL,
        row_count=len(rows),
        proof=proof,
        visualization=visualization,
        actions=actions,
        table_rows=rows,
    )

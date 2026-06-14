"""Direct trusted-SQL answers for narrow canonical Genie questions."""
from __future__ import annotations

from typing import Any

from backend.services.databricks_sql import DatabricksSqlClient, DatabricksSqlError
from backend.services.databricks_sql_helpers import qualify
from backend.services.genie_answers import GenieMessageResponse
from backend.services.repositories.databricks_genie_actions import _suggest_genie_actions
from backend.services.repositories.databricks_genie_canonical import (
    _CANONICAL_ITM_BY_STATE_SQL,
    _CANONICAL_ITM_COUNT_BY_STATE_SQL,
    _CANONICAL_ITM_COUNT_SQL,
    _CANONICAL_ITM_TOP_ZIPS_SQL,
    _canonical_in_the_money_count_scope,
    _canonical_itm_state_breakdown_scope,
    _canonical_itm_zip_scope,
)
from backend.services.repositories.databricks_genie_policy_helpers import (
    _emit_genie_warning,
)
from backend.services.repositories.databricks_genie_trust import (
    _build_genie_proof,
    _genie_question_hash,
)
from backend.services.repositories.databricks_genie_visualization import (
    _plan_genie_visualization,
)


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


def direct_canonical_response(
    question: str,
    sql_client: DatabricksSqlClient | None,
) -> GenieMessageResponse | None:
    """Return live trusted-SQL proof for narrow gold-grain count prompts."""
    if sql_client is None:
        return None
    borrower_asset = qualify("gold", "borrower_360")
    trusted_assets = [borrower_asset]

    if _canonical_itm_zip_scope(question):
        try:
            rows = sql_client.execute(_CANONICAL_ITM_TOP_ZIPS_SQL) or []
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_itm_zips_failed", exc=exc)
            return None
        if rows:
            top = rows[0]
            answer = (
                "I ranked ZIP codes by unique borrowers currently in-the-money "
                f"for refinance from {borrower_asset}. "
                f"The current leader is ZIP {top.get('zip')} ({top.get('state')}) "
                f"with {int(top.get('in_the_money_borrowers') or 0):,} borrowers; "
                "the cohort action below carries these ZIP filters into Lead Queue."
            )
        else:
            answer = (
                "The trusted borrower table returned no in-the-money ZIP rows for "
                "the current refreshed data coverage."
            )
        return _trusted_sql_response(
            question=question,
            sql_query=_CANONICAL_ITM_TOP_ZIPS_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            answer=answer,
        )

    if _canonical_itm_state_breakdown_scope(question):
        try:
            rows = sql_client.execute(_CANONICAL_ITM_BY_STATE_SQL) or []
        except DatabricksSqlError as exc:
            _emit_genie_warning("direct_canonical_genie_itm_state_breakdown_failed", exc=exc)
            return None
        if rows:
            top = rows[0]
            answer = (
                "I broke down in-the-money refinance candidates by state from "
                f"{borrower_asset}. "
                f"{top.get('state')} currently leads with "
                f"{int(top.get('in_the_money_borrowers') or 0):,} borrowers."
            )
        else:
            answer = (
                "The trusted borrower table returned no in-the-money state rows "
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
        return None

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
        f"There are {count_int:,} borrowers currently in-the-money{geo_text}. "
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

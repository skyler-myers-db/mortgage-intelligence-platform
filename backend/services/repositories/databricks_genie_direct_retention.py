"""Direct-answer branches for competitor-lien evidence and retention risk."""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.services.databricks_sql import (
    DatabricksSqlError,
)
from backend.services.genie_answers import GenieMessageResponse
from backend.services.repositories.databricks_genie_actions import (
    _total_matching_from_rows,
)
from backend.services.repositories.databricks_genie_canonical import (
    _CANONICAL_CURRENT_CUSTOMER_RETENTION_RISK_SQL,
    _CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_BY_STATE_SQL,
    _CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_SQL,
    _canonical_itm_state_scope,
)
from backend.services.repositories.databricks_genie_policy_helpers import (
    _emit_genie_warning,
    _redact_genie_rows,
)

if TYPE_CHECKING:
    from backend.services.repositories.databricks_genie_direct import _DirectContext


def _direct_retention_competitor_lien_list(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Borrowers with competitor-lien evidence."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
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
        # `array_contains(b.segment_codes,'retention')` is a real
        # conjunct, but it sits in a subquery this statement selects from
        # rather than in the outermost statement's own filter position, so
        # the reader sees no filters at all. Every returned row satisfies
        # it and `borrower_ids` already pins the cohort, so declaring
        # `retention` restates the answer instead of narrowing it.
        cohort_segments=("retention",),
    )


def _direct_retention_risk(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Current customers at retention risk."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
    trusted_assets = ctx.trusted_assets
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

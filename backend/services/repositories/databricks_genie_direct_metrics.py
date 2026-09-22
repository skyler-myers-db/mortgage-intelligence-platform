"""Direct-answer branches for scores, evidence volume, and approval trends."""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.services.databricks_sql import (
    DatabricksSqlError,
)
from backend.services.genie_answers import GenieMessageResponse
from backend.services.repositories.databricks_genie_canonical import (
    _CANONICAL_APPROVAL_TREND_30D_SQL,
    _CANONICAL_EVIDENCE_EVENTS_THIS_QUARTER_SQL,
    _CANONICAL_EVIDENCE_EVENTS_YESTERDAY_SQL,
    _CANONICAL_LEAD_SCORE_WEEKLY_DISTRIBUTION_SQL,
    _CANONICAL_MEAN_LEAD_SCORE_BY_STATE_SQL,
)
from backend.services.repositories.databricks_genie_policy_helpers import (
    _emit_genie_warning,
    _redact_genie_rows,
)

if TYPE_CHECKING:
    from backend.services.repositories.databricks_genie_direct import _DirectContext


def _direct_mean_lead_score_by_state(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Mean lead score by state."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
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


def _direct_evidence_events_yesterday(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Evidence events recorded yesterday."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    evidence_asset = ctx.evidence_asset
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


def _direct_lead_score_weekly_distribution(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Weekly lead-score distribution."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    funnel_asset = ctx.funnel_asset
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


def _direct_approval_trend_30d(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Thirty-day approval trend."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    funnel_asset = ctx.funnel_asset
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


def _direct_evidence_events_quarter(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Evidence events recorded this quarter."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    evidence_asset = ctx.evidence_asset
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

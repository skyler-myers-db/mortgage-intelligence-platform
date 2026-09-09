"""Direct-answer branches for ranked borrower, ZIP, state, and strategy boards."""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.services.databricks_sql import (
    DatabricksSqlError,
)
from backend.services.genie_answers import GenieMessageResponse
from backend.services.repositories.databricks_genie_canonical import (
    _CANONICAL_CASH_OUT_TOP_STATE_SQL,
    _CANONICAL_HELOC_TOP_ZIPS_SQL,
    _CANONICAL_INVESTOR_TOP_BY_RELATED_PROPERTY_SQL,
    _CANONICAL_LISTED_PURCHASE_TOP_SQL,
    _CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_BY_STATE_SQL,
    _CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_GLOBAL_SQL,
    _CANONICAL_STRATEGY_BOARD_SQL,
    _CANONICAL_TOP_BORROWERS_ALL_SEGMENTS_SQL,
    _CANONICAL_TOP_BORROWERS_BY_STATE_INTENT_SQL,
    _CANONICAL_TOP_BORROWERS_BY_STATE_SQL,
    _CANONICAL_TOP_BORROWERS_GLOBAL_INTENT_SQL,
    _CANONICAL_TOP_BORROWERS_GLOBAL_SQL,
    _CANONICAL_TOP_CASH_OUT_BY_EQUITY_SQL,
    _retention_eligibility_fallback_from_summary,
    _specific_top_borrower_intent_label,
    _specific_top_borrower_intent_note,
    _specific_top_borrower_sort_label,
    compose_all_segments_brief,
    compose_cohort_ranking_brief,
)
from backend.services.repositories.databricks_genie_direct_responses import (
    _segment_display_label,
)
from backend.services.repositories.databricks_genie_policy_helpers import (
    _emit_genie_warning,
    _redact_genie_rows,
)
from backend.services.scoring import (
    offer_display_label,
)

if TYPE_CHECKING:
    from backend.services.repositories.databricks_genie_direct import _DirectContext


def _direct_specific_top_borrowers_state(
    ctx: _DirectContext,
    specific_top_borrowers_state_scope: tuple[str, str, str],
) -> GenieMessageResponse | None:
    """Top borrowers for one explicit intent inside one state."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
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
        intent_note = _specific_top_borrower_intent_note(question, intent)
        answer = compose_cohort_ranking_brief(
            rows,
            cohort_label=f"{state_name} ({state_code}) {intent_label} borrowers",
            ordering_label=sort_label,
            source_asset=borrower_asset,
            scope_note=intent_note.strip(),
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


def _direct_specific_top_borrowers_global(
    ctx: _DirectContext,
    specific_top_borrowers_global_scope: str,
) -> GenieMessageResponse | None:
    """Top borrowers for one explicit intent across the whole coverage."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
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
        intent_note = _specific_top_borrower_intent_note(question, intent)
        answer = compose_cohort_ranking_brief(
            rows,
            cohort_label=f"{intent_label} borrowers across the current refreshed coverage",
            ordering_label=sort_label,
            source_asset=borrower_asset,
            scope_note=intent_note.strip(),
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


def _direct_top_borrowers_state(
    ctx: _DirectContext,
    top_borrower_state_scope: tuple[str, str],
) -> GenieMessageResponse | None:
    """Top Lead Queue borrowers inside one state."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    lead_population_asset = ctx.lead_population_asset
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
    answer = compose_cohort_ranking_brief(
        rows,
        cohort_label=f"{state_name} ({state_code}) borrowers in the ranked Lead Queue",
        ordering_label="lead score",
        source_asset=lead_population_asset,
    ) if rows else (
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


def _direct_top_borrowers_all_segments(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Top borrowers across all segments with drivers."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
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


def _direct_top_borrowers_global(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Top Lead Queue borrowers across the whole coverage."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    lead_population_asset = ctx.lead_population_asset
    try:
        rows = (
            _redact_genie_rows(sql_client.execute(_CANONICAL_TOP_BORROWERS_GLOBAL_SQL))
            or []
        )
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_top_borrowers_global_failed", exc=exc)
        return None
    answer = compose_cohort_ranking_brief(
        rows,
        cohort_label="marketing-eligible borrowers in the ranked Lead Queue",
        ordering_label="lead score",
        source_asset=lead_population_asset,
    ) if rows else (
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


def _direct_heloc_zip(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Top HELOC-intent ZIP codes."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
    trusted_assets = ctx.trusted_assets
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


def _direct_strategy_board(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Segment-by-state strategy board ranking."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
    trusted_assets = ctx.trusted_assets
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


def _direct_cash_out_state(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Top cash-out state."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
    trusted_assets = ctx.trusted_assets
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


def _direct_top_cash_out_by_equity(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Top cash-out borrowers ranked by equity."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
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


def _direct_listed_purchase(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Top listed-for-sale purchase candidates."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
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
        answer = compose_cohort_ranking_brief(
            rows,
            cohort_label="marketing-eligible listed-for-sale borrowers",
            ordering_label="opportunity score among active listing signals",
            source_asset=borrower_asset,
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


def _direct_investor_top_by_related_property(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Top investor borrowers ranked by related-property count."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
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

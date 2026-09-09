"""Direct-answer branches for population counts, shares, and distributions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.services.databricks_sql import (
    DatabricksSqlError,
)
from backend.services.genie_answers import GenieMessageResponse
from backend.services.repositories.databricks_genie_canonical import (
    _CANONICAL_ADDRESSABLE_MARKET_SQL,
    _CANONICAL_EQUITY_THRESHOLD_COUNT_SQL,
    _CANONICAL_EQUITY_THRESHOLD_STRICT_COUNT_SQL,
    _CANONICAL_HELOC_COUNT_SQL,
    _CANONICAL_HOME_EQUITY_DISTRIBUTION_SQL,
    _CANONICAL_INVESTOR_COUNT_SQL,
    _CANONICAL_ITM_COUNT_AVG_SPREAD_SQL,
    _CANONICAL_ITM_SHARE_SQL,
    _CANONICAL_LISTED_COUNT_BY_STATE_SQL,
    _CANONICAL_LISTED_COUNT_SQL,
    _CANONICAL_NEGATIVE_EQUITY_COUNT_SQL,
    _CANONICAL_RANKED_LEAD_POPULATION_SQL,
    _format_pct_threshold,
)
from backend.services.repositories.databricks_genie_canonical_scopes import (
    CanonicalEquityThresholdScope,
    CanonicalListedCountScope,
    CanonicalNegativeEquityScope,
)
from backend.services.repositories.databricks_genie_policy_helpers import (
    _emit_genie_warning,
    _redact_genie_rows,
)

if TYPE_CHECKING:
    from backend.services.repositories.databricks_genie_direct import _DirectContext


def _direct_itm_count_avg_spread(ctx: _DirectContext) -> GenieMessageResponse | None:
    """In-the-money borrower count with the average rate spread."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
    trusted_assets = ctx.trusted_assets
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
        f"This is calculated at the unique borrower grain from {borrower_asset}; "
        "it is broader than the marketing-eligible Lead Queue or any eligible-only "
        "Segment page filter."
    )
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_ITM_COUNT_AVG_SPREAD_SQL,
        trusted_assets=trusted_assets,
        rows=rows,
        answer=answer,
        metric_value=f"{count_int:,}",
    )


def _direct_itm_share(ctx: _DirectContext) -> GenieMessageResponse | None:
    """In-the-money borrowers as a share of the whole population."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
    trusted_assets = ctx.trusted_assets
    try:
        row = sql_client.execute_one(_CANONICAL_ITM_SHARE_SQL) or {}
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_itm_share_failed", exc=exc)
        return None
    raw_count = row.get("in_the_money_borrowers")
    raw_total = row.get("total_borrowers")
    if raw_count is None or raw_total is None:
        _emit_genie_warning("direct_canonical_genie_itm_share_bad_count")
        return None
    try:
        count_int = int(raw_count)
        total_int = int(raw_total)
    except (TypeError, ValueError):
        _emit_genie_warning("direct_canonical_genie_itm_share_bad_count")
        return None
    raw_share = row.get("borrower_share_pct")
    if raw_share is None:
        share_float = None
    else:
        try:
            share_float = float(raw_share)
        except (TypeError, ValueError):
            share_float = None
    rows = [
        {
            "in_the_money_borrowers": count_int,
            "total_borrowers": total_int,
            "borrower_share_pct": share_float,
            "refreshed_at": row.get("refreshed_at"),
        }
    ]
    share_text = f"{share_float:,.2f}%" if share_float is not None else "not available"
    answer = (
        f"{count_int:,} of {total_int:,} borrowers pass the refinance-economics "
        f"screen, or {share_text} of the current borrower coverage. This uses "
        f"{borrower_asset} at unique borrower grain and is broader than the "
        "marketing-eligible Lead Queue subset."
    )
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_ITM_SHARE_SQL,
        trusted_assets=trusted_assets,
        rows=rows,
        answer=answer,
        metric_value=share_text,
    )


def _direct_equity_threshold_count(
    ctx: _DirectContext,
    equity_scope: CanonicalEquityThresholdScope,
) -> GenieMessageResponse | None:
    """Borrower count or share above an equity threshold named in the question."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
    trusted_assets = ctx.trusted_assets
    sql_query = (
        _CANONICAL_EQUITY_THRESHOLD_STRICT_COUNT_SQL
        if equity_scope.strict_greater
        else _CANONICAL_EQUITY_THRESHOLD_COUNT_SQL
    )
    equity_params = {"min_equity_pct": equity_scope.threshold_pct}
    try:
        row = sql_client.execute_one(sql_query, equity_params) or {}
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_equity_threshold_failed", exc=exc)
        return None
    raw_count = row.get("equity_capacity_borrowers")
    if raw_count is None:
        _emit_genie_warning("direct_canonical_genie_equity_threshold_bad_count")
        return None
    try:
        count_int = int(raw_count)
        total_int = int(row.get("total_borrowers") or 0)
    except (TypeError, ValueError):
        _emit_genie_warning(
            "direct_canonical_genie_equity_threshold_bad_count",
            value_type=type(raw_count).__name__,
        )
        return None
    raw_share = row.get("borrower_share_pct")
    if raw_share is None:
        share_float = None
    else:
        try:
            share_float = float(raw_share)
        except (TypeError, ValueError):
            share_float = None
    raw_avg_equity = row.get("avg_equity_pct")
    try:
        avg_equity_float = float(raw_avg_equity) if raw_avg_equity is not None else None
    except (TypeError, ValueError):
        avg_equity_float = None
    rows = [
        {
            "equity_capacity_borrowers": count_int,
            "total_borrowers": total_int,
            "borrower_share_pct": share_float,
            "avg_equity_pct": avg_equity_float,
            "min_equity_pct": equity_scope.threshold_pct,
            "comparison": ">" if equity_scope.strict_greater else ">=",
            "refreshed_at": row.get("refreshed_at"),
        }
    ]
    comparison_text = "more than" if equity_scope.strict_greater else "at least"
    share_text = f"{share_float:,.2f}%" if share_float is not None else "not available"
    avg_equity_text = (
        f"{avg_equity_float:,.1f}%" if avg_equity_float is not None else "not available"
    )
    equity_metric_value = share_text if equity_scope.asks_share else f"{count_int:,}"
    population_text = (
        f" ({share_text} of {total_int:,} borrowers)" if total_int > 0 else ""
    )
    threshold_text = _format_pct_threshold(equity_scope.threshold_pct)
    answer = (
        f"{count_int:,} borrowers have {comparison_text} "
        f"{threshold_text}% modeled home equity"
        f"{population_text}. Their average modeled equity is "
        f"{avg_equity_text}. This is an equity-capacity screen from {borrower_asset}, "
        "not a filed-permit count."
    )
    return trusted_response(
        question=question,
        sql_query=sql_query,
        trusted_assets=trusted_assets,
        rows=rows,
        answer=answer,
        metric_value=equity_metric_value,
    )


def _direct_negative_equity_count(
    ctx: _DirectContext,
    negative_equity_scope: CanonicalNegativeEquityScope,
) -> GenieMessageResponse | None:
    """Negative-equity borrower count or share."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
    trusted_assets = ctx.trusted_assets
    try:
        row = sql_client.execute_one(_CANONICAL_NEGATIVE_EQUITY_COUNT_SQL) or {}
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_negative_equity_failed", exc=exc)
        return None
    raw_count = row.get("underwater_borrowers")
    if raw_count is None:
        _emit_genie_warning("direct_canonical_genie_negative_equity_bad_count")
        return None
    try:
        count_int = int(raw_count)
        total_int = int(row.get("total_borrowers") or 0)
    except (TypeError, ValueError):
        _emit_genie_warning(
            "direct_canonical_genie_negative_equity_bad_count",
            value_type=type(raw_count).__name__,
        )
        return None
    raw_share = row.get("borrower_share_pct")
    try:
        share_float = float(raw_share) if raw_share is not None else None
    except (TypeError, ValueError):
        share_float = None
    raw_median_ltv = row.get("median_underwater_ltv_pct")
    try:
        median_ltv_float = float(raw_median_ltv) if raw_median_ltv is not None else None
    except (TypeError, ValueError):
        median_ltv_float = None
    raw_high_tail = row.get("high_ltv_tail_borrowers")
    try:
        high_tail_int = int(raw_high_tail) if raw_high_tail is not None else None
    except (TypeError, ValueError):
        high_tail_int = None
    share_text = f"{share_float:,.2f}%" if share_float is not None else "not available"
    median_ltv_text = (
        f"{median_ltv_float:,.1f}%" if median_ltv_float is not None else "not available"
    )
    tail_text = (
        f" {high_tail_int:,} records exceed 500% modeled LTV, so I am showing the median rather than an average over the long tail."
        if high_tail_int and high_tail_int > 0
        else ""
    )
    rows = [
        {
            "underwater_borrowers": count_int,
            "total_borrowers": total_int,
            "borrower_share_pct": share_float,
            "median_underwater_ltv_pct": median_ltv_float,
            "high_ltv_tail_borrowers": high_tail_int,
            "refreshed_at": row.get("refreshed_at"),
        }
    ]
    population_text = (
        f" ({share_text} of {total_int:,} borrowers)" if total_int > 0 else ""
    )
    answer = (
        f"{count_int:,} borrowers are underwater with modeled LTV above 100%"
        f"{population_text}. The median modeled LTV for those borrowers is "
        f"{median_ltv_text}.{tail_text} This uses {borrower_asset} at borrower "
        "grain; it is a portfolio-risk screen, not an outreach-ready Lead Queue count."
    )
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_NEGATIVE_EQUITY_COUNT_SQL,
        trusted_assets=trusted_assets,
        rows=rows,
        answer=answer,
        metric_value=share_text if negative_equity_scope.asks_share else f"{count_int:,}",
    )


def _direct_listed_count(
    ctx: _DirectContext,
    listed_count_scope: CanonicalListedCountScope,
) -> GenieMessageResponse | None:
    """Listed-for-sale borrower count, nationwide or for one state."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
    trusted_assets = ctx.trusted_assets
    sql_query = (
        _CANONICAL_LISTED_COUNT_BY_STATE_SQL
        if listed_count_scope.state_code
        else _CANONICAL_LISTED_COUNT_SQL
    )
    listed_params = (
        {"state": listed_count_scope.state_code}
        if listed_count_scope.state_code
        else None
    )
    try:
        row = sql_client.execute_one(sql_query, listed_params) or {}
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_listed_count_failed", exc=exc)
        return None
    raw_count = row.get("listed_borrowers")
    if raw_count is None:
        _emit_genie_warning("direct_canonical_genie_listed_count_bad_count")
        return None
    try:
        count_int = int(raw_count)
    except (TypeError, ValueError):
        _emit_genie_warning(
            "direct_canonical_genie_listed_count_bad_count",
            value_type=type(raw_count).__name__,
        )
        return None
    scope_text = (
        f" in {listed_count_scope.state_name} ({listed_count_scope.state_code})"
        if listed_count_scope.state_code and listed_count_scope.state_name
        else ""
    )
    rows = [
        {
            "listed_borrowers": count_int,
            "state": listed_count_scope.state_code,
            "refreshed_at": row.get("refreshed_at"),
        }
    ]
    answer = (
        f"{count_int:,} borrowers{scope_text} currently have a live listed-for-sale "
        f"signal in {borrower_asset}. This is the broad MLS/listing trigger count; "
        "Lead Queue actions may be smaller after marketing-eligibility and consent filters."
    )
    return trusted_response(
        question=question,
        sql_query=sql_query,
        trusted_assets=trusted_assets,
        rows=rows,
        answer=answer,
        metric_value=f"{count_int:,}",
    )


def _direct_investor_count(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Investor / multi-property borrower count."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
    trusted_assets = ctx.trusted_assets
    try:
        row = sql_client.execute_one(_CANONICAL_INVESTOR_COUNT_SQL) or {}
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_investor_count_failed", exc=exc)
        return None
    raw_count = row.get("investor_borrowers")
    if raw_count is None:
        _emit_genie_warning("direct_canonical_genie_investor_count_bad_count")
        return None
    try:
        count_int = int(raw_count)
    except (TypeError, ValueError):
        _emit_genie_warning(
            "direct_canonical_genie_investor_count_bad_count",
            value_type=type(raw_count).__name__,
        )
        return None
    rows = [
        {
            "investor_borrowers": count_int,
            "refreshed_at": row.get("refreshed_at"),
        }
    ]
    answer = (
        f"{count_int:,} borrowers are in the Investor / Multi-Property segment "
        f"from {borrower_asset}. This uses Owner Link-derived segment membership "
        "at unique borrower grain."
    )
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_INVESTOR_COUNT_SQL,
        trusted_assets=trusted_assets,
        rows=rows,
        answer=answer,
        metric_value=f"{count_int:,}",
    )


def _direct_heloc_count(ctx: _DirectContext) -> GenieMessageResponse | None:
    """HELOC-intent borrower count."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
    trusted_assets = ctx.trusted_assets
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
        f"borrowers with at least 35% modeled home equity. Their average equity "
        f"is {equity_text}. This is not a filed-permit or HELOC-intent count; "
        f"it comes from {borrower_asset} and Building Permits are only used when "
        "that source is live."
    )
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_HELOC_COUNT_SQL,
        trusted_assets=trusted_assets,
        rows=rows,
        answer=answer,
        metric_value=f"{count_int:,}",
    )


def _direct_home_equity_distribution(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Home-equity distribution across the population."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
    trusted_assets = ctx.trusted_assets
    try:
        rows = _redact_genie_rows(
            sql_client.execute(_CANONICAL_HOME_EQUITY_DISTRIBUTION_SQL)
        ) or []
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_equity_distribution_failed", exc=exc)
        return None
    if rows:
        strongest = next(
            (row for row in rows if str(row.get("equity_band") or "") == "75%+"),
            rows[-1],
        )
        answer = (
            f"I grouped borrowers from {borrower_asset} into modeled home-equity bands. "
            "The 15% threshold is the baseline Portfolio Builder equity screen; "
            "35% and higher is the home-equity capacity screen used for HELOC/cash-out "
            "analysis. "
            f"The strongest-equity band shown is {strongest.get('equity_band')} with "
            f"{int(strongest.get('borrowers') or 0):,} borrowers."
        )
    else:
        answer = (
            f"{borrower_asset} returned no home-equity distribution rows for the "
            "current refreshed coverage."
        )
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_HOME_EQUITY_DISTRIBUTION_SQL,
        trusted_assets=trusted_assets,
        rows=rows,
        answer=answer,
    )


def _direct_addressable_market(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Addressable versus contactable market sizing."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
    lead_population_asset = ctx.lead_population_asset
    try:
        row = sql_client.execute_one(_CANONICAL_ADDRESSABLE_MARKET_SQL) or {}
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_addressable_market_failed", exc=exc)
        return None
    raw_count = row.get("marketable_population")
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
            "marketable_population": count_int,
            "definition": (
                "Portfolio Builder default: owner-occupied, open first lien, "
                "marketing eligible, at least 15% modeled equity"
            ),
            "refreshed_at": row.get("refreshed_at"),
        }
    ]
    answer = (
        f"The current addressable market is {count_int:,} borrowers at the "
        f"Portfolio Builder grain in {borrower_asset}. This matches the default "
        "Portfolio Builder denominator: owner-occupied properties, open first "
        "lien, marketing eligible, and at least 15% modeled equity. It is not "
        f"the narrower ranked Lead Queue subset in {lead_population_asset}."
    )
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_ADDRESSABLE_MARKET_SQL,
        trusted_assets=[borrower_asset],
        rows=rows,
        answer=answer,
        metric_value=f"{count_int:,}",
    )


def _direct_ranked_lead_population(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Size of the ranked Lead Queue population."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    lead_population_asset = ctx.lead_population_asset
    try:
        row = sql_client.execute_one(_CANONICAL_RANKED_LEAD_POPULATION_SQL) or {}
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_ranked_lead_population_failed", exc=exc)
        return None
    raw_count = row.get("ranked_leads")
    if not isinstance(raw_count, int | float | str):
        _emit_genie_warning(
            "direct_canonical_genie_ranked_lead_population_bad_count",
            value_type=type(raw_count).__name__,
        )
        return None
    try:
        count_int = int(raw_count)
    except ValueError:
        _emit_genie_warning(
            "direct_canonical_genie_ranked_lead_population_bad_count",
            value_type=type(raw_count).__name__,
        )
        return None
    rows = [{"ranked_leads": count_int, "refreshed_at": row.get("refreshed_at")}]
    answer = (
        f"The ranked Lead Queue subset has {count_int:,} marketing-eligible "
        f"leads in {lead_population_asset}. Use this number for operational "
        "queue sizing; use the addressable-market answer for the broader "
        "Portfolio Builder denominator."
    )
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_RANKED_LEAD_POPULATION_SQL,
        trusted_assets=[lead_population_asset],
        rows=rows,
        answer=answer,
        metric_value=f"{count_int:,}",
    )

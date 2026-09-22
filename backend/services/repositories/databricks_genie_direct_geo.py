"""Direct-answer branches for in-the-money geography drill-downs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.services.databricks_sql import (
    DatabricksSqlError,
)
from backend.services.genie_answers import GenieMessageResponse
from backend.services.repositories.databricks_genie_canonical import (
    _CANONICAL_ITM_BY_STATE_SQL,
    _CANONICAL_ITM_TOP_LEAD_QUEUE_ZIPS_SQL,
    _CANONICAL_ITM_TOP_ZIPS_SQL,
    _canonical_itm_lead_queue_zip_scope,
)
from backend.services.repositories.databricks_genie_policy_helpers import (
    _emit_genie_warning,
    _redact_genie_rows,
)

if TYPE_CHECKING:
    from backend.services.repositories.databricks_genie_direct import _DirectContext


def _direct_itm_zip(ctx: _DirectContext) -> GenieMessageResponse | None:
    """Top in-the-money ZIP codes."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
    lead_population_asset = ctx.lead_population_asset
    lead_queue_scope = _canonical_itm_lead_queue_zip_scope(question)
    zip_sql = (
        _CANONICAL_ITM_TOP_LEAD_QUEUE_ZIPS_SQL
        if lead_queue_scope
        else _CANONICAL_ITM_TOP_ZIPS_SQL
    )
    try:
        rows = _redact_genie_rows(sql_client.execute(zip_sql)) or []
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_itm_zips_failed", exc=exc)
        return None
    zip_trusted_assets = [lead_population_asset] if lead_queue_scope else [borrower_asset]
    if rows:
        top = rows[0]
        count_key = "in_the_money_leads" if lead_queue_scope else "in_the_money_borrowers"
        grain_text = (
            f"the ranked Lead Queue subset in {lead_population_asset}"
            if lead_queue_scope
            else f"the current borrower coverage in {borrower_asset}"
        )
        answer = (
            "I ranked ZIP codes by unique records passing the refinance-economics screen "
            f"from {grain_text}. "
            f"The current leader is ZIP {top.get('zip')} ({top.get('state')}) "
            f"with {int(top.get(count_key) or 0):,} "
            f"{'leads' if lead_queue_scope else 'borrowers'}."
        )
    else:
        answer = (
            "The trusted population returned no refinance-economics ZIP rows for "
            "the requested grain."
        )
    return trusted_response(
        question=question,
        sql_query=zip_sql,
        trusted_assets=zip_trusted_assets,
        rows=rows,
        answer=answer,
    )


def _direct_itm_state_breakdown(ctx: _DirectContext) -> GenieMessageResponse | None:
    """In-the-money breakdown by state."""
    question = ctx.question
    sql_client = ctx.sql_client
    trusted_response = ctx.trusted_response
    borrower_asset = ctx.borrower_asset
    trusted_assets = ctx.trusted_assets
    try:
        rows = _redact_genie_rows(sql_client.execute(_CANONICAL_ITM_BY_STATE_SQL)) or []
    except DatabricksSqlError as exc:
        _emit_genie_warning("direct_canonical_genie_itm_state_breakdown_failed", exc=exc)
        return None
    if rows:
        top = rows[0]
        broad_total = sum(int(row.get("in_the_money_borrowers") or 0) for row in rows)
        lead_queue_total = sum(int(row.get("lead_queue_borrowers") or 0) for row in rows)
        answer = (
            "I broke down borrowers passing the refinance-economics screen by state from "
            f"{borrower_asset}. "
            f"{top.get('state')} currently leads with "
            f"{int(top.get('in_the_money_borrowers') or 0):,} borrowers. "
            f"Across the returned states, {broad_total:,} borrowers pass the broad "
            f"economic screen; the Lead Queue action opens the {lead_queue_total:,} "
            "marketing-eligible subset after operational eligibility filters."
        )
    else:
        answer = (
            "The trusted borrower table returned no refinance-economics state rows "
            "for the current refreshed data coverage."
        )
    return trusted_response(
        question=question,
        sql_query=_CANONICAL_ITM_BY_STATE_SQL,
        trusted_assets=trusted_assets,
        rows=rows,
        answer=answer,
        # The outermost statement joins two CTEs and has no WHERE,
        # QUALIFY or inner-join ON of its own, so there is no filter
        # position to read: `broad` restricts on `in_the_money = TRUE` and
        # `lead_queue` on `array_contains(segment_codes,'itm')`, both
        # inside CTE bodies the position gate never visits. Per the gold
        # CASE ladder those two are the same population, so `itm` is exact.
        # Declared rather than inferred: without it the queue opens every
        # borrower in those states while the button still promises the
        # eligible in-the-money subset.
        cohort_segments=("itm",),
    )

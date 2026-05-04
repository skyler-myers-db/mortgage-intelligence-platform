"""Computed-from-UC Genie fallback for canonical intents.

When the ``genie`` circuit breaker is OPEN we cannot reach the live
Genie space. Earlier behaviour returned a curated catalog answer with
hardcoded specific numbers (counts, dollar amounts, sample borrower
IDs) — that violated CLAUDE.md's "no mock fallback in the running app"
contract. The honest fix is two-staged:

  1. The repository's plain "warming up" message remains the safety
     net (no fabricated content, no claims of lineage).
  2. For a small set of canonical intents we additionally try a
     deterministic SQL query against ``mip.gold.*`` and return the
     REAL count / breakdown with ``source="computed_fallback"``. The
     UI surfaces this distinctly so a user can tell at a glance that
     they are looking at a deterministic local query result, not a
     live Genie answer.

Each compute function:

* Returns a ``GenieMessageResponse`` whose ``answer`` text is composed
  from values returned by the SQL — never literals.
* Sets ``source="computed_fallback"``.
* Sets ``trusted_assets`` to the actual gold table(s) the query read.
* Catches no exceptions: the caller (the Genie repo) wraps the call
  in a TTL cache + try/except so a UC outage falls back cleanly to
  the plain warming-up message.

If a canonical intent depends on a column that's blocked-FALSE upstream
(currently ``has_permit`` and ``listed_for_sale`` pending the Cotality
MLS + Building-Permits Delta Shares), the compute function still runs
and returns the truthful zero-count answer with a note so the user
knows the data dependency is the reason — never silently massaging the
response with a fabricated number.

2026-05-04 follow-up to user feedback Q1 ("How is Genie figuring this
stuff out?"). The answer should always be: from your own UC data.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.databricks_sql_helpers import qualify
from backend.services.genie_answers import GenieMessageResponse
from backend.services.resilience import TTLCache

log = logging.getLogger(__name__)

_BORROWER_360 = qualify("gold", "borrower_360")
_SEGMENT_POPULATION = qualify("gold", "segment_population")

# Per-intent cache. Hits when the same compute is requested within the
# TTL window — typical during a Genie outage where the user retries the
# same question. Bounded to ~30s so any stale state clears quickly when
# the gold pipeline next refreshes.
_COMPUTE_CACHE: TTLCache = TTLCache()
_COMPUTE_CACHE_TTL_S: float = 30.0


def _format_thousands(n: int | float | None) -> str:
    """``12840`` → ``"12,840"``. Defensive on None / non-numeric input
    (the SQL connector may surface integer columns as Decimal in some
    driver versions)."""
    try:
        return f"{int(n or 0):,}"
    except (TypeError, ValueError):
        return "0"


def _format_money_k(n: int | float | None) -> str:
    """``312000`` → ``"$312K"``. None / zero → ``"$0"``."""
    try:
        thousands = int((n or 0) / 1000)
        return f"${thousands:,}K" if thousands else "$0"
    except (TypeError, ValueError):
        return "$0"


def compute_in_the_money_count(client: DatabricksSqlClient) -> GenieMessageResponse:
    """How many borrowers are currently in the money?"""
    sql = (
        f"SELECT COUNT(*) AS cnt, "
        f"  CAST(ROUND(AVG(rate_spread_bps)) AS INT) AS avg_spread_bps "
        f"FROM {_BORROWER_360} WHERE in_the_money = TRUE"
    )
    row = client.execute_one(sql) or {}
    cnt = int(row.get("cnt") or 0)
    avg_bps = int(row.get("avg_spread_bps") or 0)
    return GenieMessageResponse(
        conversation_id="computed-fallback",
        question="",
        answer=(
            f"{_format_thousands(cnt)} borrowers are currently in the money "
            f"with an average rate spread of {avg_bps} bps above par. "
            "Computed from the live gold.borrower_360 table because the "
            "Genie space is reconnecting."
        ),
        source="computed_fallback",
        trusted_assets=[_BORROWER_360],
        metric_value=_format_thousands(cnt),
    )


def compute_itm_zips(client: DatabricksSqlClient) -> GenieMessageResponse:
    """Top 5 ZIPs by in-the-money borrower count."""
    sql = (
        f"SELECT zip, city, state, COUNT(*) AS itm_borrowers "
        f"FROM {_BORROWER_360} "
        f"WHERE in_the_money = TRUE AND zip IS NOT NULL AND length(zip) >= 5 "
        f"GROUP BY zip, city, state "
        f"ORDER BY itm_borrowers DESC "
        f"LIMIT 5"
    )
    rows = client.execute(sql) or []
    rows = [
        {
            "zip": str(r.get("zip") or "")[:5],
            "city": f"{r.get('city') or ''}, {r.get('state') or ''}".strip(", "),
            "itm_borrowers": int(r.get("itm_borrowers") or 0),
        }
        for r in rows
    ]
    if not rows:
        return GenieMessageResponse(
            conversation_id="computed-fallback",
            question="",
            answer=(
                "No in-the-money borrowers are currently visible in the "
                "gold.borrower_360 table. (The Genie space is reconnecting.)"
            ),
            source="computed_fallback",
            trusted_assets=[_BORROWER_360],
        )
    head = ", ".join(
        f"{r['zip']} {r['city']} ({_format_thousands(r['itm_borrowers'])})"
        for r in rows
    )
    return GenieMessageResponse(
        conversation_id="computed-fallback",
        question="",
        answer=(
            f"Top in-the-money ZIPs (computed live from gold.borrower_360): "
            f"{head}. The Genie space is reconnecting; this answer is from a "
            "deterministic local SQL aggregation."
        ),
        source="computed_fallback",
        trusted_assets=[_BORROWER_360],
        table_rows=rows,
    )


def compute_heloc_by_state(client: DatabricksSqlClient) -> GenieMessageResponse:
    """HELOC opportunity by state — borrowers with strong equity AND
    a permit signal. ``has_permit`` is blocked-FALSE in gold today
    (pending Cotality Building-Permits Delta Share); the compute
    surfaces that honestly with a zero-count breakdown rather than
    fabricating numbers.
    """
    sql = (
        f"SELECT state, "
        f"  COUNT(*) AS permit_equity_borrowers, "
        f"  CAST(AVG(equity_estimate) AS BIGINT) AS avg_equity_usd "
        f"FROM {_BORROWER_360} "
        f"WHERE has_permit = TRUE AND equity_pct >= 35 "
        f"GROUP BY state "
        f"ORDER BY permit_equity_borrowers DESC"
    )
    rows = client.execute(sql) or []
    table_rows = [
        {
            "state": str(r.get("state") or "").upper()[:2],
            "permit_equity_borrowers": int(r.get("permit_equity_borrowers") or 0),
            "avg_equity": _format_money_k(r.get("avg_equity_usd")),
        }
        for r in rows
        if r.get("state")
    ]
    if not table_rows:
        return GenieMessageResponse(
            conversation_id="computed-fallback",
            question="",
            answer=(
                "Zero borrowers currently match the HELOC opportunity criteria "
                "(permit + equity ≥ 35%) in gold.borrower_360. The Cotality "
                "Building Permits Delta Share isn't yet live in this workspace, "
                "so the has_permit flag is FALSE for every row. Once the share "
                "lands, this answer will populate from the same query without "
                "any code change."
            ),
            source="computed_fallback",
            trusted_assets=[_BORROWER_360],
        )
    head = ", ".join(
        f"{r['state']} ({_format_thousands(r['permit_equity_borrowers'])})"
        for r in table_rows
    )
    return GenieMessageResponse(
        conversation_id="computed-fallback",
        question="",
        answer=(
            f"HELOC opportunity by state (live from gold.borrower_360): {head}. "
            "Computed from a deterministic local aggregation while the Genie "
            "space reconnects."
        ),
        source="computed_fallback",
        trusted_assets=[_BORROWER_360],
        table_rows=table_rows,
    )


def compute_purchase_listed(client: DatabricksSqlClient) -> GenieMessageResponse:
    """Borrowers in the Listed-for-Sale segment (purchase mortgage
    candidates). ``listed_for_sale`` is blocked-FALSE upstream pending
    the Cotality MLS Delta Share — the compute returns zero honestly
    rather than fabricating sample borrowers.
    """
    sql = (
        f"SELECT COUNT(*) AS cnt FROM {_BORROWER_360} WHERE listed_for_sale = TRUE"
    )
    row = client.execute_one(sql) or {}
    cnt = int(row.get("cnt") or 0)
    if cnt == 0:
        return GenieMessageResponse(
            conversation_id="computed-fallback",
            question="",
            answer=(
                "Zero borrowers currently carry the listed-for-sale flag in "
                "gold.borrower_360. The Cotality MLS Delta Share isn't yet "
                "live in this workspace, so listed_for_sale is FALSE for every "
                "row. Once the share lands, this number will populate from the "
                "same query without any code change."
            ),
            source="computed_fallback",
            trusted_assets=[_BORROWER_360],
            metric_value="0",
        )
    return GenieMessageResponse(
        conversation_id="computed-fallback",
        question="",
        answer=(
            f"{_format_thousands(cnt)} borrowers fall into the Listed for "
            "Sale segment — purchase mortgage candidates. Computed live from "
            "gold.borrower_360 while the Genie space reconnects."
        ),
        source="computed_fallback",
        trusted_assets=[_BORROWER_360],
        metric_value=_format_thousands(cnt),
    )


def compute_refi_plus_heloc(client: DatabricksSqlClient) -> GenieMessageResponse:
    """Borrowers who clear both the refi spread floor AND the HELOC
    equity cushion — the high-revenue cross-sell branch."""
    sql = (
        f"SELECT COUNT(*) AS cnt "
        f"FROM {_BORROWER_360} "
        f"WHERE in_the_money = TRUE AND equity_pct >= 35"
    )
    row = client.execute_one(sql) or {}
    cnt = int(row.get("cnt") or 0)
    return GenieMessageResponse(
        conversation_id="computed-fallback",
        question="",
        answer=(
            f"{_format_thousands(cnt)} borrowers clear both the refi spread "
            "floor and the HELOC equity cushion (≥35%) — the highest-revenue "
            "cross-sell branch. Computed live from gold.borrower_360 while "
            "the Genie space reconnects."
        ),
        source="computed_fallback",
        trusted_assets=[_BORROWER_360],
        metric_value=_format_thousands(cnt),
    )


def compute_retention(client: DatabricksSqlClient) -> GenieMessageResponse:
    """Borrowers in the Retention Risk segment, from gold.segment_population."""
    sql = (
        f"SELECT count, avg_score "
        f"FROM {_SEGMENT_POPULATION} "
        f"WHERE state = '_ALL' AND segment_code = 'retention' "
        f"LIMIT 1"
    )
    row = client.execute_one(sql) or {}
    cnt = int(row.get("count") or 0)
    avg = int(row.get("avg_score") or 0)
    return GenieMessageResponse(
        conversation_id="computed-fallback",
        question="",
        answer=(
            f"{_format_thousands(cnt)} current customers carry retention-risk "
            f"signals (refinance, listing, or competitor lien) with an average "
            f"opportunity score of {avg}. Computed live from "
            "gold.segment_population while the Genie space reconnects."
        ),
        source="computed_fallback",
        trusted_assets=[_SEGMENT_POPULATION],
        metric_value=_format_thousands(cnt),
    )


# ---------------------------------------------------------------------------
# Intent → compute-function dispatch
# ---------------------------------------------------------------------------

# Map intent keys (defined in genie_answers.py's _INTENTS list) to the
# compute function that will produce a real answer from gold tables.
# Keep this short — every entry is one more SQL surface that must keep
# working through gold-schema changes. Add an entry here only when the
# canonical question is one users frequently ask AND the SQL is cheap.
COMPUTE_BY_INTENT: dict[str, Callable[[DatabricksSqlClient], GenieMessageResponse]] = {
    "in_the_money":   compute_in_the_money_count,
    "itm_zips":       compute_itm_zips,
    "heloc_by_state": compute_heloc_by_state,
    "purchase":       compute_purchase_listed,
    "refi_plus_heloc": compute_refi_plus_heloc,
    "retention":      compute_retention,
}


def try_compute(
    intent_key: str | None,
    client: DatabricksSqlClient | None,
) -> GenieMessageResponse | None:
    """Try to compute a real answer for ``intent_key``. Returns None
    when there is no compute function for the intent OR when the
    underlying SQL fails (so the caller can fall back to the plain
    warming-up message instead of bubbling the warehouse error to the
    user).

    Caches the response per intent for ``_COMPUTE_CACHE_TTL_S`` so
    repeated retries during a Genie outage don't slam the warehouse.
    """
    if intent_key is None or client is None:
        return None
    compute_fn = COMPUTE_BY_INTENT.get(intent_key)
    if compute_fn is None:
        return None
    cache_key = f"genie.compute.{intent_key}"
    cached = _COMPUTE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        result = compute_fn(client)
    except Exception as exc:  # noqa: BLE001 — UC outage is caller-handled
        log.warning(
            "genie computed fallback failed for intent=%s: %s",
            intent_key,
            exc,
        )
        return None
    _COMPUTE_CACHE.set(cache_key, result, _COMPUTE_CACHE_TTL_S)
    return result


def reset_cache() -> None:
    """Test hook — clears the per-intent compute cache."""
    _COMPUTE_CACHE.reset()


__all__ = [
    "COMPUTE_BY_INTENT",
    "compute_heloc_by_state",
    "compute_in_the_money_count",
    "compute_itm_zips",
    "compute_purchase_listed",
    "compute_refi_plus_heloc",
    "compute_retention",
    "reset_cache",
    "try_compute",
]


# Suppress unused-import lint without dropping the imports — Any is
# referenced in the docstring contract for the Callable signature.
_ = Any

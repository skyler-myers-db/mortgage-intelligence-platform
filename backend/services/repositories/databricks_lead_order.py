"""The ONE ORDER BY and keyset builder for Lead Queue reads (D-audit-reads-a, tables-02).

Three reads order the ranked rows: ``DatabricksLeadRepository.list`` /
``list_page`` (the lead_population and borrower_360 templates) and
``LeadCohortQueries.list_with_identity`` (its ``ranked`` CTE over ``matched``).
They used to spell the rank order separately; a server sort (and a keyset
cursor that must resume exactly where a page ended) needs ONE builder, so a
cursor minted by one read can never resume under another read's order.

Order, for a server sort over a closed column map::

    ORDER BY <col> <dir> NULLS LAST, <rank_order> DESC, <borrower_id> ASC

and for ``rank`` (the default, byte-for-byte today's order)::

    ORDER BY <rank_order> DESC, <borrower_id> ASC

``rank_order`` is ``-lp.rank_overall`` on lead_population (rank 1 first) and
``b.opportunity_score`` on borrower_360, exactly what ``matched_cohort_sql``
projects as ``__rank_order``. The keyset tuple is ``(sort_is_null,
sort_value, rank_order, borrower_id)`` (``(rank_order, borrower_id)`` for
rank); the predicate is null-aware and every value is a bound parameter.
Column names come only from the closed map below, never from a request.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Literal, cast

from backend.schemas.lead import LeadSummary

LeadSort = Literal["rank", "score", "equity", "rate", "confidence"]
LeadSortDir = Literal["asc", "desc"]

LEAD_SORTS: tuple[LeadSort, ...] = ("rank", "score", "equity", "rate", "confidence")
LEAD_SORT_DIRS: tuple[LeadSortDir, ...] = ("asc", "desc")

# The closed column map: request token -> gold column (the same column on
# gold.lead_population and gold.borrower_360).
SORT_COLUMNS: dict[str, str] = {
    "score": "opportunity_score",
    "equity": "equity_estimate",
    "rate": "rate_spread_bps",
    "confidence": "confidence",
}

RANK_ORDER_KEY = "__rank_order"


@dataclass(frozen=True)
class LeadOrderSource:
    """How one statement names the ordering columns."""

    column_prefix: str
    rank_expr: str
    id_expr: str

    def column(self, sort: str) -> str:
        return f"{self.column_prefix}{SORT_COLUMNS[sort]}"


LEAD_POPULATION_SOURCE = LeadOrderSource("lp.", "-lp.rank_overall", "lp.borrower_id")
BORROWER_360_SOURCE = LeadOrderSource("b.", "b.opportunity_score", "b.borrower_id")
# ``list_with_identity``'s ranked CTE reads the projected columns of ``matched``.
MATCHED_SOURCE = LeadOrderSource("", RANK_ORDER_KEY, "borrower_id")

# The ``__rank_order`` projection each list template prepends to its columns
# (prepended, so the shared projection constants stay verbatim and still end
# with ``refreshed_at``).
LEAD_POPULATION_RANK_PROJECTION = f"{LEAD_POPULATION_SOURCE.rank_expr} AS {RANK_ORDER_KEY}"
BORROWER_360_RANK_PROJECTION = f"{BORROWER_360_SOURCE.rank_expr} AS {RANK_ORDER_KEY}"

KeysetValue = int | float | str | None
LeadKeyset = tuple[KeysetValue, ...]


@dataclass(frozen=True)
class LeadOrder:
    """A validated sort: ``rank`` carries no direction."""

    sort: LeadSort = "rank"
    sort_dir: LeadSortDir | None = None

    @classmethod
    def of(cls, sort: str | None, sort_dir: str | None) -> LeadOrder:
        token = sort or "rank"
        if token not in LEAD_SORTS:
            raise ValueError("unknown lead sort")
        if token == "rank":
            return cls()
        direction = sort_dir or "desc"
        if direction not in LEAD_SORT_DIRS:
            raise ValueError("unknown lead sort direction")
        return cls(sort=cast(LeadSort, token), sort_dir=direction)

    @property
    def is_rank(self) -> bool:
        return self.sort == "rank"


RANK = LeadOrder()


def order_by_sql(order: LeadOrder, source: LeadOrderSource) -> str:
    """The ``ORDER BY`` clause (keyword included) for ``order`` over ``source``."""

    tail = f"{source.rank_expr} DESC, {source.id_expr} ASC"
    if order.is_rank:
        return f"ORDER BY {tail}"
    direction = "ASC" if order.sort_dir == "asc" else "DESC"
    return f"ORDER BY {source.column(order.sort)} {direction} NULLS LAST, {tail}"


def _rank_after(source: LeadOrderSource) -> str:
    return (
        f"({source.rank_expr} < :lead_after_rank "
        f"OR ({source.rank_expr} = :lead_after_rank AND {source.id_expr} > :lead_after_id))"
    )


def keyset_clause(
    order: LeadOrder,
    after: LeadKeyset | None,
    source: LeadOrderSource,
    params: dict[str, object],
) -> str:
    """``AND (...)``: the rows strictly after ``after`` in ``order``; '' for page 0.

    Binds ``lead_after_*`` parameters into ``params``. Raises ``ValueError``
    for a tuple whose shape does not match the order.
    """

    if after is None:
        return ""
    if order.is_rank:
        if len(after) != 2:
            raise ValueError("rank keyset is (rank_order, borrower_id)")
        rank_value, borrower_id = after
        params["lead_after_rank"] = rank_value
        params["lead_after_id"] = borrower_id
        return f"AND {_rank_after(source)}"
    if len(after) != 4:
        raise ValueError("sort keyset is (sort_is_null, sort_value, rank_order, borrower_id)")
    is_null, sort_value, rank_value, borrower_id = after
    params["lead_after_rank"] = rank_value
    params["lead_after_id"] = borrower_id
    column = source.column(order.sort)
    if is_null:
        # NULLS LAST: past a NULL only NULLs remain, in rank order.
        return f"AND ({column} IS NULL AND {_rank_after(source)})"
    params["lead_after_value"] = sort_value
    beyond = ">" if order.sort_dir == "asc" else "<"
    return (
        f"AND ({column} IS NULL "
        f"OR {column} {beyond} :lead_after_value "
        f"OR ({column} = :lead_after_value AND {_rank_after(source)}))"
    )


def _keyset_number(value: object) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("keyset value is not numeric")
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal | float):
        number = float(value)
        return int(number) if number.is_integer() else number
    raise ValueError("keyset value is not numeric")


def keyset_of_row(order: LeadOrder, row: dict[str, object]) -> LeadKeyset:
    """The keyset tuple of one RAW SQL row (never the redacted LeadSummary).

    The redactor coerces a NULL sort value to 0, which would resume a NULLS
    LAST page in the middle of the non-null run, so the tuple is read from
    the row the warehouse returned.
    """

    rank_value = _keyset_number(row.get(RANK_ORDER_KEY))
    borrower_id = str(row["borrower_id"])
    if order.is_rank:
        return (rank_value, borrower_id)
    sort_value = _keyset_number(row.get(SORT_COLUMNS[order.sort]))
    return (1 if sort_value is None else 0, sort_value, rank_value, borrower_id)


@dataclass(frozen=True)
class LeadPage:
    """One served page: the redacted rows, the +1 sentinel and the resume tuple."""

    leads: list[LeadSummary]
    has_more: bool
    last_keyset: LeadKeyset | None

    def copy_with(self, leads: list[LeadSummary]) -> LeadPage:
        return replace(self, leads=leads)


# Every repository filter keyword with its default: a page is cached and
# resumed under the COMPLETE set, so an omitted keyword and its default
# share one cache entry (the warm-cache parity test pins that).
LEAD_FILTER_DEFAULTS: dict[str, Any] = {
    "segment": None,
    "portfolio_id": None,
    "state": None,
    "zip_code": None,
    "county_fips": None,
    "county_fipses": None,
    "state_codes": None,
    "zip_codes": None,
    "city_states": None,
    "borrower_ids": None,
    "segment_codes": None,
    "segment_mode": "any",
    "target_lender_ref": None,
    "cohort_id": None,
    "funnel_stage": None,
    "portfolio_criteria": None,
    "approval_status": None,
    "outreach_status": None,
    "aged_days": None,
    "min_opportunity_score": None,
    "min_rate_spread_bps": None,
    "max_opportunity_score": None,
    "max_rate_spread_bps": None,
}


def lead_page_filters(
    segment: str | None,
    portfolio_id: str | None,
    filters: Mapping[str, object],
) -> dict[str, Any]:
    """The complete filter set of one page read; an unknown keyword is refused."""

    unknown = sorted(set(filters) - set(LEAD_FILTER_DEFAULTS))
    if unknown:
        raise TypeError(f"unknown lead filter keyword(s): {unknown}")
    return {**LEAD_FILTER_DEFAULTS, **filters, "segment": segment, "portfolio_id": portfolio_id}

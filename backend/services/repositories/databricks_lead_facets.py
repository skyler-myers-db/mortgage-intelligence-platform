"""Facet counts for the Lead Queue filter menus (audit tables-06).

One GROUP BY (state, approval) or one single-row conditional count (segment,
product) over the SAME matched cohort the ranked list and its count read
(``LeadCohortQueries.matched_cohort_sql``), so a menu's numbers can never
apply a different filter than the queue it narrows. Every value is a bound
parameter, every bucket is filtered to a closed vocabulary, and the result is
cached for ``settings.mip_cache_ttl_s`` under a key that covers the dimension
and every filter.
"""

from __future__ import annotations

import json
import time
from typing import Any

from backend.config.settings import settings
from backend.schemas.lead import SEGMENT_CODE_VALUES
from backend.schemas.lead_facets import LeadFacetCounts, LeadFacetDimension
from backend.schemas.portfolio import PortfolioCriteria
from backend.schemas.usps import USPS_STATE_CODES
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.repositories.databricks_lead_cohorts import (
    LeadCohortFilters,
    LeadCohortQueries,
)
from backend.services.repositories.databricks_portfolio_predicates import PORTFOLIO_PRODUCT_CODES
from backend.services.resilience import TTLCache

#: The Lead Queue PRODUCT menu labels, keyed by their Portfolio product code.
PRODUCT_FACET_LABELS: dict[str, str] = {
    "refi": "Refi",
    "heloc": "HELOC",
    "cash-out": "Cash-out",
    "purchase": "Purchase",
    "retention": "Retention",
}
APPROVAL_FACET_VALUES: tuple[str, ...] = ("pending", "approved", "rejected", "hold")


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return 0
    try:
        return max(0, int(value))
    except ValueError:
        return 0


class DatabricksLeadFacetRepository:
    """Per-option counts for one Lead Queue filter dimension."""

    def __init__(
        self,
        client: DatabricksSqlClient,
        *,
        cache: TTLCache | None = None,
        cache_ttl_s: float | None = None,
    ) -> None:
        self._client = client
        self._cache = cache if cache is not None else TTLCache()
        self._cache_ttl_s = settings.mip_cache_ttl_s if cache_ttl_s is None else cache_ttl_s
        self._queries = LeadCohortQueries(client, cache_ttl_s=self._cache_ttl_s, clock=time)

    def facets(
        self,
        dimension: LeadFacetDimension,
        *,
        segment: str | None,
        portfolio_id: str | None,
        state: str | None = None,
        zip_code: str | None = None,
        county_fips: str | None = None,
        county_fipses: list[str] | None = None,
        state_codes: list[str] | None = None,
        zip_codes: list[str] | None = None,
        city_states: list[str] | None = None,
        borrower_ids: list[str] | None = None,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
        target_lender_ref: str | None = None,
        cohort_id: str | None = None,
        funnel_stage: str | None = None,
        portfolio_criteria: PortfolioCriteria | None = None,
        approval_status: str | None = None,
        outreach_status: str | None = None,
        aged_days: int | None = None,
        min_opportunity_score: int | None = None,
        min_rate_spread_bps: float | None = None,
        max_opportunity_score: int | None = None,
        max_rate_spread_bps: float | None = None,
    ) -> LeadFacetCounts:
        cache_key = "lead_facets:" + json.dumps(
            {
                "dimension": dimension,
                "segment": segment,
                "portfolio_id": portfolio_id,
                "state": state,
                "zip_code": zip_code,
                "county_fips": county_fips,
                "county_fipses": county_fipses,
                "state_codes": state_codes,
                "zip_codes": zip_codes,
                "city_states": city_states,
                "borrower_ids": borrower_ids,
                "segment_codes": segment_codes,
                "segment_mode": segment_mode,
                "target_lender_ref": target_lender_ref,
                "cohort_id": cohort_id,
                "funnel_stage": funnel_stage,
                "portfolio_criteria": (
                    portfolio_criteria.model_dump(mode="json", exclude_none=True)
                    if portfolio_criteria is not None
                    else None
                ),
                "approval_status": approval_status,
                "outreach_status": outreach_status,
                "aged_days": aged_days,
                "min_opportunity_score": min_opportunity_score,
                "min_rate_spread_bps": min_rate_spread_bps,
                "max_opportunity_score": max_opportunity_score,
                "max_rate_spread_bps": max_rate_spread_bps,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        if self._cache_ttl_s > 0:
            cached = self._cache.get(cache_key)
            if isinstance(cached, LeadFacetCounts):
                return cached
        filters = LeadCohortFilters(
            segment=segment,
            state=state,
            zip_code=zip_code,
            county_fips=county_fips,
            county_fipses=county_fipses,
            state_codes=state_codes,
            zip_codes=zip_codes,
            city_states=city_states,
            borrower_ids=borrower_ids,
            segment_codes=segment_codes,
            segment_mode=segment_mode,
            target_lender_ref=target_lender_ref,
            funnel_stage=funnel_stage,
            portfolio_criteria=portfolio_criteria,
            min_opportunity_score=min_opportunity_score,
            min_rate_spread_bps=min_rate_spread_bps,
            max_opportunity_score=max_opportunity_score,
            max_rate_spread_bps=max_rate_spread_bps,
            approval_status=approval_status,
            outreach_status=outreach_status,
            aged_days=aged_days,
        )
        result = self._read(dimension, filters)
        if self._cache_ttl_s > 0:
            self._cache.set(cache_key, result, self._cache_ttl_s)
        return result

    def _read(self, dimension: LeadFacetDimension, filters: LeadCohortFilters) -> LeadFacetCounts:
        matched_sql, params, _uses_lead_population = self._queries.matched_cohort_sql(
            filters,
            include_lead_columns=True,
        )
        if dimension in ("state", "approval"):
            return self._grouped(dimension, matched_sql, params)
        return self._single_row(dimension, matched_sql, params)

    def _grouped(
        self,
        dimension: LeadFacetDimension,
        matched_sql: str,
        params: dict[str, object],
    ) -> LeadFacetCounts:
        """state / approval: one GROUP BY; total = the sum of the kept buckets."""

        column = "m.state" if dimension == "state" else "m.approval_status"
        statement = (
            f"WITH matched AS (\n  {matched_sql}\n)\n"
            f"SELECT {column} AS facet_value, COUNT(*) AS facet_count\n"
            f"FROM matched m\nGROUP BY {column}"
        )
        allowed = USPS_STATE_CODES if dimension == "state" else frozenset(APPROVAL_FACET_VALUES)
        counts: dict[str, int] = {}
        for row in self._client.execute(statement, params):
            value = str(row.get("facet_value") or "").strip()
            value = value.upper() if dimension == "state" else value.lower()
            if value in allowed:
                counts[value] = counts.get(value, 0) + _count(row.get("facet_count"))
        if dimension == "state":
            buckets = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        else:
            buckets = [(value, counts[value]) for value in APPROVAL_FACET_VALUES if value in counts]
        return LeadFacetCounts(total_matching=sum(count for _, count in buckets), buckets=buckets)

    def _single_row(
        self,
        dimension: LeadFacetDimension,
        matched_sql: str,
        params: dict[str, object],
    ) -> LeadFacetCounts:
        """segment / product: one row, COUNT(*) plus a conditional sum per option."""

        facet_params: dict[str, object] = dict(params)
        cases: list[str] = []
        labels: list[str] = []
        if dimension == "segment":
            for index, code in enumerate(SEGMENT_CODE_VALUES):
                name = f"facet_segment_{index}"
                facet_params[name] = code
                cases.append(
                    f"SUM(CASE WHEN array_contains(m.segment_codes, :{name}) THEN 1 ELSE 0 END) AS facet_{index}"
                )
                labels.append(code)
        else:
            for index, (product, label) in enumerate(PRODUCT_FACET_LABELS.items()):
                names: list[str] = []
                for offset, offer_code in enumerate(PORTFOLIO_PRODUCT_CODES[product]):
                    name = f"facet_product_{index}_{offset}"
                    facet_params[name] = offer_code
                    names.append(f":{name}")
                cases.append(
                    "SUM(CASE WHEN m.recommended_offer_code IN "
                    f"({', '.join(names)}) THEN 1 ELSE 0 END) AS facet_{index}"
                )
                labels.append(label)
        statement = (
            f"WITH matched AS (\n  {matched_sql}\n)\n"
            f"SELECT COUNT(*) AS facet_total, {', '.join(cases)}\nFROM matched m"
        )
        row: dict[str, Any] = self._client.execute_one(statement, facet_params) or {}
        return LeadFacetCounts(
            total_matching=_count(row.get("facet_total")),
            buckets=[(label, _count(row.get(f"facet_{index}"))) for index, label in enumerate(labels)],
        )

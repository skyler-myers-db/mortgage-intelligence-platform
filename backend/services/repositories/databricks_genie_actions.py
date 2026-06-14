"""Genie governed-action routing helpers.

These helpers translate trusted Genie result rows into auditable UI actions
and Lead Queue routes. They are intentionally pure: no SQL calls, no Lakebase
writes, and no background reads.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlencode

from backend.schemas.common import validate_public_borrower_id
from backend.services.genie_answers import GenieActionSuggestion, GenieVisualizationSpec
from backend.services.repositories.databricks_genie_canonical import (
    _retention_competitor_lien_list_question,
)


def _sql_hash(sql_query: str | None) -> str | None:
    if not sql_query:
        return None
    normalized = re.sub(r"\s+", " ", sql_query.strip())
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _borrower_ids_from_rows(rows: list[dict[str, Any]] | None) -> list[str]:
    ids: list[str] = []
    for row in rows or []:
        value = row.get("borrower_id")
        if not isinstance(value, str):
            continue
        try:
            borrower_id = validate_public_borrower_id(value)
        except ValueError:
            continue
        if borrower_id not in ids:
            ids.append(borrower_id)
    return ids


def _total_matching_from_rows(rows: list[dict[str, Any]] | None) -> int:
    for row in rows or []:
        raw = row.get("total_matching_borrowers")
        if raw is None:
            continue
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            continue
    return len(rows or [])


def _row_values(
    rows: list[dict[str, Any]] | None,
    *columns: str,
    digits: int | None = None,
    upper: bool = False,
) -> list[str]:
    values: list[str] = []
    for row in rows or []:
        for column in columns:
            raw = row.get(column)
            if raw is None:
                continue
            value = str(raw).strip()
            if upper:
                value = value.upper()
            if digits is not None:
                if re.fullmatch(r"\d+\.0", value):
                    value = value.split(".", 1)[0]
                if re.fullmatch(r"\d+", value) and len(value) < digits:
                    value = value.zfill(digits)
                match = re.fullmatch(rf"\d{{{digits}}}", value)
                if not match:
                    continue
            elif upper and not re.fullmatch(r"[A-Z]{2}", value):
                continue
            if value not in values:
                values.append(value)
    return values


def _segment_codes_from_question(question: str) -> list[str]:
    q = question.lower()
    codes: list[str] = []
    if re.search(r"\b(in[-\s]?the[-\s]?money|itm|refi|refinance)\b", q):
        codes.append("itm")
    if "home equity" in q or "heloc" in q:
        codes.append("equity")
    if "investor" in q or "multi-property" in q or "multi property" in q:
        codes.append("investor")
    if (
        "retention" in q
        or "competitor lien" in q
        or "current customer" in q
        or "recapture" in q
        or "at risk" in q
        or "going to a competitor" in q
    ):
        codes.append("retention")
    if "listed" in q or "for sale" in q or "purchase" in q:
        codes.append("listed")
    if "permit" in q:
        codes.append("permit")
    return list(dict.fromkeys(codes))


def _portfolio_criteria_from_question(question: str) -> dict[str, Any]:
    q = question.lower()
    criteria: dict[str, Any] = {}
    if re.search(r"\b(owner[-\s]?occupied|primary residence)\b", q):
        criteria["occupancy"] = "Owner-occupied"
    elif re.search(r"\b(non[-\s]?owner[-\s]?occupied|investment property)\b", q):
        criteria["occupancy"] = "Non-owner-occupied"

    if re.search(r"\b(open|active)\s+(heloc|second lien|2nd lien)\b", q):
        criteria["lien_status"] = "Open HELOC"
    elif re.search(r"\b(open|active)\s+(first lien|1st lien)\b", q):
        criteria["lien_status"] = "Open 1st lien"
    elif re.search(r"\b(free\s*(?:&|and)\s*clear)\b", q):
        criteria["lien_status"] = "Free & clear"

    if re.search(r"\bsingle[-\s]?property owner\b", q):
        criteria["owner_link"] = "Single-property owner"
    elif re.search(r"\b(multi[-\s]?property|2[-\s]*4 properties)\b", q):
        criteria["owner_link"] = "Multi-property (2-4)"
    elif re.search(r"\b(portfolio investor|5\+ properties)\b", q):
        criteria["owner_link"] = "Portfolio investor (5+)"

    if "listed for sale" in q:
        criteria["purchase_intent"] = "Listed for sale"
    elif "heloc intent" in q or "heloc propensity" in q or "permit" in q:
        criteria["purchase_intent"] = "HELOC intent"

    if "cash-out" in q or "cash out" in q:
        criteria["product"] = "Cash-out"
    elif re.search(r"\bheloc\b", q):
        criteria["product"] = "HELOC"
    elif re.search(r"\b(retention|recapture|at risk|going to a competitor)\b", q):
        criteria["product"] = "Retention"

    if "current customer" in q:
        criteria["lender_relationship"] = "Current customer"
    elif "former customer" in q:
        criteria["lender_relationship"] = "Former customer"
    elif "competitor" in q and not _retention_competitor_lien_list_question(question):
        criteria["lender_relationship"] = "Competitor customer"

    equity_match = re.search(
        r"(?:equity|ltv|loan[-\s]?to[-\s]?value)[^\d]{0,24}(15|25|40)\s*%",
        q,
    )
    if equity_match:
        criteria["min_equity_pct_label"] = f"≥ {equity_match.group(1)}%"
    return criteria


def _portfolio_criteria_from_sql(sql_query: str | None) -> dict[str, Any]:
    if not sql_query:
        return {}
    sql = re.sub(r"\s+", " ", sql_query.strip().lower())
    if not sql:
        return {}

    criteria: dict[str, Any] = {}
    if re.search(r"\bis_owner_occupied\s*=\s*(true|1)\b", sql) or re.search(
        r"\bwhere\s+is_owner_occupied\b", sql
    ):
        criteria["occupancy"] = "Owner-occupied"
    elif re.search(r"\bis_owner_occupied\s*=\s*(false|0)\b", sql):
        criteria["occupancy"] = "Non-owner-occupied"

    second_pos_positive = bool(
        re.search(r"\bcoalesce\(\s*second_pos_amount\s*,\s*0\s*\)\s*>\s*0\b", sql)
        or re.search(r"\bsecond_pos_amount\s*>\s*0\b", sql)
    )
    second_pos_zero = bool(
        re.search(r"\bcoalesce\(\s*second_pos_amount\s*,\s*0\s*\)\s*=\s*0\b", sql)
        or re.search(r"\bsecond_pos_amount\s*=\s*0\b", sql)
        or re.search(r"\bsecond_pos_amount\s+is\s+null\b", sql)
    )
    first_pos_positive = bool(
        re.search(r"\bcoalesce\(\s*current_lien_balance\s*,\s*0\s*\)\s*>\s*0\b", sql)
        or re.search(r"\bcurrent_lien_balance\s*>\s*0\b", sql)
    )
    first_pos_zero = bool(
        re.search(r"\bcoalesce\(\s*current_lien_balance\s*,\s*0\s*\)\s*<=\s*0\b", sql)
        or re.search(r"\bcurrent_lien_balance\s*<=\s*0\b", sql)
        or re.search(r"\bcurrent_lien_balance\s+is\s+null\b", sql)
    )
    if second_pos_positive:
        criteria["lien_status"] = "Open HELOC"
    elif first_pos_positive and second_pos_zero:
        criteria["lien_status"] = "Open 1st lien"
    elif first_pos_zero and second_pos_zero:
        criteria["lien_status"] = "Free & clear"

    if re.search(r"\bcoalesce\(\s*related_property_count\s*,\s*1\s*\)\s*<=\s*1\b", sql):
        criteria["owner_link"] = "Single-property owner"
    elif re.search(
        r"\bcoalesce\(\s*related_property_count\s*,\s*1\s*\)\s+between\s+2\s+and\s+4\b",
        sql,
    ):
        criteria["owner_link"] = "Multi-property (2-4)"
    elif re.search(r"\bcoalesce\(\s*related_property_count\s*,\s*1\s*\)\s*>=\s*5\b", sql):
        criteria["owner_link"] = "Portfolio investor (5+)"

    has_listing = bool(re.search(r"\blisted_for_sale\s*=\s*true\b", sql))
    has_heloc_intent = bool(
        re.search(r"\bhas_permit\s*=\s*true\b", sql)
        or re.search(r"\bhas_heloc_propensity_trigger\s*=\s*true\b", sql)
    )
    if has_listing and has_heloc_intent:
        criteria["purchase_intent"] = "Both"
    elif has_listing:
        criteria["purchase_intent"] = "Listed for sale"
    elif has_heloc_intent:
        criteria["purchase_intent"] = "HELOC intent"

    if re.search(r"\bis_current_customer\s*=\s*true\b", sql):
        criteria["lender_relationship"] = "Current customer"
    elif re.search(r"\bis_former_customer\s*=\s*true\b", sql):
        criteria["lender_relationship"] = "Former customer"
    elif re.search(r"\bis_competitor_lien\s*=\s*true\b", sql):
        criteria["lender_relationship"] = "Competitor customer"

    if (
        re.search(r"\brecommended_offer_code\s*=\s*'cash_out'\b", sql)
        or re.search(
            r'\brecommended_offer_code\s*=\s*"cash_out"\b',
            sql,
        )
        or re.search(r"\brecommended_offer_code\s+in\s*\([^)]*'cash_out'[^)]*\)", sql)
    ):
        criteria["product"] = "Cash-out"
    elif re.search(r"\brecommended_offer_code\s*=\s*'retention'\b", sql):
        criteria["product"] = "Retention"
    elif re.search(r"\brecommended_offer_code\s*=\s*'heloc'\b", sql):
        criteria["product"] = "HELOC"

    equity_match = re.search(r"\bequity_pct\s*>=\s*(15|25|40)\b", sql)
    if equity_match:
        criteria["min_equity_pct_label"] = f"≥ {equity_match.group(1)}%"
    return criteria


def _route_from_answer_rows(
    *,
    question: str,
    rows: list[dict[str, Any]] | None,
    borrower_ids: list[str],
    sql_query: str | None = None,
) -> tuple[str, dict[str, Any]]:
    params: dict[str, str] = {}
    filter_criteria: dict[str, Any] = {}
    zips = _row_values(rows, "zip", "zip_code", "zipcode", "postal_code", digits=5)
    counties = _row_values(rows, "county_fips_5", "county_fips", "fips_5", "fips", digits=5)
    states = _row_values(rows, "state", "state_code", upper=True)
    if states and re.search(r"\bwhich\s+state\b|\bwhat\s+state\b", question.lower()):
        states = states[:1]
    segment_codes = _segment_codes_from_question(question)
    portfolio_criteria = {
        **_portfolio_criteria_from_question(question),
        **_portfolio_criteria_from_sql(sql_query),
    }

    if borrower_ids:
        filter_criteria["borrower_ids"] = borrower_ids
        params["borrower_ids"] = ",".join(borrower_ids)
    elif zips:
        params["zips"] = ",".join(zips)
        filter_criteria["zips"] = zips
    elif counties:
        if len(counties) == 1:
            params["county"] = counties[0]
            filter_criteria["county"] = counties[0]
        else:
            params["counties"] = ",".join(counties)
            filter_criteria["counties"] = counties
    elif states:
        params["states"] = ",".join(states)
        filter_criteria["states"] = states

    if segment_codes:
        if len(segment_codes) == 1:
            params["segment"] = segment_codes[0]
        else:
            params["segment_codes"] = ",".join(segment_codes)
            params["segment_mode"] = "all"
        filter_criteria["segment_codes"] = segment_codes
        filter_criteria["segment_mode"] = "all" if len(segment_codes) > 1 else "any"

    if portfolio_criteria:
        filter_criteria["portfolio_criteria"] = portfolio_criteria
        for key in (
            "occupancy",
            "lien_status",
            "lender_relationship",
            "product",
            "target_lender_ref",
            "min_equity_pct_label",
            "owner_link",
            "purchase_intent",
        ):
            value = portfolio_criteria.get(key)
            if isinstance(value, str) and value.strip():
                params[key] = value

    if params:
        return f"/lead-queue?{urlencode(params)}", filter_criteria
    return "/lead-queue", filter_criteria


def _suggest_genie_actions(
    *,
    question: str,
    rows: list[dict[str, Any]] | None,
    trusted_assets: list[str],
    visualization: GenieVisualizationSpec | None,
    conversation_id: str | None,
    message_id: str | None,
    question_hash: str | None,
    sql_query: str | None,
    source: str = "genie",
) -> list[GenieActionSuggestion]:
    actions: list[GenieActionSuggestion] = []
    borrower_ids = _borrower_ids_from_rows(rows)
    row_count = len(rows) if rows else 0
    q = question.lower()
    base_criteria: dict[str, Any] = {
        "source": source,
        "source_assets": trusted_assets,
        "visualization_kind": visualization.kind if visualization else None,
        "row_count": row_count,
    }
    sql_digest = _sql_hash(sql_query)
    if sql_digest:
        base_criteria["sql_hash"] = sql_digest
    lead_queue_route, result_filters = _route_from_answer_rows(
        question=question,
        rows=rows,
        borrower_ids=borrower_ids,
        sql_query=sql_query,
    )
    if result_filters:
        base_criteria["result_filters"] = result_filters
    if borrower_ids:
        criteria = dict(base_criteria)
        actions.append(
            GenieActionSuggestion(
                id="save-borrowers",
                label=f"Save {len(borrower_ids)} borrower{'' if len(borrower_ids) == 1 else 's'}",
                action_type="save_borrowers",
                description="Add returned borrowers to the governed saved workspace.",
                borrower_ids=borrower_ids,
                criteria=criteria,
            )
        )
        criteria = dict(base_criteria)
        route = f"/borrower-360/{borrower_ids[0]}"
        actions.append(
            GenieActionSuggestion(
                id="show-first-rationale",
                label="Show why first borrower is ranked",
                action_type="show_rationale",
                description="Open Borrower 360 for the top returned borrower.",
                route=route,
                borrower_ids=[borrower_ids[0]],
                criteria=criteria,
            )
        )
    if row_count > 0 and result_filters:
        criteria = dict(base_criteria)
        actions.append(
            GenieActionSuggestion(
                id="open-cohort",
                label="Open this cohort in Lead Queue",
                action_type="open_cohort",
                description="Navigate into the lead queue with this Genie result audited.",
                route=lead_queue_route,
                borrower_ids=borrower_ids,
                criteria=criteria,
            )
        )
        criteria = dict(base_criteria)
        actions.append(
            GenieActionSuggestion(
                id="create-campaign-draft",
                label="Create draft campaign",
                action_type="create_draft_campaign",
                description="Create a Lakebase draft campaign from this governed Genie result.",
                route=lead_queue_route,
                borrower_ids=borrower_ids,
                criteria=criteria,
            )
        )
    if borrower_ids and ("offer" in q or "strategy" in q or "10,000" in q):
        criteria = dict(base_criteria)
        route = f"/offer-orchestrator/{borrower_ids[0]}"
        actions.append(
            GenieActionSuggestion(
                id="compare-offers",
                label="Compare offer strategies",
                action_type="compare_offer_strategies",
                description="Audit this strategy comparison and open the offer surface.",
                route=route,
                borrower_ids=borrower_ids[:1],
                criteria=criteria,
            )
        )
    return actions[:5]

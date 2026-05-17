"""Visualization planning helpers for Databricks Genie responses."""
from __future__ import annotations

from typing import Any

from backend.services.genie_answers import GenieVisualizationSpec


def _row_columns(rows: list[dict[str, Any]] | None) -> list[str]:
    if not rows:
        return []
    cols: list[str] = []
    for row in rows[:5]:
        for key in row:
            if key not in cols:
                cols.append(key)
    return cols


_GENIE_IDENTIFIER_COLUMNS = {
    "zip",
    "zip_code",
    "zipcode",
    "postal_code",
    "fips",
    "fips_5",
    "county_fips",
    "county_fips_5",
    "msa_cbsa_code",
    "cbsa_code",
    "census_tract",
    "tract",
    "borrower_id",
    "id",
}


def _is_genie_identifier_column(column: str) -> bool:
    lower = column.lower()
    return lower in _GENIE_IDENTIFIER_COLUMNS or lower.endswith("_id")


def _numeric_columns(rows: list[dict[str, Any]] | None) -> list[str]:
    out: list[str] = []
    for col in _row_columns(rows):
        if _is_genie_identifier_column(col):
            continue
        values = [row.get(col) for row in rows or [] if row.get(col) is not None]
        if values and all(isinstance(v, int | float) for v in values):
            out.append(col)
    return out


def _text_columns(rows: list[dict[str, Any]] | None) -> list[str]:
    out: list[str] = []
    for col in _row_columns(rows):
        values = [row.get(col) for row in rows or [] if row.get(col) is not None]
        if values and all(isinstance(v, str) for v in values):
            out.append(col)
    return out


def _dateish_columns(rows: list[dict[str, Any]] | None) -> list[str]:
    names = []
    for col in _row_columns(rows):
        lower = col.lower()
        if lower.endswith("_date") or lower.endswith("_at") or "snapshot" in lower:
            names.append(col)
    return names


def _label_column(rows: list[dict[str, Any]] | None, question: str) -> str | None:
    cols = _row_columns(rows)
    preferred = [
        "state",
        "zip",
        "zip_code",
        "zipcode",
        "postal_code",
        "fips",
        "fips_5",
        "county_fips",
        "county_fips_5",
        "msa_cbsa_code",
        "cbsa_code",
        "census_tract",
        "tract",
        "borrower_id",
        "clip",
        "id",
        "county",
        "county_name",
        "msa",
        "market",
        "segment",
        "segment_code",
        "recommended_offer",
        "offer_code",
        "product_label",
    ]
    q = question.lower()
    if "zip" in q or "postal" in q:
        preferred = ["zip", "zip_code", "zipcode", "postal_code", *preferred]
    if "county" in q or "counties" in q:
        preferred = [
            "county_fips_5",
            "county_fips",
            "fips_5",
            "fips",
            "county",
            "county_name",
            *preferred,
        ]
    if "fips" in q:
        preferred = ["fips", "fips_5", "county_fips", "county_fips_5", *preferred]
    if "cbsa" in q or "msa" in q:
        preferred = ["msa_cbsa_code", "cbsa_code", "msa", "market", *preferred]
    if "state" in q or "map" in q:
        preferred = ["state", *preferred]
    for col in preferred:
        if col in cols:
            return col
    texts = _text_columns(rows)
    return texts[0] if texts else None


def _value_column(rows: list[dict[str, Any]] | None, question: str) -> str | None:
    nums = _numeric_columns(rows)
    if not nums:
        return None
    q = question.lower()
    preferred = [
        "borrowers",
        "borrower_count",
        "count",
        "marketable_borrowers",
        "addressable_borrowers",
        "in_the_money_borrowers",
        "high_opportunity_borrowers",
        "opportunity_score",
        "avg_score",
        "approval_rate",
        "conversion_rate",
        "rate_spread_bps",
        "equity_pct",
    ]
    if "score" in q:
        preferred = ["opportunity_score", "avg_score", *preferred]
    if "rate" in q:
        preferred = ["approval_rate", "conversion_rate", "rate_spread_bps", *preferred]
    for col in preferred:
        if col in nums:
            return col
    return nums[0]


def _plan_genie_visualization(
    question: str,
    rows: list[dict[str, Any]] | None,
) -> GenieVisualizationSpec | None:
    q = question.lower()
    row_count = len(rows) if rows else 0
    label = _label_column(rows, question)
    value = _value_column(rows, question)
    date_col = (_dateish_columns(rows) or [None])[0]
    cols = set(_row_columns(rows))

    if "borrower_id" in cols and row_count > 0:
        return GenieVisualizationSpec(
            kind="borrower_list",
            title="Borrower drill-down",
            x="borrower_id",
            y=value,
            reason="result includes borrower_id rows",
        )
    if ("strategy" in q or "10,000" in q or "outreach touches" in q) and row_count > 0:
        return GenieVisualizationSpec(
            kind="strategy_board",
            title="Strategy board",
            x=label,
            y=value,
            reason="strategy-oriented prompt with returned rows",
        )
    if ("map" in q or "geo" in q or "where" in q) and label == "state" and value:
        return GenieVisualizationSpec(
            kind="map",
            title=f"{value} by {label}",
            x=label,
            y=value,
            reason="geography prompt with state column",
        )
    if ("trend" in q or "over time" in q or "by week" in q or "daily" in q) and date_col and value:
        return GenieVisualizationSpec(
            kind="line",
            title=f"{value} trend",
            x=date_col,
            y=value,
            reason="time-oriented prompt with date/snapshot column",
        )
    if row_count == 1 and value:
        return GenieVisualizationSpec(
            kind="metric",
            title=value,
            y=value,
            reason="single-row numeric result",
        )
    if label and value and row_count >= 2:
        return GenieVisualizationSpec(
            kind="bar",
            title=f"{value} by {label}",
            x=label,
            y=value,
            reason="categorical result with numeric measure",
        )
    if row_count > 0:
        return GenieVisualizationSpec(kind="table", title="Query result")
    return None

"""Visualization planning helpers for Databricks Genie responses."""
from __future__ import annotations

import re
from decimal import Decimal
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


_FLAG_STRINGS = frozenset({"true", "false"})
COHORT_LABEL_COLUMN = "cohort"


def _is_flag_value(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    return isinstance(value, str) and value.strip().lower() in _FLAG_STRINGS


def _flag_columns(rows: list[dict[str, Any]] | None) -> list[str]:
    """Columns whose every non-null value is a boolean (or "true"/"false").

    Live capture 2026-09-08: a four-flag co-occurrence result charted as
    twelve bars all labelled "true" because the SQL result serialised the
    booleans as strings and the first all-string column became the label.
    A flag is a filter, not a category axis and not a measure.
    """

    out: list[str] = []
    for col in _row_columns(rows):
        values = [row.get(col) for row in rows or [] if row.get(col) is not None]
        if values and all(_is_flag_value(v) for v in values):
            out.append(col)
    return out


# The Genie SQL result serialises every cell as text ("1036", "40.70"), so a
# numeric column is one whose non-null cells all PARSE as numbers. Live capture
# 2026-09-08: with an isinstance check the planner never saw a measure in a
# live result and returned "table" for every answer, leaving charts to the
# client's guesswork.
_NUMERIC_TEXT_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?$")


def _is_numeric_cell(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float | Decimal):
        return True
    return isinstance(value, str) and bool(_NUMERIC_TEXT_RE.match(value.strip()))


def _numeric_columns(rows: list[dict[str, Any]] | None) -> list[str]:
    out: list[str] = []
    flags = set(_flag_columns(rows))
    for col in _row_columns(rows):
        if _is_genie_identifier_column(col) or col in flags:
            continue
        values = [row.get(col) for row in rows or [] if row.get(col) is not None]
        if values and all(_is_numeric_cell(v) for v in values):
            out.append(col)
    return out


def _text_columns(rows: list[dict[str, Any]] | None) -> list[str]:
    """String columns that are neither flags nor numbers-as-text."""

    out: list[str] = []
    flags = set(_flag_columns(rows))
    numeric = set(_numeric_columns(rows))
    for col in _row_columns(rows):
        if col in flags or col in numeric:
            continue
        values = [row.get(col) for row in rows or [] if row.get(col) is not None]
        if values and all(isinstance(v, str) for v in values):
            out.append(col)
    return out


def _humanize_flag(column: str) -> str:
    words = column.replace("_", " ").strip()
    words = re.sub(r"\s+flag$", "", words, flags=re.IGNORECASE)
    words = re.sub(r"^(?:is|has)\s+", "", words, flags=re.IGNORECASE)
    return words.strip().capitalize() if words else column


def augment_cohort_label(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Add a readable ``cohort`` label when the rows are a flag co-occurrence.

    Only when two or more flag columns describe each row and no other text
    column can label it: each row's flags that are true are joined into one
    label ("In the money · Equity · Investor"). Presentation only — no
    numeric value is added, so the claims verifier's support set is
    unchanged. Returns the input untouched in every other case.
    """

    if not rows:
        return rows
    flags = _flag_columns(rows)
    if len(flags) < 2 or _text_columns(rows) or COHORT_LABEL_COLUMN in _row_columns(rows):
        return rows
    labelled: list[dict[str, Any]] = []
    for row in rows:
        on = [
            _humanize_flag(col)
            for col in flags
            if (value := row.get(col)) is not None
            and (value is True or str(value).strip().lower() == "true")
        ]
        label = " · ".join(on) if on else "None of these"
        labelled.append({COHORT_LABEL_COLUMN: label, **row})
    return labelled


def _labels_are_distinct(rows: list[dict[str, Any]] | None, label: str) -> bool:
    values = [row.get(label) for row in rows or []]
    return len({str(v) for v in values}) == len(values)


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
    flags = set(_flag_columns(rows))
    if COHORT_LABEL_COLUMN in cols:
        return COHORT_LABEL_COLUMN
    for col in preferred:
        if col in cols and col not in flags:
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
    if label and value and row_count >= 2 and _labels_are_distinct(rows, label):
        return GenieVisualizationSpec(
            kind="bar",
            title=f"{value} by {label}",
            x=label,
            y=value,
            reason="categorical result with numeric measure",
        )
    if label and value and row_count >= 2:
        # Repeated labels (a flag, a coarse bucket) cannot be bars: the reader
        # would see several bars with the same name and no way to tell them
        # apart. The table carries the full row.
        return GenieVisualizationSpec(
            kind="table",
            title="Query result",
            reason="label column repeats across rows",
        )
    if row_count > 0:
        return GenieVisualizationSpec(kind="table", title="Query result")
    return None

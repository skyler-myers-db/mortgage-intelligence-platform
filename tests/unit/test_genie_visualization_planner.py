"""Chart planning over Genie rows: flags are filters, not axes.

Live capture 2026-09-08 (deep-research demo): a sub-analysis returned a
four-flag co-occurrence table (in_the_money_flag, equity_flag,
listed_for_sale_flag, investor_flag, borrower_count). The SQL result
serialised the booleans as the strings "true"/"false", the first all-string
column became the bar label, and the chart showed twelve bars all labelled
"true". These tests pin the rules that stop that: a flag column is never a
label or a measure, repeated labels never become bars, and a pure flag
co-occurrence gets one readable cohort label per row.
"""

from __future__ import annotations

from backend.services.repositories.databricks_genie_visualization import (
    COHORT_LABEL_COLUMN,
    _flag_columns,
    _label_column,
    _numeric_columns,
    _plan_genie_visualization,
    _text_columns,
    augment_cohort_label,
)

_FLAG_ROWS = [
    {"in_the_money_flag": "true", "equity_flag": "false", "investor_flag": "false", "borrower_count": 1123},
    {"in_the_money_flag": "true", "equity_flag": "false", "investor_flag": "true", "borrower_count": 694},
    {"in_the_money_flag": "true", "equity_flag": "true", "investor_flag": "true", "borrower_count": 692},
    {"in_the_money_flag": "true", "equity_flag": "true", "investor_flag": "false", "borrower_count": 329},
]

_STATE_ROWS = [
    {"state": "IL", "borrowers": 1200},
    {"state": "TX", "borrowers": 900},
    {"state": "WA", "borrowers": 400},
]


def test_string_and_native_booleans_are_flag_columns_not_text_or_numbers() -> None:
    native = [{"listed": True, "count": 3}, {"listed": False, "count": 5}]
    assert _flag_columns(_FLAG_ROWS) == ["in_the_money_flag", "equity_flag", "investor_flag"]
    assert _flag_columns(native) == ["listed"]
    assert _text_columns(_FLAG_ROWS) == []
    assert _numeric_columns(_FLAG_ROWS) == ["borrower_count"]
    # bool is an int subclass in Python; it must never count as a measure.
    assert _numeric_columns(native) == ["count"]


def test_flag_column_is_never_the_label() -> None:
    assert _label_column(_FLAG_ROWS, "how do the cohorts differ") is None


def test_flag_cooccurrence_gets_a_readable_cohort_label() -> None:
    labelled = augment_cohort_label(_FLAG_ROWS)
    assert labelled is not None
    assert [row[COHORT_LABEL_COLUMN] for row in labelled] == [
        "In the money",
        "In the money · Investor",
        "In the money · Equity · Investor",
        "In the money · Equity",
    ]
    # The original columns and measures are untouched; the label is prepended.
    assert list(labelled[0].keys())[0] == COHORT_LABEL_COLUMN
    assert labelled[0]["borrower_count"] == 1123
    plan = _plan_genie_visualization("how do the cohorts differ", labelled)
    assert plan is not None
    assert plan.kind == "bar"
    assert plan.x == COHORT_LABEL_COLUMN
    assert plan.y == "borrower_count"


def test_augmentation_leaves_ordinary_rows_alone() -> None:
    assert augment_cohort_label(_STATE_ROWS) is _STATE_ROWS
    one_flag = [{"listed": "true", "count": 3}, {"listed": "false", "count": 5}]
    assert augment_cohort_label(one_flag) is one_flag
    with_text = [{"segment": "itm", "listed": "true", "investor": "false", "count": 3}]
    assert augment_cohort_label(with_text) is with_text
    assert augment_cohort_label(None) is None
    assert augment_cohort_label([]) == []


def test_repeated_labels_fall_back_to_the_table() -> None:
    repeated = [
        {"segment": "itm", "count": 10},
        {"segment": "itm", "count": 7},
        {"segment": "equity", "count": 5},
    ]
    plan = _plan_genie_visualization("compare segments", repeated)
    assert plan is not None
    assert plan.kind == "table"
    assert plan.reason == "label column repeats across rows"


def test_ordinary_state_counts_still_chart_as_bars() -> None:
    plan = _plan_genie_visualization("borrowers by state", _STATE_ROWS)
    assert plan is not None
    assert plan.kind == "bar"
    assert plan.x == "state"
    assert plan.y == "borrowers"

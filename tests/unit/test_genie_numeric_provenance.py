"""The claims verifier's provenance map (audit 2026-09-21 ``genie-10`` phase 2).

The support set every claim is checked against became a map from each support
value to HOW the rows produce it. Pinned here:

* one case per support kind (cell, row_count, sum, share, derived, bound);
* the fixed priority on each contested pair, and the column rule (two columns
  at the winning priority leave it None), both taken across EVERY key within
  the verdict tolerance, not the first one;
* that identifier and date string tokens stay ``derived`` exactly as the
  phase-1 derivation labelled them;
* ``occurrences``: set only when every number with that token in the checked
  text is itself a cell-supported claim, never for a token over 32 chars;
* the wire validators, the JSON round trip, and a pre-phase-2 claims payload;
* that the map's KEY SET and the ``returned_value`` label equal the phase-1
  builders, kept below verbatim as test oracles (the tree-swap hash is the
  binding proof; these keep the equivalence pinned in CI);
* the client token grammar (frontend/src/lib/genieFigureTokens.parity.json)
  against ``_NUMBER_RE``: the client locates a figure by the same token.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from backend.services.genie_answers import (
    GenieClaimsSummary,
    GenieMessageResponse,
    GenieProof,
    GenieVerifiedClaim,
)
from backend.services.repositories.databricks_genie_numeric import (
    _NUMBER_RE,
    _is_supported,
    _numeric_claims,
    check_numeric_claims,
    claims_summary,
)
from backend.services.repositories.databricks_genie_numeric_support import (
    _add_distribution_shape_support,
    _add_share_support,
    _add_supported_variants,
    _column_looks_dateish,
    _column_supports_claim_kind,
    _column_unit_multiplier,
    _numeric_cell_value,
    _numeric_support_from_rows,
)
from backend.services.repositories.databricks_genie_visualization import (
    _is_genie_identifier_column,
    _row_columns,
)

REPO = Path(__file__).resolve().parents[2]
PARITY = REPO / "frontend" / "src" / "lib" / "genieFigureTokens.parity.json"


def _item(text: str, token: str, rows: list[dict[str, Any]], question: str = "q") -> Any:
    items = [item for item in check_numeric_claims(text, rows, question).verified if item.token == token]
    assert items, f"{token!r} was not verified in {text!r}"
    return items[0]


# --- one case per support kind --------------------------------------------

_TWO_STATES = [{"state": "IL", "borrowers": 120}, {"state": "TX", "borrowers": 250}]


@pytest.mark.parametrize(
    ("text", "token", "rows", "support", "column", "derivation"),
    [
        ("Texas has 250 borrowers.", "250", _TWO_STATES, "cell", "borrowers", "returned_value"),
        (
            "The query returned 3 states with borrowers.",
            "3",
            [*_TWO_STATES, {"state": "CA", "borrowers": 410}],
            "row_count",
            None,
            "derived_from_rows",
        ),
        ("Together they hold 370 borrowers.", "370", _TWO_STATES, "sum", "borrowers", "derived_from_rows"),
        (
            "Segment A holds 30% of these borrowers.",
            "30%",
            [{"segment": "A", "borrowers": 30}, {"segment": "B", "borrowers": 70}],
            "share",
            "borrowers",
            "derived_from_rows",
        ),
        ("The average is 185 borrowers.", "185", _TWO_STATES, "derived", None, "derived_from_rows"),
        ("Both states have more than 100 borrowers.", "100", _TWO_STATES, "bound", None, "bound"),
    ],
)
def test_each_support_kind(
    text: str, token: str, rows: list[dict[str, Any]], support: str, column: str | None, derivation: str
) -> None:
    item = _item(text, token, rows)
    assert (item.support, item.column, item.derivation) == (support, column, derivation)


# --- the fixed priority ----------------------------------------------------


def test_cell_beats_sum() -> None:
    rows = [{"a_loans": 10, "b_loans": 30}, {"a_loans": 20, "b_loans": 5}]
    # 30 is a cell of b_loans AND the sum of a_loans.
    item = _item("There are 30 loans here.", "30", rows)
    assert (item.support, item.column) == ("cell", "b_loans")


def test_row_count_beats_sum() -> None:
    rows = [{"segment": "A", "loans": 1}, {"segment": "B", "loans": 1}]
    item = _item("Only 2 loans qualify.", "2", rows)
    assert (item.support, item.column) == ("row_count", None)


def test_sum_beats_share() -> None:
    rows = [{"segment": "A", "rate_pct": 40}, {"segment": "B", "rate_pct": 60}]
    # 100 is the rate_pct total and also its cumulative share.
    item = _item("The two segments make up 100% of the rate.", "100%", rows)
    assert (item.support, item.column) == ("sum", "rate_pct")


def test_share_beats_derived() -> None:
    rows = [
        {"segment": "A", "share_rate": 20},
        {"segment": "B", "share_rate": 30},
        {"segment": "C", "share_rate": 50},
    ]
    # 80 is the share of the total above the first bucket AND a cumulative
    # count from the top (derived).
    item = _item("The rest hold 80% of the share.", "80%", rows)
    assert (item.support, item.column) == ("share", "share_rate")


def test_cell_beats_a_derived_identifier_token() -> None:
    rows = [{"loan_id": "L1250", "borrowers": 1250}]
    item = _item("The book has 1,250 borrowers.", "1,250", rows)
    assert (item.support, item.column, item.derivation) == ("cell", "borrowers", "returned_value")


def _keys_within_tolerance(text: str, rows: list[dict[str, Any]]) -> list[tuple[float, Any]]:
    """The map's keys (in insertion order) the claim's value is within the
    verdict tolerance of, with their labels."""

    [claim] = _numeric_claims(text)
    support = _numeric_support_from_rows(rows, claim=claim)
    return [(key, label) for key, label in support.items() if _is_supported(claim.value, {key})]


def test_the_best_support_is_taken_across_every_key_within_tolerance() -> None:
    # Spec 1b: the highest priority among ALL keys within tolerance, not the
    # first one. The identifier token 1203 (derived) is a DISTINCT key that is
    # inserted before the 1204 cell and is within rel_tol 0.001 of 1,204.
    rows = [{"borrower_id": "B1203", "borrowers": 1204}]
    text = "Texas has 1,204 borrowers."
    near = _keys_within_tolerance(text, rows)
    assert [(key, label.kind) for key, label in near][:1] == [(1203.0, "derived")], near
    assert (1204.0, "cell") in [(key, label.kind) for key, label in near], near
    item = _item(text, "1,204", rows)
    assert (item.support, item.column, item.derivation) == ("cell", "borrowers", "returned_value")


def test_two_columns_on_distinct_keys_within_tolerance_name_no_column() -> None:
    # The column rule also spans keys: 1,204 is within tolerance of a cell of
    # a_loans (1204) and of a different cell of b_loans (1203).
    rows = [{"segment": "A", "a_loans": 1204, "b_loans": 1203}]
    text = "Segment A carries 1,204 loans."
    near = {(key, label.kind, label.column) for key, label in _keys_within_tolerance(text, rows)}
    assert {(1204.0, "cell", "a_loans"), (1203.0, "cell", "b_loans")} <= near, near
    item = _item(text, "1,204", rows)
    assert (item.support, item.column, item.derivation) == ("cell", None, "returned_value")


def test_two_columns_at_the_winning_priority_name_no_column() -> None:
    rows = [{"segment": "A", "heloc_loans": 50, "refi_loans": 50}]
    item = _item("Segment A carries 50 loans.", "50", rows)
    assert (item.support, item.column) == ("cell", None)


def test_a_unit_multiplier_column_is_a_cell_with_that_column() -> None:
    rows = [{"segment": "A", "balance_k": 250}]
    item = _item("Segment A carries a $250,000 balance.", "$250,000", rows)
    assert (item.support, item.column, item.derivation) == ("cell", "balance_k", "returned_value")


@pytest.mark.parametrize(
    "extra",
    [
        {"loan_id": "L2026"},
        {"period": "2026-09-30"},
        {"month": "2026-09"},
        {"refreshed_at": "Sep 30, 2026"},
    ],
)
def test_identifier_and_date_tokens_stay_derived(extra: dict[str, Any]) -> None:
    rows = [{"state": "IL", "borrowers": 7, **extra}]
    item = _item("Together the region holds 2,026 borrowers.", "2,026", rows)
    assert (item.support, item.column, item.derivation) == ("derived", None, "derived_from_rows")


# --- occurrences -----------------------------------------------------------


def test_a_unique_cell_figure_counts_its_occurrences() -> None:
    assert _item("Texas has 250 borrowers.", "250", _TWO_STATES).occurrences == 1
    text = "Texas has 250 borrowers, and 250 borrowers is the larger state."
    check = check_numeric_claims(text, _TWO_STATES, "q")
    assert [item.occurrences for item in check.verified if item.token == "250"] == [2, 2]


@pytest.mark.parametrize(
    ("text", "rows", "question"),
    [
        # An identifier occurrence of the same token (filtered as a ZIP).
        (
            "ZIP 60614 leads; 60614 borrowers sit there.",
            [{"zip_label": "north", "borrowers": 60614}],
            "q",
        ),
        # A timeframe occurrence of the same token.
        (
            "Over the last 30 days, 30 borrowers refinanced.",
            [{"state": "IL", "borrowers": 30}, {"state": "TX", "borrowers": 12}],
            "q",
        ),
        # A non-measure occurrence of the same token, far from any unit word.
        (
            "We reviewed 7 regions in this national pass of the overall market. "
            "Illinois alone has 7 borrowers.",
            [{"state": "IL", "borrowers": 7}, {"state": "TX", "borrowers": 19}],
            "q",
        ),
        # The same token derived elsewhere: 120 bps is the spread's mean.
        (
            "Illinois has 120 borrowers at an average spread of 120 bps.",
            [
                {"state": "IL", "borrowers": 120, "spread_bps": 240},
                {"state": "TX", "borrowers": 200, "spread_bps": 0},
            ],
            "q",
        ),
    ],
)
def test_occurrences_is_none_when_the_token_is_used_elsewhere(
    text: str, rows: list[dict[str, Any]], question: str
) -> None:
    cells = [item for item in check_numeric_claims(text, rows, question).verified if item.support == "cell"]
    assert cells, "control: the measure figure must still be a verified cell"
    assert all(item.occurrences is None for item in cells)


def test_a_token_also_used_as_a_query_limit_is_never_located() -> None:
    # The limit filter removes every claim with that value, so the token is
    # never a verified figure and nothing can locate it.
    text = "The top 5 states are listed; Illinois has 5 borrowers."
    check = check_numeric_claims(text, [{"state": "IL", "borrowers": 5}], "Show the top 5 states")
    assert [item for item in check.verified if item.token == "5"] == []


def test_a_token_over_32_characters_is_never_located() -> None:
    rows = [{"segment": "A", "balance_amount": 1234567890123456789012345.67}]
    text = "Segment A holds $1,234,567,890,123,456,789,012,345.67 in balance."
    check = check_numeric_claims(text, rows, "q")
    [item] = check.verified
    assert item.support == "cell"
    assert len(item.token) == 32
    assert item.occurrences is None


def test_occurrences_is_computed_before_the_40_item_cap() -> None:
    # One token straddles the cap: S0's 1,000 is listed (item 0) and repeated
    # after the 40th item. Counting only the listed items would see one cell
    # claim against two numbers in the text and drop the listed occurrences.
    rows = [{"segment": f"S{i}", "loans": 1000 + i} for i in range(45)]
    text = " ".join(f"Segment S{i} has {1000 + i:,} loans." for i in range(45))
    text += " Segment S0 has 1,000 loans again."
    check = check_numeric_claims(text, rows, "q")
    assert check.verified_count == 46 and len(check.verified) == 40
    first = check.verified[0]
    assert (first.token, first.support, first.occurrences) == ("1,000", "cell", 2)
    assert all(item.occurrences == 1 for item in check.verified[1:])


# --- the wire model --------------------------------------------------------


def test_claims_summary_maps_the_provenance_fields() -> None:
    summary = claims_summary(check_numeric_claims("Texas has 250 borrowers.", _TWO_STATES, "q"))
    assert summary is not None
    [item] = summary.items
    assert (item.support, item.column, item.occurrences) == ("cell", "borrowers", 1)


@pytest.mark.parametrize(
    "fields",
    [
        {"support": "derived", "occurrences": 2},
        {"support": None, "occurrences": 1},
        {"support": "row_count", "column": "borrowers"},
        {"support": "bound", "column": "borrowers"},
        {"support": None, "column": "borrowers"},
        {"support": "cell", "occurrences": 0},
        {"support": "cell", "occurrences": 501},
        {"support": "cell", "column": "c" * 129},
        {"support": "cells"},
    ],
)
def test_the_validators_refuse_inconsistent_provenance(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        GenieVerifiedClaim(token="250", kind="number", derivation="returned_value", **fields)


@pytest.mark.parametrize(
    "fields",
    [
        {"support": "cell", "column": "borrowers", "occurrences": 3},
        {"support": "sum", "column": "borrowers"},
        {"support": "share", "column": None},
        {"support": "derived"},
        {"support": "bound"},
        {},
    ],
)
def test_the_validators_accept_consistent_provenance(fields: dict[str, Any]) -> None:
    GenieVerifiedClaim(token="250", kind="number", derivation="returned_value", **fields)


def _response(claims: GenieClaimsSummary) -> GenieMessageResponse:
    return GenieMessageResponse(
        conversation_id="c-1",
        question="Which state has the most borrowers?",
        answer="Texas has 250 borrowers.",
        source="genie",
        trusted_assets=["mip.gold.borrower_360"],
        proof=GenieProof(trusted=True, claims=claims),
    )


def test_a_message_round_trip_keeps_the_provenance_fields() -> None:
    summary = claims_summary(check_numeric_claims("Texas has 250 borrowers.", _TWO_STATES, "q"))
    assert summary is not None
    restored = GenieMessageResponse.model_validate_json(_response(summary).model_dump_json())
    assert restored.proof is not None and restored.proof.claims == summary
    assert restored.proof.claims.items[0].occurrences == 1


def test_a_pre_phase_2_claims_payload_still_validates() -> None:
    stored = {
        "conversation_id": "c-1",
        "question": "Which state has the most borrowers?",
        "answer": "Texas has 250 borrowers.",
        "source": "genie",
        "trusted_assets": ["mip.gold.borrower_360"],
        "proof": {
            "trusted": True,
            "claims": {
                "verified": 1,
                "total": 1,
                "items": [{"token": "250", "kind": "number", "derivation": "returned_value", "section": None}],
            },
        },
    }
    restored = GenieMessageResponse.model_validate(stored)
    assert restored.proof is not None and restored.proof.claims is not None
    item = restored.proof.claims.items[0]
    assert (item.support, item.column, item.occurrences) == (None, None, None)


# --- the phase-1 builders as oracles ----------------------------------------
# Verbatim copies of the phase-1 support-set builder and direct-cell set
# (2931d82c), so CI keeps pinning that the map's key set and the
# returned_value label never drift from them.

_ISO_DATE_CELL_RE = re.compile(r"\d{4}-\d{2}(?:-\d{2})?(?:[T ][\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)?")


def _phase1_support_set(rows: list[dict[str, Any]] | None, kind: Any) -> set[float]:
    values: set[float] = set()
    if rows is None:
        return values
    if kind == "number":
        values.add(float(len(rows)))
    for row in rows:
        for cell in row.values():
            if isinstance(cell, str):
                for token in re.findall(r"-?\d[\d,]*\.?\d*", cell):
                    try:
                        _add_supported_variants(values, float(token.replace(",", "")))
                    except ValueError:
                        continue
    columns = [
        col
        for col in _row_columns(rows)
        if not _is_genie_identifier_column(col)
        and not _column_looks_dateish(col)
        and _column_supports_claim_kind(col, kind)
    ]
    by_column: list[list[float]] = []
    for col in columns:
        col_values = [n for row in rows if (n := _numeric_cell_value(row.get(col))) is not None]
        if col_values:
            by_column.append(col_values)
            for value in col_values:
                _add_supported_variants(values, value)
                if multiplier := _column_unit_multiplier(col):
                    _add_supported_variants(values, value * multiplier)
    for col_values in by_column:
        _add_supported_variants(values, sum(col_values))
        _add_supported_variants(values, min(col_values))
        _add_supported_variants(values, max(col_values))
        _add_supported_variants(values, sum(col_values) / len(col_values))
        for earlier, later in zip(col_values, col_values[1:], strict=False):
            _add_supported_variants(values, abs(later - earlier))
        _add_supported_variants(values, abs(col_values[-1] - col_values[0]))
        _add_supported_variants(values, max(col_values) - min(col_values))
        _add_distribution_shape_support(values, col_values, kind=kind, shares=values)
    if kind == "percent":
        for col in _row_columns(rows):
            if _is_genie_identifier_column(col) or _column_looks_dateish(col):
                continue
            _add_share_support(
                values, [n for row in rows if (n := _numeric_cell_value(row.get(col))) is not None]
            )
    return values


def _phase1_direct_cells(rows: list[dict[str, Any]] | None, kind: Any) -> set[float]:
    values: set[float] = set()
    if not rows:
        return values
    for row in rows:
        for col, cell in row.items():
            if (
                isinstance(cell, str)
                and not _is_genie_identifier_column(col)
                and not _column_looks_dateish(col)
                and not _ISO_DATE_CELL_RE.fullmatch(cell.strip())
            ):
                for token in re.findall(r"-?\d[\d,]*\.?\d*", cell):
                    try:
                        _add_supported_variants(values, float(token.replace(",", "")))
                    except ValueError:
                        continue
    for col in _row_columns(rows):
        if _is_genie_identifier_column(col) or _column_looks_dateish(col) or not _column_supports_claim_kind(col, kind):
            continue
        for row in rows:
            numeric_value = _numeric_cell_value(row.get(col))
            if numeric_value is None:
                continue
            _add_supported_variants(values, numeric_value)
            if multiplier := _column_unit_multiplier(col):
                _add_supported_variants(values, numeric_value * multiplier)
    return values


_COLUMNS = [
    "borrowers",
    "loan_count",
    "avg_rate_spread_bps",
    "in_the_money_share",
    "rate_pct",
    "balance_k",
    "total_balance_amount",
    "avm_value_m",
    "borrower_id",
    "snapshot_date",
    "refreshed_at",
    "note",
    "period",
]


def _random_rows(rng: random.Random) -> list[dict[str, Any]]:
    columns = rng.sample(_COLUMNS, rng.randint(1, 5))
    rows: list[dict[str, Any]] = []
    for _ in range(rng.choice([0, 1, 2, 3, 7, 12])):
        row: dict[str, Any] = {}
        for col in columns:
            if col in {"note", "borrower_id"}:
                row[col] = f"{rng.choice(['B', 'note '])}{rng.randint(0, 99999)}"
            elif col in {"snapshot_date", "period", "refreshed_at"}:
                row[col] = f"2026-0{rng.randint(1, 9)}-{rng.randint(10, 28)}"
            elif col.endswith("_share"):
                row[col] = round(rng.random(), 3)
            else:
                row[col] = rng.choice([rng.randint(0, 500), rng.randint(0, 90000), round(rng.uniform(0, 400), 1)])
        rows.append(row)
    return rows


def _random_text(rng: random.Random, rows: list[dict[str, Any]]) -> str:
    cells = [v for row in rows for v in row.values() if isinstance(v, int | float)]
    parts: list[str] = []
    for _ in range(rng.randint(1, 5)):
        base = rng.choice(cells) if cells and rng.random() < 0.6 else rng.randint(1, 99999)
        unit = rng.choice(["borrowers", "bps spread", "% share", "loans", "in balance"])
        figure = f"{base:,}" if isinstance(base, int) else f"{base}"
        if unit == "% share":
            parts.append(f"about {figure}% share of loans")
        elif unit == "in balance":
            parts.append(f"${figure} in balance")
        else:
            parts.append(f"{figure} {unit}")
    return "The answer shows " + ", ".join(parts) + "."


def test_the_map_key_set_and_the_cell_label_equal_the_phase_1_builders() -> None:
    rng = random.Random(20261007)
    claims_seen = 0
    for _ in range(300):
        rows = _random_rows(rng)
        text = _random_text(rng, rows)
        for claim in _numeric_claims(text):
            support = _numeric_support_from_rows(rows, claim=claim)
            assert set(support) == _phase1_support_set(rows, claim.kind)
            claims_seen += 1
        for item in check_numeric_claims(text, rows, "q").verified:
            if item.support == "bound":
                continue
            matching = [c for c in _numeric_claims(text) if c.raw.strip()[:32] == item.token and c.kind == item.kind]
            direct = any(_is_supported(c.value, _phase1_direct_cells(rows, c.kind)) for c in matching)
            assert (item.derivation == "returned_value") == direct, (text, rows, item)
            assert (item.support == "cell") == (item.derivation == "returned_value")
    assert claims_seen > 500


def test_every_emitted_column_is_a_key_of_the_checked_rows() -> None:
    rng = random.Random(7)
    emitted = 0
    for _ in range(300):
        rows = _random_rows(rng)
        keys = {key for row in rows for key in row}
        for item in check_numeric_claims(_random_text(rng, rows), rows, "q").verified:
            if item.column is not None:
                assert item.column in keys
                emitted += 1
    assert emitted > 50


# --- the client token grammar ------------------------------------------------


def test_the_parity_json_matches_number_re() -> None:
    cases = json.loads(PARITY.read_text(encoding="utf-8"))
    assert len(cases) >= 30
    for case in cases:
        tokens = [match.group(0).strip()[:32] for match in _NUMBER_RE.finditer(case["text"])]
        assert tokens == case["tokens"], case["text"]

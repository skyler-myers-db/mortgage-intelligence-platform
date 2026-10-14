"""Numeric support builders for the Genie claims verifier.

Moved out of ``databricks_genie_numeric`` (audit 2026-09-21 ``genie-10``
phase 2, the 900-line gate): the values the returned rows can support for one
claim, and the helpers that read a column's kind, unit and numeric cells. The
verdict lives in ``databricks_genie_numeric``; nothing here decides whether a
claim is supported.

Phase 2 labels every support value with its provenance (``_Support``): the
KEY SET of ``_numeric_support_from_rows`` is exactly the support set the
verdict has always read, and each key also records how the rows produce it
(a returned cell, the row count, a column sum, a share of a column total, or
another derivation). The fixed priority that resolves a key with several
supports is documented in ``databricks_genie_numeric``.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from backend.services.repositories.databricks_genie_visualization import (
    _is_genie_identifier_column,
    _row_columns,
)

if TYPE_CHECKING:
    from backend.services.repositories.databricks_genie_numeric import _NumericClaim


SupportKind = Literal["cell", "row_count", "sum", "share", "derived"]
#: Highest first: cell > row_count > sum > share > derived.
SUPPORT_PRIORITY: dict[SupportKind, int] = {
    "cell": 4,
    "row_count": 3,
    "sum": 2,
    "share": 1,
    "derived": 0,
}


@dataclass(frozen=True)
class _Support:
    """How the returned rows produce one support value. ``column`` names the
    returned column for cell, sum and share supports; None when two columns
    tie at the winning priority (or for a column-less kind)."""

    kind: SupportKind
    column: str | None = None


_ROW_COUNT = _Support("row_count")
_DERIVED = _Support("derived")


class _ValueSink(Protocol):
    def add(self, value: float, /) -> None: ...


class _LabeledSink:
    """A set-like sink: every key added to ``target`` merges in ``support``.

    The merge keeps the highest priority; two different columns at the
    winning priority leave the column None. It is commutative and
    associative, so the label never depends on insertion order."""

    __slots__ = ("_bare", "_rank", "_support", "_target")

    def __init__(self, target: dict[float, _Support], support: _Support) -> None:
        self._target = target
        self._support = support
        self._rank = SUPPORT_PRIORITY[support.kind]
        self._bare = _Support(support.kind)

    def add(self, value: float, /) -> None:
        current = self._target.get(value)
        if current is None or SUPPORT_PRIORITY[current.kind] < self._rank:
            self._target[value] = self._support
        elif (
            SUPPORT_PRIORITY[current.kind] == self._rank
            and current.column is not None
            and current.column != self._support.column
        ):
            self._target[value] = self._bare


#: An ISO date or timestamp cell ("2026-09", "2026-09-30T12:00:00Z"): its
#: year is not a figure a reader would call a returned value.
_ISO_DATE_CELL_RE = re.compile(r"\d{4}-\d{2}(?:-\d{2})?(?:[T ][\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)?")


_NUMERIC_CELL_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")


def _numeric_support_from_rows(
    rows: list[dict[str, Any]] | None,
    *,
    claim: _NumericClaim,
) -> dict[float, _Support]:
    """The support values for ``claim`` keyed to their provenance.

    The key set is the verifier's support set, unchanged; only the labels
    are new. ``cell`` is exactly what a reader can find in ONE returned cell:
    a numeric token of a string cell in a non-identifier, non-date column
    (not an ISO date cell), and a kind-compatible numeric cell of a
    non-identifier, non-date column with its unit-multiplier variant.
    Identifier and date string tokens and ISO date cells are ``derived``."""

    support: dict[float, _Support] = {}
    if rows is None:
        return support
    if claim.kind == "number":
        _LabeledSink(support, _ROW_COUNT).add(float(len(rows)))
    for row in rows:
        for col, cell in row.items():
            if isinstance(cell, str):
                direct = (
                    not _is_genie_identifier_column(col)
                    and not _column_looks_dateish(col)
                    and not _ISO_DATE_CELL_RE.fullmatch(cell.strip())
                )
                sink = _LabeledSink(support, _Support("cell", col) if direct else _DERIVED)
                for token in re.findall(r"-?\d[\d,]*\.?\d*", cell):
                    try:
                        _add_supported_variants(sink, float(token.replace(",", "")))
                    except ValueError:
                        continue
    columns = [
        col
        for col in _row_columns(rows)
        if not _is_genie_identifier_column(col)
        and not _column_looks_dateish(col)
        and _column_supports_claim_kind(col, claim.kind)
    ]
    by_column: list[tuple[str, list[float]]] = []
    for col in columns:
        col_values: list[float] = []
        for row in rows:
            value = row.get(col)
            numeric_value = _numeric_cell_value(value)
            if numeric_value is not None:
                col_values.append(numeric_value)
        if col_values:
            by_column.append((col, col_values))
            cell_sink = _LabeledSink(support, _Support("cell", col))
            for value in col_values:
                _add_supported_variants(cell_sink, value)
                if multiplier := _column_unit_multiplier(col):
                    _add_supported_variants(cell_sink, value * multiplier)
    derived = _LabeledSink(support, _DERIVED)
    for col, col_values in by_column:
        _add_supported_variants(_LabeledSink(support, _Support("sum", col)), sum(col_values))
        _add_supported_variants(derived, min(col_values))
        _add_supported_variants(derived, max(col_values))
        _add_supported_variants(derived, sum(col_values) / len(col_values))
        # Deltas an honest trend narrative derives from the returned rows:
        # consecutive-row changes, the endpoint change, and the max-min range.
        for earlier, later in zip(col_values, col_values[1:], strict=False):
            _add_supported_variants(derived, abs(later - earlier))
        _add_supported_variants(derived, abs(col_values[-1] - col_values[0]))
        _add_supported_variants(derived, max(col_values) - min(col_values))
        _add_distribution_shape_support(
            derived,
            col_values,
            kind=claim.kind,
            shares=_LabeledSink(support, _Support("share", col)),
        )
    # A PERCENT claim is legitimately derived from a COUNT column (a bucket's
    # share of the column total), which the claim-kind filter above excludes.
    # Only SHARE ratios are contributed here — never raw values, quantiles, or
    # cumulative totals — so a count column can still never support a currency
    # or bps claim (cross-kind laundering stays closed).
    if claim.kind == "percent":
        for col in _row_columns(rows):
            if _is_genie_identifier_column(col) or _column_looks_dateish(col):
                continue
            share_values = [
                numeric
                for row in rows
                if (numeric := _numeric_cell_value(row.get(col))) is not None
            ]
            _add_share_support(_LabeledSink(support, _Support("share", col)), share_values)
    return support


def _add_share_support(values: _ValueSink, col_values: list[float]) -> None:
    """Bucket shares and cumulative shares of a column total (percent only)."""

    if not col_values or len(col_values) > _MAX_DISTRIBUTION_ROWS:
        return
    total = sum(col_values)
    if total <= 0:
        return
    running = 0.0
    for value in col_values:
        _add_supported_variants(values, 100.0 * value / total)
        running += value
        _add_supported_variants(values, 100.0 * running / total)
        _add_supported_variants(values, 100.0 * (total - running) / total)


# Distribution/shape narratives ("the median bucket is X", "62% of borrowers
# sit above N bps", "the top three bands hold 41,203 borrowers") are the whole
# point of a histogram question, but every such figure is DERIVED, never a
# cell literal — so the claims verifier suppressed them and shape questions
# became structurally unanswerable (live persona audit 2026-08-07, VP-Lending
# distribution probe returned 101 rows and zero narrative).
#
# This is a POSITIVE proof path, not an exemption: each value below is
# computed from the returned column, so an unsupported number still fails
# closed. Bounded at O(len(column)) per column.
_MAX_DISTRIBUTION_ROWS = 2_000


def _add_distribution_shape_support(
    values: _ValueSink,
    col_values: list[float],
    *,
    kind: str,
    shares: _ValueSink,
) -> None:
    if not col_values or len(col_values) > _MAX_DISTRIBUTION_ROWS:
        return
    ordered = sorted(col_values)
    count = len(ordered)

    # Order statistics an honest shape narrative cites.
    def _quantile(fraction: float) -> float:
        idx = min(count - 1, max(0, int(round(fraction * (count - 1)))))
        return ordered[idx]

    for fraction in (0.1, 0.25, 0.5, 0.75, 0.9, 0.95):
        _add_supported_variants(values, _quantile(fraction))
    if count % 2 == 0:  # even-length median as the mean of the middle pair
        _add_supported_variants(
            values, (ordered[count // 2 - 1] + ordered[count // 2]) / 2
        )

    total = sum(col_values)
    if total <= 0:
        return
    # Cumulative counts from both ends: on a histogram column these are
    # exactly "N borrowers above/below the threshold" and "the top K bands
    # hold N". Shares are contributed separately (percent claims only).
    running = 0.0
    for value in col_values:
        running += value
        _add_supported_variants(values, running)
        _add_supported_variants(values, total - running)
    # Shares are PERCENT values. Contributing them to a bare COUNT claim let a
    # rounded cumulative share stand in for a fabricated borrower count — the
    # canonical-ranking regression test caught exactly that false accept.
    if kind == "percent":
        _add_share_support(shares, col_values)


def _column_supports_claim_kind(
    column: str,
    kind: Literal["currency", "percent", "bps", "number"],
) -> bool:
    if kind == "number":
        return True
    normalized = column.lower().replace("-", "_").replace(" ", "_")
    if kind == "currency":
        return any(
            term in normalized
            for term in (
                "amount",
                "avm",
                "balance",
                "dollar",
                "equity_value",
                "loan_value",
                "usd",
                "value",
            )
        )
    if kind == "percent":
        return any(
            term in normalized
            for term in ("pct", "percent", "percentage", "ratio", "rate", "share")
        ) and not any(term in normalized for term in ("bps", "basis", "spread"))
    return any(term in normalized for term in ("bps", "basis", "spread"))


def _numeric_cell_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if isinstance(value, str):
        normalized = value.strip().replace(",", "")
        if not _NUMERIC_CELL_RE.fullmatch(normalized):
            return None
        numeric = float(normalized)
        return numeric if math.isfinite(numeric) else None
    return None


def _column_unit_multiplier(column: str) -> float | None:
    lower = column.lower()
    if re.search(r"(?:^|[_\s-])(?:thousands?|k)(?:$|[_\s-])", lower):
        return 1_000
    if re.search(r"(?:^|[_\s-])(?:millions?|m)(?:$|[_\s-])", lower):
        return 1_000_000
    # Basis-point columns are legitimately narrated as percentage points
    # ("+350 bps" == "3.50 percentage points"); support the converted form.
    if re.search(r"(?:^|[_\s-])bps(?:$|[_\s-])", lower):
        return 0.01
    if re.search(r"(?:^|[_\s-])(?:billions?|b)(?:$|[_\s-])", lower):
        return 1_000_000_000
    return None


def _add_supported_variants(values: _ValueSink, value: float) -> None:
    if not math.isfinite(value):
        return
    values.add(value)
    values.add(round(value))
    values.add(round(value, 1))
    values.add(round(value, 2))
    values.add(round(value, 3))
    if abs(value) <= 1:
        pct = value * 100
        values.add(pct)
        values.add(round(pct))
        values.add(round(pct, 1))
        values.add(round(pct, 2))
    if abs(value) >= 1_000:
        values.add(round(value / 1_000, 3))
        _add_scaled_display_variants(values, value, 1_000)
    if abs(value) >= 100_000:
        _add_scaled_display_variants(values, value, 1_000_000)
    if abs(value) >= 1_000_000:
        values.add(round(value / 1_000_000, 3))
    if abs(value) >= 100_000_000:
        _add_scaled_display_variants(values, value, 1_000_000_000)
    if abs(value) >= 1_000_000_000:
        values.add(round(value / 1_000_000_000, 3))


def _add_scaled_display_variants(values: _ValueSink, value: float, scale: float) -> None:
    scaled = value / scale
    for digits in (0, 1, 2, 3):
        values.add(round(scaled, digits) * scale)


def _column_looks_dateish(column: str) -> bool:
    lower = column.lower()
    return lower.endswith("_date") or lower.endswith("_at") or "snapshot" in lower

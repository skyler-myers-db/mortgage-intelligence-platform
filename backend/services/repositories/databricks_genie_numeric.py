"""Numeric-claim checks for trusted Genie prose.

The table/chart rows are the ground truth. This module keeps the natural-
language answer from claiming unsupported borrower counts, rates, scores, or
dollar values while avoiding false positives on identifiers, dates, and query
limits.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from backend.services.genie_answers import GenieMessageResponse, GenieProof
from backend.services.repositories.databricks_genie_visualization import (
    _is_genie_identifier_column,
    _row_columns,
)

_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"(?P<currency>\$)?"
    r"(?P<num>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"\s*(?P<suffix>[kKmMbB])?"
    r"(?P<pct>%)?"
    r"(?![A-Za-z0-9_-])"
)
_NUMERIC_CELL_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")

_MEASURE_TERMS = {
    "$",
    "%",
    "amount",
    "average",
    "avg",
    "avm",
    "balance",
    "basis",
    "borrower",
    "borrowers",
    "bps",
    "count",
    "dollar",
    "dollars",
    "equity",
    "lead",
    "leads",
    "loan",
    "loans",
    "ltv",
    "mean",
    "median",
    "million",
    "percent",
    "percentage",
    "properties",
    "property",
    "rate",
    "score",
    "spread",
    "sum",
    "total",
    "value",
}

_IDENTIFIER_CONTEXT_TERMS = {
    "borrower 360",
    "borrower id",
    "borrower_id",
    "cbsa",
    "census tract",
    "customer 360",
    "date",
    "fips",
    "message id",
    "message_id",
    "msa",
    "postal code",
    "postal_code",
    "refreshed",
    "snapshot",
    "tract",
    "week",
    "year",
    "zip",
    "zipcode",
    "zip code",
}

_QUESTION_LIMIT_TERMS = {
    "first",
    "limit",
    "show",
    "top",
}


@dataclass(frozen=True)
class _NumericClaim:
    raw: str
    value: float
    span: tuple[int, int]
    is_percent: bool
    kind: Literal["currency", "percent", "bps", "number"]


def _unsupported_answer_numeric_claims(
    answer_text: str | None,
    rows: list[dict[str, Any]] | None,
    question: str,
) -> list[str]:
    """Return sanitized unsupported numeric claim labels.

    The labels are intentionally generic; callers should not echo the model's
    unsupported number back to the user because that repeats the hallucination.
    """

    if not answer_text:
        return []
    claims = [
        claim
        for claim in _numeric_claims(answer_text)
        if _is_measure_claim(answer_text, claim)
        and not _is_identifier_or_date_context(answer_text, claim)
        and not _number_is_query_limit(question, claim)
    ]
    if not claims:
        return []
    unsupported: list[str] = []
    for claim in claims:
        support = _numeric_support_values_from_rows(rows, claim=claim)
        if _is_supported(claim.value, support):
            continue
        unsupported.append("unsupported_numeric_claim")
    return unsupported[:5]


def _numeric_claim_blocked_response(
    *,
    conversation_id: str,
    message_id: str,
    elapsed_ms: int,
    question_hash: str,
    question: str,
    trusted_assets: list[str],
    reasoning_trace: list[dict[str, str]] | None,
) -> GenieMessageResponse:
    return GenieMessageResponse(
        conversation_id=conversation_id,
        message_id=message_id,
        elapsed_ms=elapsed_ms,
        question_hash=question_hash,
        question=question,
        answer=(
            "Genie returned trusted SQL, but the app could not verify one or "
            "more numeric claims against the returned rows, so the result was "
            "not displayed. Ask again with a narrower metric or inspect the "
            "source rows."
        ),
        source="policy_blocked",
        trusted_assets=trusted_assets,
        sql_query=None,
        row_count=0,
        proof=GenieProof(
            sql_query=None,
            source_assets=trusted_assets,
            row_count=0,
            trusted=False,
            reasoning_trace=reasoning_trace or [],
            known_data_gaps=[
                "Genie returned numeric prose that could not be verified against the returned rows."
            ],
            conversation_id=conversation_id,
            message_id=message_id,
            elapsed_ms=elapsed_ms,
            generated_at=datetime.now(UTC).isoformat(),
        ),
        table_rows=[],
    )


def _numeric_claims(text: str) -> list[_NumericClaim]:
    claims: list[_NumericClaim] = []
    for match in _NUMBER_RE.finditer(text):
        raw = match.group("num").replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        suffix = (match.group("suffix") or "").lower()
        if suffix == "k":
            value *= 1_000
        elif suffix == "m":
            value *= 1_000_000
        elif suffix == "b":
            value *= 1_000_000_000
        elif multiplier := _word_suffix_multiplier(text, match.end()):
            value *= multiplier
        claims.append(
            _NumericClaim(
                raw=match.group(0),
                value=value,
                span=match.span(),
                is_percent=bool(match.group("pct")),
                kind=_numeric_claim_kind(
                    text, match.span(), match.group(0), bool(match.group("pct"))
                ),
            )
        )
    return claims


def _word_suffix_multiplier(text: str, end: int) -> float | None:
    suffix = re.match(r"\s*(thousand|million|billion)\b", text[end:], re.IGNORECASE)
    if suffix is None:
        return None
    word = suffix.group(1).lower()
    if word == "thousand":
        return 1_000
    if word == "million":
        return 1_000_000
    if word == "billion":
        return 1_000_000_000
    return None


def _numeric_claim_kind(
    text: str,
    span: tuple[int, int],
    raw: str,
    is_percent: bool,
) -> Literal["currency", "percent", "bps", "number"]:
    before, after = _context(text, span, size=28)
    window = _normalized_window(before, raw, after)
    if raw.strip().startswith("$") or re.search(r"\b(?:dollars?|usd)\b", window):
        return "currency"
    if is_percent or re.search(r"\b(?:percent|percentage)\b", window):
        return "percent"
    if re.search(r"\b(?:bps|basis points?)\b", window):
        return "bps"
    if re.search(r"\b(?:balance|amount|avm|loan value|property value|equity value)\b", window):
        return "currency"
    return "number"


def _numeric_support_values_from_rows(
    rows: list[dict[str, Any]] | None,
    *,
    claim: _NumericClaim,
) -> set[float]:
    values: set[float] = set()
    if rows is None:
        return values
    if claim.kind == "number":
        values.add(float(len(rows)))
    columns = [
        col
        for col in _row_columns(rows)
        if not _is_genie_identifier_column(col)
        and not _column_looks_dateish(col)
        and _column_supports_claim_kind(col, claim.kind)
    ]
    by_column: list[list[float]] = []
    for col in columns:
        col_values: list[float] = []
        for row in rows:
            value = row.get(col)
            numeric_value = _numeric_cell_value(value)
            if numeric_value is not None:
                col_values.append(numeric_value)
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
    return values


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
    if re.search(r"(?:^|[_\s-])(?:billions?|b)(?:$|[_\s-])", lower):
        return 1_000_000_000
    return None


def _add_supported_variants(values: set[float], value: float) -> None:
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


def _add_scaled_display_variants(values: set[float], value: float, scale: float) -> None:
    scaled = value / scale
    for digits in (0, 1, 2, 3):
        values.add(round(scaled, digits) * scale)


def _is_supported(value: float, support: set[float]) -> bool:
    return any(
        math.isclose(value, candidate, rel_tol=0.001, abs_tol=0.05)
        for candidate in support
    )


def _is_measure_claim(text: str, claim: _NumericClaim) -> bool:
    before, after = _context(text, claim.span, size=48)
    window = _normalized_window(before, claim.raw, after)
    if claim.raw.strip().startswith("$") or claim.is_percent:
        return True
    if re.search(r"\b(?:k|m|b)\b", claim.raw.lower()):
        return True
    return any(re.search(rf"\b{re.escape(term)}\b", window) for term in _MEASURE_TERMS)


def _is_identifier_or_date_context(text: str, claim: _NumericClaim) -> bool:
    if claim.kind != "number" or not claim.value.is_integer():
        return False
    before, after = _context(text, claim.span, size=28)
    before_lower = before.lower()
    after_lower = after.lower()
    if int(claim.value) == 360 and re.search(r"\b(?:borrower|customer)\s*$", before_lower):
        return True
    identifier_terms = "|".join(re.escape(term) for term in _IDENTIFIER_CONTEXT_TERMS)
    if re.search(rf"(?:{identifier_terms})\s*[:#-]?\s*$", before_lower):
        return True
    if re.match(rf"^\s*[:#-]?\s*(?:{identifier_terms})\b", after_lower):
        return True
    value = int(claim.value)
    if _is_iso_date_token(text, claim.span):
        return True
    month = (
        r"(?:january|february|march|april|may|june|july|august|"
        r"september|october|november|december)"
    )
    if 1900 <= value <= 2100:
        return bool(
            re.search(
                rf"(?:\b(?:date|refreshed|snapshot|week|year|in|during|since|through|of)\s*|"
                rf"\b{month}\s+\d{{1,2}},\s*)$",
                before_lower,
            )
            or re.match(r"^\s*(?:year|snapshot|date)\b", after_lower)
        )
    if 1 <= value <= 31:
        return bool(
            re.search(rf"\b{month}\s*$", before_lower)
            or re.match(rf"^\s*,?\s*(?:{month}|\d{{4}}\b)", after_lower)
        )
    return False


def _is_iso_date_token(text: str, span: tuple[int, int]) -> bool:
    for match in re.finditer(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b", text):
        if match.start() <= span[0] and span[1] <= match.end():
            return True
    return False


def _number_is_query_limit(question: str, claim: _NumericClaim) -> bool:
    question_claims = _numeric_claims(question)
    if not any(_is_supported(claim.value, {q.value}) for q in question_claims):
        return False
    q = question.lower()
    for q_claim in question_claims:
        if not _is_supported(claim.value, {q_claim.value}):
            continue
        before, after = _context(q, q_claim.span, size=24)
        window = _normalized_window(before, q_claim.raw, after)
        if any(re.search(rf"\b{re.escape(term)}\b", window) for term in _QUESTION_LIMIT_TERMS):
            return True
    return False


def _normalized_window(before: str, raw: str, after: str) -> str:
    return re.sub(r"\s+", " ", f"{before} {raw} {after}".lower())


def _column_looks_dateish(column: str) -> bool:
    lower = column.lower()
    return lower.endswith("_date") or lower.endswith("_at") or "snapshot" in lower


def _context(text: str, span: tuple[int, int], *, size: int) -> tuple[str, str]:
    start, end = span
    return text[max(0, start - size):start], text[end:end + size]

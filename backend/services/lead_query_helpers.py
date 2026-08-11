"""Validation and normalization helpers for Lead Queue query parameters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

from fastapi import HTTPException

from backend.schemas.common import validate_public_borrower_id
from backend.schemas.genie_numeric_filters import GENIE_NUMERIC_FILTER_BOUNDS
from backend.schemas.lead import SEGMENT_CODE_VALUES
from backend.schemas.portfolio import PortfolioCriteria
from backend.services.repositories.databricks_portfolio import PORTFOLIO_EQUITY_THRESHOLDS

_ALLOWED_SEGMENT_CODES: frozenset[str] = frozenset(SEGMENT_CODE_VALUES)
# Reviewed numeric floors a governed Genie cohort can carry, and the inclusive
# (minimum, maximum) each one is validated against. Same object as
# ``genie_actions._REPLAYABLE_NUMERIC_FILTERS``: the cohort row is written by
# that module and read back here, so the two ranges cannot disagree.
COHORT_NUMERIC_FILTER_BOUNDS: Mapping[str, tuple[int, int]] = GENIE_NUMERIC_FILTER_BOUNDS


def parse_csv_filter(
    raw: str | None,
    *,
    width: int,
    label: str,
    numeric: bool = False,
) -> list[str] | None:
    if raw is None:
        return None
    values = [part.strip().upper() for part in raw.split(",") if part.strip()]
    if not values:
        return None
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        valid_chars = value.isdigit() if numeric else value.isalpha()
        if len(value) != width or not valid_chars:
            raise HTTPException(
                status_code=422,
                detail=f"{label} must be comma-separated {width}-character values",
            )
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def parse_segment_codes(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    out: list[str] = []
    for part in raw.split(","):
        code = part.strip().lower()
        if not code:
            continue
        if code not in _ALLOWED_SEGMENT_CODES:
            raise HTTPException(status_code=422, detail="segment_codes contains an unknown segment")
        if code not in out:
            out.append(code)
    return out or None


def parse_segment_mode(raw: str) -> Literal["any", "all"]:
    mode = raw.strip().lower()
    if mode not in {"any", "all"}:
        raise HTTPException(status_code=422, detail="segment_mode must be any or all")
    return cast(Literal["any", "all"], mode)


def parse_borrower_ids(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    out: list[str] = []
    for value in raw.split(","):
        borrower_id = value.strip()
        if not borrower_id:
            continue
        try:
            borrower_id = validate_public_borrower_id(borrower_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="borrower_ids must be comma-separated synthetic B-* ids",
            ) from exc
        if borrower_id not in out:
            out.append(borrower_id)
    return out or None


def cohort_list(
    filters: dict[str, object],
    key: str,
    *,
    width: int | None = None,
    numeric: bool = False,
    borrower_ids: bool = False,
) -> list[str] | None:
    raw = filters.get(key)
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise HTTPException(status_code=422, detail=f"cohort {key} filter is invalid")
    out: list[str] = []
    for value in raw:
        text = str(value).strip()
        if not text:
            continue
        if borrower_ids:
            try:
                normalized = validate_public_borrower_id(text)
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail="cohort borrower_ids filter is invalid",
                ) from exc
        else:
            normalized = text.upper()
            if width is not None:
                valid_chars = normalized.isdigit() if numeric else normalized.isalpha()
                if len(normalized) != width or not valid_chars:
                    raise HTTPException(
                        status_code=422,
                        detail=f"cohort {key} filter is invalid",
                    )
        if normalized not in out:
            out.append(normalized)
    return out or None


def cohort_numeric_floor(filters: dict[str, object], key: str) -> int | None:
    """Return a reviewed integer floor stored on a governed Genie cohort.

    Closed vocabulary: the key must be one this module knows how to apply, and
    the stored value must be a whole number inside the reviewed range. A
    cohort row that fails either check is rejected rather than replayed
    without the predicate -- replaying it broader is the exact defect this
    closes (live 2026-08-11: a score-narrowed answer of 32 replayed as 1,766).

    ``min_rate_spread_bps`` is the one signed floor: the column is negative on
    2,561,392 of 5,156,184 gold rows, so a stored ``-25`` is a real cohort the
    queue must reproduce, not a corrupt row.
    """

    bounds = COHORT_NUMERIC_FILTER_BOUNDS.get(key)
    if bounds is None:
        raise HTTPException(status_code=422, detail=f"cohort {key} filter is invalid")
    minimum, maximum = bounds
    raw = filters.get(key)
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool) or (isinstance(raw, float) and not raw.is_integer()):
        raise HTTPException(status_code=422, detail=f"cohort {key} filter is invalid")
    # `filters` is JSON decoded from Lakebase, so `raw` is statically `object`.
    # Narrow before converting rather than leaning on int()'s runtime TypeError:
    # the stored value is untrusted, and a type the gate never considered
    # should be refused explicitly, not coerced by accident.
    if not isinstance(raw, int | float | str):
        raise HTTPException(status_code=422, detail=f"cohort {key} filter is invalid")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"cohort {key} filter is invalid") from exc
    if value < minimum or value > maximum:
        raise HTTPException(status_code=422, detail=f"cohort {key} filter is invalid")
    return value


def apply_cohort_equity_floor(
    criteria: PortfolioCriteria,
    floor: int | None,
) -> PortfolioCriteria:
    """Fold a cohort's equity floor into the reviewed Portfolio vocabulary.

    ``min_equity_pct`` already compiles to ``equity_pct >= :equity_floor`` in
    ``build_preview_predicates``, so the cohort reuses that path instead of
    growing a second equity predicate. The strictest floor wins: a cohort must
    never come back broader than either the answer's threshold or the
    portfolio criteria it was built from.
    """

    if floor is None:
        return criteria
    label_floor = PORTFOLIO_EQUITY_THRESHOLDS.get(criteria.min_equity_pct_label or "", 0)
    effective = max(float(floor), float(criteria.min_equity_pct or 0), float(label_floor))
    return criteria.model_copy(update={"min_equity_pct": effective})


def cohort_segment_mode(filters: dict[str, object]) -> str:
    raw = filters.get("segment_mode")
    if raw is None:
        return "any"
    if not isinstance(raw, str):
        raise HTTPException(status_code=422, detail="cohort segment_mode filter is invalid")
    normalized = raw.strip().lower()
    if normalized not in {"any", "all"}:
        raise HTTPException(status_code=422, detail="cohort segment_mode filter is invalid")
    return normalized


def portfolio_criteria_from_query(
    *,
    geography: str | None,
    occupancy: str | None,
    lien_status: str | None,
    lender_relationship: str | None,
    product: str | None,
    target_lender_ref: str | None,
    loan_product: str | None = None,
    origination_channel: str | None = None,
    min_equity_pct_label: str | None,
    min_equity_pct: float | None,
    owner_link: str | None = None,
    purchase_intent: str | None = None,
    marketing_eligibility: str | None = None,
    consent_status: str | None = None,
    recency: str | None = None,
) -> PortfolioCriteria | None:
    fields: dict[str, object] = {}
    for key, value in (
        ("geography", geography),
        ("occupancy", occupancy),
        ("lien_status", lien_status),
        ("lender_relationship", lender_relationship),
        ("product", product),
        ("loan_product", loan_product),
        ("origination_channel", origination_channel),
        ("target_lender_ref", target_lender_ref),
        ("min_equity_pct_label", min_equity_pct_label),
        ("owner_link", owner_link),
        ("purchase_intent", purchase_intent),
        ("marketing_eligibility", marketing_eligibility),
        ("consent_status", consent_status),
        ("recency", recency),
    ):
        if value:
            fields[key] = value
    if min_equity_pct is not None:
        fields["min_equity_pct"] = min_equity_pct
    return PortfolioCriteria.model_validate(fields) if fields else None


def requires_marketing_override_admin(
    *,
    marketing_eligibility: str | None,
    consent_status: str | None,
    include_suppressed_for_analytics: bool = False,
) -> bool:
    """Return true when a lead-list request may expose suppressed rows."""

    def normalize(value: str | None) -> str:
        return (value or "").strip().lower().replace("_", " ").replace("-", " ")

    if include_suppressed_for_analytics:
        return True
    normalized_marketing = normalize(marketing_eligibility or "Eligible only")
    normalized_consent = normalize(consent_status)
    if normalized_marketing and normalized_marketing not in {"eligible only", "eligible"}:
        return True
    return normalized_consent in {"opt out", "unknown"}

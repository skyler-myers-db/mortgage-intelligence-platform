"""Validation and normalization helpers for Lead Queue query parameters."""

from __future__ import annotations

from typing import Literal, cast

from fastapi import HTTPException

from backend.schemas.common import validate_public_borrower_id
from backend.schemas.lead import SEGMENT_CODE_VALUES
from backend.schemas.portfolio import PortfolioCriteria

_ALLOWED_SEGMENT_CODES: frozenset[str] = frozenset(SEGMENT_CODE_VALUES)


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

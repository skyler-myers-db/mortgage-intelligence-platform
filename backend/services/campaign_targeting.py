"""Replay a reviewed saved-campaign cohort for one public borrower id."""

from __future__ import annotations

from backend.schemas.portfolio import PortfolioCriteria
from backend.services.repositories import LeadRepository


def campaign_contains_borrower(
    repo: LeadRepository,
    *,
    borrower_id: str,
    criteria: dict[str, object],
) -> bool:
    """Return whether ``borrower_id`` remains inside the exact saved cohort.

    Campaign criteria have two governed shapes: Portfolio Builder criteria and
    Genie proof criteria with nested ``result_filters``. Both are projected by
    ``project_public_campaign_json_field`` before reaching this function.
    """

    filters: dict[str, object]
    is_genie_criteria = criteria.get("source") in {"genie", "trusted_sql"}
    top_level_borrower_ids: list[str] = []
    if is_genie_criteria:
        nested = criteria.get("result_filters")
        filters = dict(nested) if isinstance(nested, dict) else {}
        raw_top_level = criteria.get("borrower_ids")
        if isinstance(raw_top_level, list):
            top_level_borrower_ids = [str(value) for value in raw_top_level]
    else:
        filters = dict(criteria)

    raw_filter_borrower_ids = filters.get("borrower_ids")
    filter_borrower_ids = (
        [str(value) for value in raw_filter_borrower_ids]
        if isinstance(raw_filter_borrower_ids, list)
        else []
    )
    if top_level_borrower_ids and borrower_id not in top_level_borrower_ids:
        return False
    if filter_borrower_ids and borrower_id not in filter_borrower_ids:
        return False

    portfolio_raw = filters.get("portfolio_criteria")
    if not is_genie_criteria:
        portfolio_raw = filters
    portfolio_criteria = PortfolioCriteria.model_validate(
        portfolio_raw if isinstance(portfolio_raw, dict) else {}
    )

    def _strings(value: object) -> list[str] | None:
        if not isinstance(value, list):
            return None
        normalized = [str(item).strip() for item in value if str(item).strip()]
        return normalized or None

    segment_codes = _strings(filters.get("segment_codes"))
    segment_mode = str(filters.get("segment_mode") or "any").strip().lower()
    if segment_mode not in {"any", "all"}:
        raise ValueError("campaign segment mode is invalid")
    counties = _strings(filters.get("counties"))
    county = str(filters.get("county") or "").strip() or None
    target_lender_ref = str(filters.get("target_lender_ref") or "").strip() or None

    count = repo.count(
        segment=None,
        portfolio_id=None,
        borrower_ids=[borrower_id],
        state_codes=_strings(filters.get("states")) if is_genie_criteria else None,
        zip_codes=_strings(filters.get("zips")) if is_genie_criteria else None,
        county_fips=county,
        county_fipses=counties,
        segment_codes=segment_codes,
        segment_mode=segment_mode,
        target_lender_ref=target_lender_ref,
        portfolio_criteria=portfolio_criteria,
    )
    return count == 1

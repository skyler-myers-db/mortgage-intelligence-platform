"""Replay a governed Genie cohort as Lead Queue filters.

When a Genie answer produces a cohort, the confirmed filters are
persisted to ``mip_app.genie_cohorts`` and the user is handed a
``cohort_id``. The Lead Queue then replays *that* row rather than the
URL: query params are useful for shareable links and visual chips, but
they must not widen a confirmed cohort if the URL is edited by hand.

Two things leave this module together on purpose:

* the replayable filters, which are what the queue actually queries; and
* the count the Genie answer stated, which is what the user just read on
  screen. The queue replays only the reviewed geography/segment subset,
  so an answer narrowed by any numeric threshold replays broader --
  measured live 2026-08-10 at 55x (32 borrowers -> 1,766). Carrying the
  stated count here is what lets the response say so instead of quietly
  showing a different population under the same question.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from backend.schemas.portfolio import PortfolioCriteria
from backend.services.lakebase import LakebaseError, get_lakebase_client
from backend.services.lead_query_helpers import (
    cohort_list,
    cohort_segment_mode,
    parse_segment_codes,
)

_COHORT_FILTER_SELECT_SQL = """
SELECT route_filters, row_count
FROM mip_app.genie_cohorts
WHERE cohort_id = %(cohort_id)s
  AND actor_email = %(actor_email)s
LIMIT 1
"""


@dataclass(frozen=True)
class CohortReplay:
    """The persisted cohort, normalized into Lead Queue filter values."""

    filters: dict[str, object]
    stated_count: int | None
    segment_mode: str
    state_codes: list[str] | None
    zip_codes: list[str] | None
    county_fipses: list[str] | None
    borrower_ids: list[str] | None
    segment_codes: list[str] | None
    county_fips: str | None
    target_lender_ref: str | None
    unreplayable_filters: list[str]


def load_cohort_filters(cohort_id: str, *, actor: str) -> tuple[dict[str, object], int | None]:
    """Return the replayable filters and the count the Genie answer stated."""

    try:
        row = get_lakebase_client().fetchone(
            _COHORT_FILTER_SELECT_SQL,
            {"cohort_id": cohort_id, "actor_email": actor},
        )
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail="Lakebase temporarily unavailable") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="cohort not found")
    filters_raw = row.get("route_filters") or {}
    if isinstance(filters_raw, str):
        try:
            filters_raw = json.loads(filters_raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="cohort route filters are invalid") from exc
    if not isinstance(filters_raw, dict):
        raise HTTPException(status_code=422, detail="cohort route filters are invalid")
    stated_raw = row.get("row_count")
    stated_count: int | None
    try:
        stated_count = int(stated_raw) if stated_raw is not None else None
    except (TypeError, ValueError):
        stated_count = None
    return filters_raw, stated_count


def resolve_cohort_replay(cohort_id: str, *, actor: str) -> CohortReplay:
    """Load the persisted cohort and normalize every filter it can replay.

    Raises 404 when the cohort is not the actor's, and 422 when any
    persisted filter fails the same validation the URL path applies --
    a stored cohort gets no more trust than a hand-typed one.
    """

    filters, stated_count = load_cohort_filters(cohort_id, actor=actor)

    # Validation order is load-bearing: a cohort with more than one bad
    # filter must report the same 422 detail the inline router did.
    segment_mode = cohort_segment_mode(filters)
    state_codes = cohort_list(filters, "states", width=2)
    zip_codes = cohort_list(filters, "zips", width=5, numeric=True)
    county_fipses = cohort_list(filters, "counties", width=5, numeric=True)
    borrower_ids = cohort_list(filters, "borrower_ids", borrower_ids=True)

    county_fips: str | None = None
    cohort_county = str(filters.get("county") or "").strip()
    if cohort_county:
        if not cohort_county.isdigit() or len(cohort_county) != 5:
            raise HTTPException(status_code=422, detail="cohort county filter is invalid")
        county_fips = cohort_county

    segment_codes: list[str] | None = None
    cohort_segments = cohort_list(filters, "segment_codes")
    if cohort_segments is not None:
        segment_codes = parse_segment_codes(",".join(cohort_segments))

    unreplayable: list[str] = []
    unreplayable_raw = filters.get("unreplayable_filters")
    if isinstance(unreplayable_raw, list):
        unreplayable = [str(key) for key in unreplayable_raw if str(key).strip()]

    return CohortReplay(
        filters=filters,
        stated_count=stated_count,
        segment_mode=segment_mode,
        state_codes=state_codes,
        zip_codes=zip_codes,
        county_fipses=county_fipses,
        borrower_ids=borrower_ids,
        segment_codes=segment_codes,
        county_fips=county_fips,
        target_lender_ref=str(filters.get("target_lender_ref") or "").strip() or None,
        unreplayable_filters=unreplayable,
    )


def cohort_portfolio_criteria(
    filters: dict[str, object],
    *,
    has_replay_filter: bool,
) -> tuple[PortfolioCriteria, bool]:
    """Return the cohort's portfolio criteria and whether anything is replayable.

    The persisted criteria are re-validated and then re-gated: the
    contactability filter is forced back to ``Eligible only`` and any
    stored consent override is dropped, so a cohort can never replay into
    a wider marketing population than the queue's own fail-closed default.
    """

    portfolio_raw = filters.get("portfolio_criteria")
    if isinstance(portfolio_raw, dict):
        fields: dict[str, Any] = dict(portfolio_raw)
        try:
            original_criteria = PortfolioCriteria(**fields)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="cohort portfolio criteria are invalid",
            ) from exc
        if not original_criteria.has_effective_predicate(count_default_marketing=False):
            raise HTTPException(
                status_code=422,
                detail="cohort portfolio criteria are invalid",
            )
        fields["marketing_eligibility"] = "Eligible only"
        fields.pop("consent_status", None)
        return PortfolioCriteria(**fields), True
    if portfolio_raw is not None:
        raise HTTPException(
            status_code=422,
            detail="cohort portfolio criteria are invalid",
        )
    return PortfolioCriteria(marketing_eligibility="Eligible only"), has_replay_filter

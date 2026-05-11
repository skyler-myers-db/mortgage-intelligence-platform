"""Geography rollup endpoints.

/api/geo/state-rollups — per-state addressable / in-the-money /
top-tier / avg_score / top_segment_code driven by the latest
``mip.gold.funnel_snapshot_daily`` snapshot joined to
``mip.gold.state_top_segment``. This is the source of truth for
USChoroplethMap state-level data.

/api/geo/county-rollups?state=XX — per-county aggregates within the
given state, backed by ``mip.gold.county_rollup``. The UI lazy-fetches
when the user drills into a state; counties not present in the payload
render "—" (honest null).

/api/geo/zip-rollups?county_fips=NNNNN — per-ZIP aggregates within a county
(5-char FIPS), backed by ``mip.gold.zip_rollup``. Each row carries a
stable ``sample_borrower_id`` so the UI's ZIP-tile click-through deep-
links to a real borrower dossier.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.schemas.geo import (
    CountyRollupResponse,
    StateRollupResponse,
    ZipRollupResponse,
)
from backend.schemas.portfolio import PortfolioCriteria
from backend.services.repositories import GeoRepository, get_geo_repository

router = APIRouter(prefix="/api/geo", tags=["geo"])

RepoDep = Annotated[GeoRepository, Depends(get_geo_repository)]
_ALLOWED_SEGMENT_CODES: frozenset[str] = frozenset(
    {"itm", "listed", "permit", "investor", "equity", "retention"}
)


def _parse_segment_codes(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    parsed: list[str] = []
    seen: set[str] = set()
    for value in raw.split(","):
        code = value.strip().lower()
        if not code or code in seen:
            continue
        if code not in _ALLOWED_SEGMENT_CODES:
            raise HTTPException(status_code=422, detail="segment_codes contains an unknown segment")
        seen.add(code)
        parsed.append(code)
    return parsed or None


def _portfolio_criteria_from_geo_query(
    *,
    occupancy: str | None = None,
    lien_status: str | None = None,
    owner_link: str | None = None,
    purchase_intent: str | None = None,
    min_equity_pct_label: str | None = None,
    min_equity_pct: float | None = None,
) -> PortfolioCriteria | None:
    fields: dict[str, object] = {}
    if occupancy:
        fields["occupancy"] = occupancy
    if lien_status:
        fields["lien_status"] = lien_status
    if owner_link:
        fields["owner_link"] = owner_link
    if purchase_intent:
        fields["purchase_intent"] = purchase_intent
    if min_equity_pct_label:
        fields["min_equity_pct_label"] = min_equity_pct_label
    if min_equity_pct is not None:
        fields["min_equity_pct"] = min_equity_pct
    if not fields:
        return None
    try:
        return PortfolioCriteria(**fields)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/state-rollups", response_model=StateRollupResponse)
def state_rollups(
    repo: RepoDep,
    segment_codes: Annotated[
        str | None,
        Query(
            alias="segment_codes",
            description=(
                "Optional comma-separated SegmentCode list (itm,listed,permit,"
                "investor,equity,retention). When provided, per-state counts "
                "reflect ONLY borrowers matching the filter. Use segment_mode="
                "all when selected cards should narrow via intersection."
            ),
        ),
    ] = None,
    segment_mode: Annotated[
        str,
        Query(
            pattern="^(any|all)$",
            description=(
                "any = segment arrays overlap; all = borrower contains every "
                "selected segment code."
            ),
        ),
    ] = "any",
    occupancy: Annotated[str | None, Query(alias="occupancy", max_length=64)] = None,
    lien_status: Annotated[str | None, Query(alias="lien_status", max_length=64)] = None,
    owner_link: Annotated[str | None, Query(alias="owner_link", max_length=64)] = None,
    purchase_intent: Annotated[str | None, Query(alias="purchase_intent", max_length=64)] = None,
    min_equity_pct_label: Annotated[str | None, Query(alias="min_equity_pct_label", max_length=32)] = None,
    min_equity_pct: Annotated[float | None, Query(alias="min_equity_pct", ge=0, le=100)] = None,
) -> StateRollupResponse:
    """Return per-state rollups for the latest funnel snapshot.

    60s TTL cache lives in the repository; the router is a thin pass-
    through so adding a per-portfolio filter later is a repo change,
    not a router change.

    2026-05-04 (FIX G): when ``segment_codes`` is supplied, the
    repository switches to a live segment-filtered query against
    ``mip.gold.borrower_360`` so the choropleth tooltip can show the
    borrower count for the active segment filter instead of the
    cross-segment total.
    """
    parsed = _parse_segment_codes(segment_codes)
    return repo.state_rollups(
        segment_codes=parsed,
        segment_mode=segment_mode,
        portfolio_criteria=_portfolio_criteria_from_geo_query(
            occupancy=occupancy,
            lien_status=lien_status,
            owner_link=owner_link,
            purchase_intent=purchase_intent,
            min_equity_pct_label=min_equity_pct_label,
            min_equity_pct=min_equity_pct,
        ),
    )


@router.get("/county-rollups", response_model=CountyRollupResponse)
def county_rollups(
    repo: RepoDep,
    state: Annotated[
        str,
        Query(
            min_length=2,
            max_length=2,
            pattern=r"^[A-Za-z]{2}$",
            description="2-char USPS state code (uppercased server-side).",
        ),
    ],
    segment_codes: Annotated[
        str | None,
        Query(
            alias="segment_codes",
            description=(
                "Optional comma-separated SegmentCode list. When provided, "
                "per-county counts reflect only borrowers matching the same "
                "segment filter as /state-rollups."
            ),
        ),
    ] = None,
    segment_mode: Annotated[
        str,
        Query(
            pattern="^(any|all)$",
            description=(
                "any = segment arrays overlap; all = borrower contains every "
                "selected segment code."
            ),
        ),
    ] = "any",
    occupancy: Annotated[str | None, Query(alias="occupancy", max_length=64)] = None,
    lien_status: Annotated[str | None, Query(alias="lien_status", max_length=64)] = None,
    owner_link: Annotated[str | None, Query(alias="owner_link", max_length=64)] = None,
    purchase_intent: Annotated[str | None, Query(alias="purchase_intent", max_length=64)] = None,
    min_equity_pct_label: Annotated[str | None, Query(alias="min_equity_pct_label", max_length=32)] = None,
    min_equity_pct: Annotated[float | None, Query(alias="min_equity_pct", ge=0, le=100)] = None,
) -> CountyRollupResponse:
    """Return per-county rollups for the given state at the latest snapshot.

    State codes outside the current tenant footprint, or states with no
    Cotality-backed county coverage in the current gold refresh, legitimately
    return an empty ``rollups`` list. The map UI renders "—" for any county
    not in the payload, which is the correct posture for a geography without
    gold-layer coverage.
    """
    return repo.county_rollups(
        state,
        segment_codes=_parse_segment_codes(segment_codes),
        segment_mode=segment_mode,
        portfolio_criteria=_portfolio_criteria_from_geo_query(
            occupancy=occupancy,
            lien_status=lien_status,
            owner_link=owner_link,
            purchase_intent=purchase_intent,
            min_equity_pct_label=min_equity_pct_label,
            min_equity_pct=min_equity_pct,
        ),
    )


@router.get("/zip-rollups", response_model=ZipRollupResponse)
def zip_rollups(
    repo: RepoDep,
    county_fips: Annotated[
        str,
        Query(
            alias="county_fips",
            min_length=5,
            max_length=5,
            pattern=r"^\d{5}$",
            description="5-char county FIPS (2-char state + 3-char county).",
        ),
    ],
    segment_codes: Annotated[
        str | None,
        Query(
            alias="segment_codes",
            description=(
                "Optional comma-separated SegmentCode list. When provided, "
                "per-ZIP counts reflect only borrowers matching the same "
                "segment filter as /state-rollups."
            ),
        ),
    ] = None,
    segment_mode: Annotated[
        str,
        Query(
            pattern="^(any|all)$",
            description=(
                "any = segment arrays overlap; all = borrower contains every "
                "selected segment code."
            ),
        ),
    ] = "any",
    occupancy: Annotated[str | None, Query(alias="occupancy", max_length=64)] = None,
    lien_status: Annotated[str | None, Query(alias="lien_status", max_length=64)] = None,
    owner_link: Annotated[str | None, Query(alias="owner_link", max_length=64)] = None,
    purchase_intent: Annotated[str | None, Query(alias="purchase_intent", max_length=64)] = None,
    min_equity_pct_label: Annotated[str | None, Query(alias="min_equity_pct_label", max_length=32)] = None,
    min_equity_pct: Annotated[float | None, Query(alias="min_equity_pct", ge=0, le=100)] = None,
) -> ZipRollupResponse:
    """Return per-ZIP rollups for the given county FIPS.

    Each ZIP carries a stable-ranked ``sample_borrower_id`` so the UI's
    ZIP-tile click-through can deep-link to ``/borrower-360/<id>``.
    Empty list when the county has no rollup rows (county outside the current
    Cotality-backed coverage or CTAS hasn't run).
    """
    return repo.zip_rollups(
        county_fips,
        segment_codes=_parse_segment_codes(segment_codes),
        segment_mode=segment_mode,
        portfolio_criteria=_portfolio_criteria_from_geo_query(
            occupancy=occupancy,
            lien_status=lien_status,
            owner_link=owner_link,
            purchase_intent=purchase_intent,
            min_equity_pct_label=min_equity_pct_label,
            min_equity_pct=min_equity_pct,
        ),
    )

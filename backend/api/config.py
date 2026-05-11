from fastapi import APIRouter

from backend.config.settings import settings
from backend.services.databricks_sql_helpers import qualify
from backend.services.geography_scope import GeographyScope, load_geography_scope
from backend.services.pii_redaction import normalize_public_lender_ref
from backend.services.state_footprint import get_state_footprint_resolver

router = APIRouter(prefix="/api/config", tags=["config"])


def _target_lender_options() -> tuple[list[str], str]:
    try:
        from backend.services.databricks_sql import get_sql_client

        rows = get_sql_client().execute(
            "SELECT current_lender_ref, COUNT(*) AS borrowers "
            f"FROM {qualify('gold', 'borrower_360')} "
            "WHERE current_lender_ref IS NOT NULL "
            "GROUP BY current_lender_ref "
            "ORDER BY CASE WHEN current_lender_ref = 'Summit Mortgage' THEN 0 ELSE 1 END, "
            "         borrowers DESC, current_lender_ref ASC "
            "LIMIT 25"
        )
    except Exception:
        return ["All"], "unavailable"
    values: list[str] = []
    for row in rows:
        try:
            value = normalize_public_lender_ref(str(row.get("current_lender_ref") or ""))
        except ValueError:
            continue
        if value and value not in values:
            values.append(value)
    return ["All", *values], "live" if values else "configured_empty"


def _live_geography_scope() -> GeographyScope | None:
    try:
        from backend.services.databricks_sql import get_sql_client

        return load_geography_scope(get_sql_client())
    except Exception:
        return None


def _geography_options(scope: GeographyScope | None) -> tuple[list[str], str]:
    resolver = get_state_footprint_resolver()
    if resolver.using_fallback():
        return ["All"], "metadata_only"
    states = resolver.list()
    if not states:
        return ["All"], "unavailable"
    options = [f"All {len(states)} states", *(row.state_name for row in states)]
    if scope and scope.counties:
        return options, "live"
    return options, "footprint_fallback"


@router.get("/options")
def get_config_options() -> dict[str, object]:
    target_lenders, target_lenders_status = _target_lender_options()
    geo_scope = _live_geography_scope()
    geographies, geographies_status = _geography_options(geo_scope)
    return {
        "lender_name": settings.mip_lender_name,
        "geographies": geographies,
        "geographies_status": geographies_status,
        "geography_scope": geo_scope.to_api_dict() if geo_scope else None,
        "occupancy": ["Owner-occupied", "Non-owner-occupied", "All"],
        "lien_status": ["Open 1st lien", "Open HELOC", "Free & clear", "Any"],
        "lender_relationships": ["All", "Current customer", "Former customer", "Competitor customer"],
        "products": ["All products", "Refi", "HELOC", "Cash-out", "Purchase", "Retention"],
        "equity_thresholds": ["≥ 15%", "≥ 25%", "≥ 40%", "Any"],
        "target_lender_refs": target_lenders,
        "target_lender_refs_status": target_lenders_status,
    }


@router.get("/footprint")
def get_config_footprint() -> dict[str, object]:
    """Return refreshed geography coverage.

    The live gold geography rollups are the single source of truth for which
    US states have data-bearing Cotality coverage today. Frontend hydrates
    the county-drill allowlist in `USChoroplethMap.tsx` and the
    `LOCATION_TO_STATES` map in `routes/segment-intelligence.tsx` from this
    response; portfolio-builder's dynamic "All N states" dropdown option is
    built from the same list.

    State display metadata can be enriched by `mip.ref.state_footprint`, but
    that table is not data scope. If live gold coverage is unavailable, the
    resolver returns metadata with ``using_fallback=true``. Data-bearing
    routes must treat that as unavailable coverage proof.

    ``geography_scope`` is discovered from ``mip.gold.county_rollup`` and
    ``mip.gold.zip_rollup``. It lets the frontend disclose the actual county
    and ZIP coverage present today without hardcoding demo-specific counts.

    Hole-finder round-2 #20.
    """
    resolver = get_state_footprint_resolver()
    rows = resolver.list()
    geo_scope = _live_geography_scope()
    return {
        "states": [
            {
                "state_code": r.state_code,
                "state_name": r.state_name,
                "display_order": r.display_order,
                "is_default_state": r.is_default_state,
            }
            for r in rows
        ],
        "geography_scope": geo_scope.to_api_dict() if geo_scope else None,
        "using_fallback": resolver.using_fallback(),
    }

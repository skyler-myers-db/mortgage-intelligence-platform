from fastapi import APIRouter

from backend.config.settings import settings
from backend.services.state_footprint import get_state_footprint_resolver

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("/options")
def get_config_options() -> dict[str, object]:
    return {
        "lender_name": settings.mip_lender_name,
        "geographies": [
            # Slice 9: anchor to the 6-state Delta Share footprint.
            "Illinois / Cook County / 60611",
            "California / Los Angeles County / 90038",
            "Texas / Travis County / 78704",
            "Washington / King County / 98103",
            "Florida / Miami-Dade County / 33132",
            "Colorado / Denver County / 80202",
        ],
        "occupancy": ["Owner-occupied", "Second home", "Investor"],
        "lien_status": ["Open first lien", "Free and clear", "Multiple liens"],
        "lender_relationships": ["All", "Current customer", "Former customer", "Competitor"],
        "products": ["Refi", "HELOC", "Cash-out", "Purchase", "Retention"],
        "equity_thresholds": ["5%", "10%", "15%", "20%", "30%"],
    }


@router.get("/footprint")
def get_config_footprint() -> dict[str, object]:
    """Return the tenant's operational footprint.

    The footprint is the single source of truth for which US states the
    lender writes business in. Frontend hydrates `SUPPORTED_COUNTY_STATES`
    (county drill allowlist in `USChoroplethMap.tsx`) and the
    `LOCATION_TO_STATES` map in `routes/segment-intelligence.tsx` from this
    response; portfolio-builder's "All N states" dropdown option is built
    from the same list.

    Sourced from `mip.ref.state_footprint` via
    `backend.services.state_footprint.StateFootprintResolver`
    (TTL-cached 300s). Falls back to the canonical 6-state Summit
    footprint when UC is unavailable so the app never ships empty.

    Hole-finder round-2 #20.
    """
    resolver = get_state_footprint_resolver()
    rows = resolver.list()
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
        "default_state_code": resolver.default_state_code(),
    }

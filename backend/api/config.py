from fastapi import APIRouter

from backend.config.settings import settings

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("/options")
def get_config_options() -> dict[str, object]:
    return {
        "demo_lender": settings.mip_demo_lender,
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

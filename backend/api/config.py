from fastapi import APIRouter

from backend.config.settings import settings

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("/options")
def get_config_options() -> dict[str, object]:
    return {
        "demo_lender": settings.mip_demo_lender,
        "mock_mode": settings.mip_mock_mode,
        "geographies": [
            "Georgia / Atlanta MSA / 30309",
            "California / Orange County / 92602",
            "Texas / Travis County / 78704",
        ],
        "occupancy": ["Owner-occupied", "Second home", "Investor"],
        "lien_status": ["Open first lien", "Free and clear", "Multiple liens"],
        "lender_relationships": ["All", "Current customer", "Former customer", "Competitor"],
        "products": ["Refi", "HELOC", "Cash-out", "Purchase", "Retention"],
        "equity_thresholds": ["5%", "10%", "15%", "20%", "30%"],
    }

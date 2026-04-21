from fastapi import APIRouter

from backend.config.settings import settings

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "mode": "mock" if settings.mip_mock_mode else "live",
        "app_env": settings.app_env,
    }

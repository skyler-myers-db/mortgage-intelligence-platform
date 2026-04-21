from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.api import (
    admin,
    audit,
    borrowers,
    config,
    genie,
    health,
    leads,
    offers,
    outreach,
    portfolio,
    segments,
)

app = FastAPI(title="Mortgage Intelligence Platform API")

for router in [
    health.router,
    config.router,
    admin.router,
    portfolio.router,
    segments.router,
    leads.router,
    borrowers.router,
    offers.router,
    outreach.router,
    genie.router,
    audit.router,
]:
    app.include_router(router)


# Serve the built Vite SPA. Frontend bundle lands in `frontend/dist/` after
# `npm run build`. On Databricks Apps this happens during deployment; locally
# it happens when the user runs `make build` or `npm --prefix frontend run
# build`. If dist/ is absent (e.g. running the API standalone during dev),
# skip the mount and let the router-only API serve /api/* normally.
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if _FRONTEND_DIST.is_dir() and (_FRONTEND_DIST / "index.html").is_file():
    # Mount the hashed assets under /assets.
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

    # Any other non-/api path returns index.html so React Router handles the
    # client-side route. FastAPI evaluates routes in registration order and
    # the `/api` routers are already in, so this catch-all doesn't collide.
    @app.get("/{full_path:path}")
    def _spa_fallback(full_path: str) -> FileResponse:
        _ = full_path  # router acts as SPA catch-all
        return FileResponse(_FRONTEND_DIST / "index.html")

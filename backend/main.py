from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
from backend.config.settings import _running_under_pytest, settings


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Validate warehouse credentials before serving traffic.

    Slice-4 invariant: the app runs on real Unity Catalog data in
    every environment. A startup that succeeds without DATABRICKS_*
    credentials would be a latent misconfiguration -- the first /api
    request would 500 on lazy factory init. We fail loudly here
    instead. Pytest runs bypass the check because dependency_overrides
    inject stubs and nothing under tests/ needs live creds.
    """
    if not _running_under_pytest():
        # ``require_databricks_creds`` raises RuntimeError with a
        # clear operator-facing message when any of the three env
        # vars is missing. The exception propagates through FastAPI
        # lifespan and terminates the uvicorn process.
        settings.require_databricks_creds()
    yield


app = FastAPI(title="Mortgage Intelligence Platform API", lifespan=_lifespan)

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

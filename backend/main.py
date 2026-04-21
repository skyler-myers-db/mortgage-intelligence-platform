import logging
import time
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

log = logging.getLogger("mip-runtime")


def _warm_warehouse() -> None:
    """Issue one ``SELECT 1`` against the SQL warehouse to warm it.

    Slice-6 resilience hook: the serverless warehouse auto-stops after
    15 minutes. The first user-facing query after a cold start eats
    20-60s, which ruins request pacing. Triggering the wake-up in
    lifespan means the warehouse is already running by the time the
    first React query fires.

    Failure is logged but non-fatal -- the circuit breaker will mask
    the cold first request and the UI degraded banner covers the gap.
    Blocking startup on a transient warehouse outage would be worse
    than letting it start and degrade visibly.
    """
    from backend.services.databricks_sql import get_sql_client

    start = time.monotonic()
    try:
        client = get_sql_client()
        client.execute_one("SELECT 1 AS warm")
        took_ms = int((time.monotonic() - start) * 1000)
        log.info("warehouse warm (took %dms)", took_ms)
    except Exception as exc:  # noqa: BLE001 -- log-and-continue is the contract
        log.warning("warehouse warm-start failed (non-fatal): %s", exc)


def _warm_lakebase() -> None:
    """Issue one ``SELECT 1`` against Lakebase to warm the connection.

    Same posture as ``_warm_warehouse``: failure is logged, startup
    continues, and the audit router's 503 + resilience-aware UI
    banner cover the gap until the first real request succeeds.
    """
    from backend.services.lakebase import get_lakebase_client

    # If Lakebase creds are absent, skip silently -- the audit router
    # already surfaces 503 on its own when the creds are missing, and
    # we don't want to duplicate that signal at startup.
    if not settings.lakebase_host or not settings.lakebase_user:
        log.info("lakebase warm-start skipped (no creds configured)")
        return
    start = time.monotonic()
    try:
        client = get_lakebase_client()
        client.fetchone("SELECT 1 AS warm")
        took_ms = int((time.monotonic() - start) * 1000)
        log.info("lakebase warm (took %dms)", took_ms)
    except Exception as exc:  # noqa: BLE001 -- log-and-continue is the contract
        log.warning("lakebase warm-start failed (non-fatal): %s", exc)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Validate warehouse credentials + warm dependencies before serving.

    Slice-4 invariant: the app runs on real Unity Catalog data in
    every environment. A startup that succeeds without DATABRICKS_*
    credentials would be a latent misconfiguration -- the first /api
    request would 500 on lazy factory init. We fail loudly here
    instead. Pytest runs bypass the check because dependency_overrides
    inject stubs and nothing under tests/ needs live creds.

    Slice-6 additions:
    * After creds pass, issue ``SELECT 1`` against the warehouse and
      Lakebase so the first user request doesn't eat cold-start
      latency. Failure is a warning, not a boot-refusal -- circuit
      breakers + the frontend degraded banner cover the gap.
    """
    if not _running_under_pytest():
        # ``require_databricks_creds`` raises RuntimeError with a
        # clear operator-facing message when any of the three env
        # vars is missing. The exception propagates through FastAPI
        # lifespan and terminates the uvicorn process.
        settings.require_databricks_creds()
        _warm_warehouse()
        _warm_lakebase()
    yield


app = FastAPI(title="Mortgage Intelligence Platform API", lifespan=_lifespan)


# Slice-6: translate ``DependencyDownError`` (breaker open or all retries
# exhausted) to a structured HTTP 503 body the frontend's degraded-state
# banner can read. Keeping this handler on the app (not per-router)
# means every future router gets the same contract for free.
from fastapi import Request  # noqa: E402 -- handler below needs it
from fastapi.responses import JSONResponse  # noqa: E402

from backend.services.resilience import DependencyDownError  # noqa: E402


@app.exception_handler(DependencyDownError)
async def _dependency_down_handler(_request: Request, exc: DependencyDownError) -> JSONResponse:
    """Return 503 with ``{detail, retryable, dependency}`` body.

    The frontend keys on ``retryable: true`` to turn on the
    DegradedBanner and start exponential-backoff re-fetch. We surface
    the dependency name so the banner copy can be specific
    ("warehouse is warming up" vs "lakebase is warming up") without
    parsing the free-text detail.
    """
    return JSONResponse(
        status_code=503,
        content={
            "detail": str(exc),
            "retryable": True,
            "dependency": exc.dependency,
        },
    )

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

import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse
from starlette.types import ASGIApp

from backend.api import (
    admin,
    audit,
    borrowers,
    config,
    genie,
    geo,
    health,
    leads,
    offers,
    outreach,
    portfolio,
    segments,
)
from backend.config.settings import _running_under_pytest, settings
from backend.services.observability import (
    configure_logging,
    emit,
    get_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)

log = logging.getLogger("mip-runtime")

# Slice-13: install structured logging on import so every module that
# logs during startup (warehouse warm, Lakebase warm) produces JSON
# lines. configure_logging() is idempotent so a second call during tests
# is a safe no-op.
configure_logging()


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


# ---------------------------------------------------------------------------
# Slice-13: Correlation-ID middleware
# ---------------------------------------------------------------------------


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Attach a correlation ID to every inbound request.

    Reads ``X-Correlation-ID`` from the client (so a browser fetch or a
    load-test harness can propagate its own trace), mints a fresh UUID
    when the header is absent, binds the value to the ContextVar so
    every downstream ``emit(...)`` call carries the same ID, and echoes
    it back on the response. Emits one ``http_request`` log line per
    request with method / path / status / duration_ms.
    """

    HEADER = "X-Correlation-ID"

    # Client-supplied correlation ids get sanitised before we log or echo
    # them. Without this the header would be echoed verbatim into logs
    # and audit trails — a classic log-injection / header-poisoning vector
    # (flagged by Copilot 2026-04-22). Policy:
    #   * charset: [A-Za-z0-9._-] only (matches RFC 4122 UUIDs + common
    #     trace ids without admitting control characters / whitespace)
    #   * length: 1..128 so a burst of huge headers can't bloat log lines
    #   * anything that fails either check is dropped silently and we
    #     mint a fresh correlation id, keeping the middleware invariant
    #     that every request has exactly one id.
    _CID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    @classmethod
    def _sanitize_correlation_id(cls, raw: str | None) -> str | None:
        if raw is None:
            return None
        trimmed = raw.strip()
        if cls._CID_PATTERN.match(trimmed):
            return trimmed
        return None

    async def dispatch(  # type: ignore[override]
        self, request: StarletteRequest, call_next: Any
    ) -> StarletteResponse:
        incoming = self._sanitize_correlation_id(request.headers.get(self.HEADER))
        cid = incoming if incoming else get_correlation_id()
        token = set_correlation_id(cid)
        start = time.monotonic()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[self.HEADER] = cid
            return response
        finally:
            duration_ms = round((time.monotonic() - start) * 1000.0, 2)
            emit(
                logging.getLogger("mip.http"),
                "http_request",
                method=request.method,
                path=request.url.path,
                status=status_code,
                duration_ms=duration_ms,
            )
            reset_correlation_id(token)


app.add_middleware(CorrelationIdMiddleware)


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
    geo.router,
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
    # Hashed Vite assets.
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")
    # Brand artwork (Entrada mark SVG + future brand assets).
    _BRAND_DIR = _FRONTEND_DIST / "brand"
    if _BRAND_DIR.is_dir():
        app.mount("/brand", StaticFiles(directory=_BRAND_DIR), name="brand")

    # Catch-all: first look for a real file at `dist/<full_path>`. If it
    # exists, serve it verbatim (static assets dropped into `public/` —
    # us-counties.json, favicon.svg, future data files — show up at the
    # dist root and need to be reachable by URL path). Otherwise fall
    # back to index.html so React Router owns the route client-side.
    #
    # `no-store` on the SPA shell so browsers don't keep a stale
    # index.html that points at an old hashed-asset bundle after a deploy.
    @app.get("/{full_path:path}")
    def _spa_fallback(full_path: str) -> FileResponse:
        candidate = (_FRONTEND_DIST / full_path).resolve()
        try:
            # Path-traversal guard: candidate must stay inside dist/.
            candidate.relative_to(_FRONTEND_DIST.resolve())
        except ValueError:
            candidate = _FRONTEND_DIST / "index.html"
        if candidate.is_file() and candidate.name != "index.html":
            return FileResponse(candidate)
        return FileResponse(
            _FRONTEND_DIST / "index.html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

"""FastAPI application assembly, middleware, routers, and exception handlers."""

import asyncio
import logging
import os
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse
from starlette.types import ASGIApp

from backend.api import (
    activation,
    admin,
    analytics,
    assets,
    audit,
    borrowers,
    campaigns,
    config,
    data_estate,
    genie,
    genie_feedback_routes,
    geo,
    growth_agent,
    growth_agent_compose_routes,
    health,
    home,
    leads,
    lookup,
    offers,
    outreach,
    portfolio,
    sales,
    segments,
    telemetry,
    workspace,
)
from backend.config.settings import (
    _running_under_pytest,
    check_trust_boundary_at_startup,
    settings,
)
from backend.services.backpressure import BackpressureController, BackpressureMiddleware
from backend.services.observability import (
    configure_logging,
    emit,
    get_correlation_id,
    reset_correlation_id,
    sanitize_correlation_id,
    set_correlation_id,
)
from backend.services.static_assets import select_asset_variant
from backend.services.visit_tracking import VisitTrackingMiddleware
from backend.version import api_version

log = logging.getLogger("mip-runtime")
API_VERSION = "v1"
CANONICAL_API_PREFIX = f"/api/{API_VERSION}"
COMPAT_API_PREFIX = "/api"

# Slice-13: install structured logging on import so every module that
# logs during startup (warehouse warm, Lakebase warm) produces JSON
# lines. configure_logging() is idempotent so a second call during tests
# is a safe no-op.
configure_logging()


def _operation_id(route: APIRoute) -> str:
    """Stable OpenAPI operation ids across versioned + compatibility routes."""

    methods = "_".join(sorted((route.methods or set()) - {"HEAD", "OPTIONS"})).lower()
    path = re.sub(r"[^0-9A-Za-z]+", "_", route.path_format).strip("_").lower()
    return f"{methods}_{path}_{route.name}"


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
        emit(
            log,
            "warehouse_warm_start_succeeded",
            dependency="warehouse",
            outcome="success",
            duration_ms=took_ms,
        )
    except Exception as exc:  # noqa: BLE001 -- log-and-continue is the contract
        emit(
            log,
            "warehouse_warm_start_failed",
            level=logging.WARNING,
            dependency="warehouse",
            outcome="error",
            exc_type=type(exc).__name__,
            exc_msg=str(exc)[:500],
        )


def _warm_lakebase() -> None:
    """Issue one ``SELECT 1`` against Lakebase to warm the connection.

    Same posture as ``_warm_warehouse``: failure is logged, startup
    continues, and the audit router's 503 + resilience-aware UI
    banner cover the gap until the first real request succeeds.
    """
    from backend.services.lakebase import get_lakebase_client

    # If Lakebase connection hints are absent, skip silently -- the
    # audit router already surfaces 503 on its own when the creds are
    # missing, and we don't want to duplicate that signal at startup.
    #
    # Deployed Databricks Apps may provide PGHOST / PGUSER plus a
    # workspace-token password provider instead of LAKEBASE_* vars, so
    # the warm-start gate must mirror the Lakebase client resolver's
    # supported connection hints rather than only settings fields.
    host_hint = settings.lakebase_host or os.environ.get("PGHOST")
    user_hint = settings.lakebase_user or os.environ.get("PGUSER")
    if not host_hint or not user_hint:
        emit(
            log,
            "lakebase_warm_start_skipped",
            dependency="lakebase",
            outcome="skipped",
            reason="missing_connection_hints",
        )
        return
    start = time.monotonic()
    try:
        client = get_lakebase_client()
        client.fetchone("SELECT 1 AS warm")
        took_ms = int((time.monotonic() - start) * 1000)
        emit(
            log,
            "lakebase_warm_start_succeeded",
            dependency="lakebase",
            outcome="success",
            duration_ms=took_ms,
        )
    except Exception as exc:  # noqa: BLE001 -- log-and-continue is the contract
        emit(
            log,
            "lakebase_warm_start_failed",
            level=logging.WARNING,
            dependency="lakebase",
            outcome="error",
            exc_type=type(exc).__name__,
            exc_msg=str(exc)[:500],
        )


def _warm_hot_lead_cache() -> None:
    """Populate the default `/api/leads` cache entry (list + count).

    2026-06-11 audit P1-6: the default lead page is the slowest hot-path
    query (3.6-6.6s cold) and fronts both hero routes. Warming it at
    startup improves the first route load after a process start; an
    optional positive ``MIP_LEADS_WARM_INTERVAL_S`` can also re-warm on
    a cadence below the cache TTL for staffed walkthrough blocks. Same
    log-and-continue posture as ``_warm_warehouse``: a failed warm
    degrades to the pre-existing cold-query behaviour, never a boot refusal.
    """
    from backend.services.lead_warm import warm_default_lead_page

    start = time.monotonic()
    try:
        result = warm_default_lead_page()
        emit(
            log,
            "lead_cache_warm_succeeded",
            dependency="warehouse",
            outcome="success",
            duration_ms=int((time.monotonic() - start) * 1000),
            leads=result["leads"],
            total=result["total"],
        )
    except Exception as exc:  # noqa: BLE001 -- log-and-continue is the contract
        emit(
            log,
            "lead_cache_warm_failed",
            level=logging.WARNING,
            dependency="warehouse",
            outcome="error",
            exc_type=type(exc).__name__,
            exc_msg=str(exc)[:500],
        )


async def _lead_cache_rewarm_loop(interval_s: float) -> None:
    """Refresh-ahead: keep the default lead page permanently warm."""
    while True:
        await asyncio.sleep(interval_s)
        await asyncio.to_thread(_warm_hot_lead_cache)


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

    2026-06-11 audit P1-6:
    * Warm the default `/api/leads` page (slowest hot-path query) once at
      startup. When ``MIP_LEADS_WARM_INTERVAL_S`` is positive, keep it warm
      with a refresh-ahead loop for demo/performance-critical sessions; the
      deployed app default sets this to 0 so idle workspaces can auto-stop.
    """
    rewarm_task: asyncio.Task[None] | None = None
    if not _running_under_pytest():
        # ``require_databricks_creds`` raises RuntimeError with a
        # clear operator-facing message when any of the three env
        # vars is missing. The exception propagates through FastAPI
        # lifespan and terminates the uvicorn process.
        settings.require_databricks_creds()
        # R6-10 trust boundary guard: emit WARNING when the default
        # `trust_forwarded_headers=True` is active but the runtime
        # doesn't look like a Databricks Apps deploy (so no upstream
        # header-stripping edge). Non-fatal; operators read the
        # structured log line and decide whether to flip the flag.
        check_trust_boundary_at_startup()
        _warm_warehouse()
        _warm_lakebase()
        _warm_hot_lead_cache()
        if settings.mip_leads_warm_interval_s > 0:
            rewarm_task = asyncio.create_task(
                _lead_cache_rewarm_loop(settings.mip_leads_warm_interval_s)
            )
        else:
            emit(
                log,
                "lead_cache_rewarm_disabled",
                dependency="warehouse",
                outcome="skipped",
                interval_s=settings.mip_leads_warm_interval_s,
            )
    try:
        yield
    finally:
        if rewarm_task is not None:
            rewarm_task.cancel()
            with suppress(asyncio.CancelledError):
                await rewarm_task


app = FastAPI(
    title="Mortgage Intelligence Platform API",
    version=api_version(),
    lifespan=_lifespan,
    docs_url="/docs" if settings.mip_expose_openapi else None,
    redoc_url="/redoc" if settings.mip_expose_openapi else None,
    openapi_url="/openapi.json" if settings.mip_expose_openapi else None,
    generate_unique_id_function=_operation_id,
)


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

    R5-17: the ``path`` field logged on every request is the
    **templated** route (``/api/borrowers/{id}``), not the concrete URL
    path (``/api/borrowers/B-00042``). Borrower-level routes put IDs in
    the path segment -- synthetic today, but the roadmap replaces them
    with real Cotality CLIPs, at which point a verbatim-path log line
    becomes a CLIP trail keyed to the correlation id. We match the
    template off the resolved Starlette ``Route`` (populated by
    FastAPI's router in ``request.scope["route"]`` after ``call_next``
    returns) and fall back to a simple segment-stripping regex for any
    paths that slipped through routing (static files, unresolved 404s,
    SPA fallback). This mirrors ``_statement_hash`` in
    ``backend/services/databricks_sql.py`` -- log the shape, not the
    value.
    """

    HEADER = "X-Correlation-ID"

    # Client-supplied correlation ids get sanitised before we log or echo
    # them. Without this the header would be echoed verbatim into logs
    # and audit trails — a classic log-injection / header-poisoning vector
    # (flagged by Copilot 2026-04-22). Policy:
    #   * charset: [A-Za-z0-9._-] only (matches RFC 4122 UUIDs + common
    #     trace ids without admitting control characters / whitespace)
    #   * length: 1..128 so a burst of huge headers can't bloat log lines
    #   * value must not be shaped like PII; correlation ids are persisted
    #     into the audit ledger, response bodies, and logs, so a caller trying
    #     to use an SSN/phone/email/borrower id as a trace id gets a fresh id.
    #   * anything that fails either check is dropped silently and we
    #     mint a fresh correlation id, keeping the middleware invariant
    #     that every request has exactly one id.
    # Fallback path normaliser for unrouted paths. Matches the two
    # borrower-id shapes the product currently emits -- masked borrower
    # IDs and Cotality CLIP strings (numeric, >= 6 digits per the
    # Cotality data contract). Conservative on purpose: we would rather
    # under-normalise an unknown path than over-normalise a real UC
    # object name that happens to match a loose pattern.
    _ID_SEGMENT_PATTERN = re.compile(
        r"/(?:B-[A-Za-z0-9][A-Za-z0-9_-]{0,126}|CL-[A-Za-z0-9]+|\d{6,})(?=/|$)"
    )

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    @classmethod
    def _sanitize_correlation_id(cls, raw: str | None) -> str | None:
        return sanitize_correlation_id(raw)

    @classmethod
    def _templated_path(cls, request: StarletteRequest) -> str:
        """Return the route template for logging.

        Prefer the resolved Starlette ``Route`` parked on
        ``request.scope["route"]`` by FastAPI's router -- that's the
        literal ``@router.get("/{borrower_id}")`` string the developer
        typed, so ID-bearing segments are already parameterised. Falls
        back to a regex-normalised form of the concrete URL path when
        no route was matched (pre-routing exceptions, 404s, the SPA
        catch-all). Never returns the raw URL path verbatim for
        ID-bearing segments.
        """
        route = request.scope.get("route") if request.scope else None
        template = getattr(route, "path", None)
        if isinstance(template, str) and template:
            return template
        return cls._ID_SEGMENT_PATTERN.sub("/{id}", request.url.path)

    async def dispatch(
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
                path=self._templated_path(request),
                status=status_code,
                duration_ms=duration_ms,
            )
            reset_correlation_id(token)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach browser security headers to every app response.

    The Databricks Apps edge owns authentication and may add its own
    platform headers after this middleware runs. These headers cover the
    application-controlled browser posture: no content sniffing, no
    framing, conservative referrer behavior, no ambient device APIs, and
    a CSP tuned for the static Vite SPA served from this same origin.
    """

    _CSP = (
        "default-src 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "form-action 'self'"
    )

    async def dispatch(
        self, request: StarletteRequest, call_next: Any
    ) -> StarletteResponse:
        response = await call_next(request)
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), camera=(), microphone=()",
        )
        response.headers.setdefault("Content-Security-Policy", self._CSP)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("X-API-Version", API_VERSION)
        if request.url.path.startswith("/assets/"):
            response.headers.setdefault(
                "Cache-Control",
                "public, max-age=31536000, immutable",
            )
        return response


_backpressure_controller = BackpressureController()

app.add_middleware(BackpressureMiddleware, controller=_backpressure_controller)
# S3: throttled authenticated-visit ledger (one mip_app.user_visits row per
# actor per window; the Lakebase write happens off the request path).
# Added before CorrelationIdMiddleware so it sits INSIDE it in the stack
# (last add_middleware call = outermost) and its structured log lines carry
# the request's correlation id.
app.add_middleware(VisitTrackingMiddleware)
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
# compresslevel=6 (not the library default 9): dynamic JSON responses sit on
# the request path, and levels 7-9 buy ~1-2% extra ratio for ~2x the CPU.
# Static assets no longer rely on this middleware at all -- they are
# precompressed at build time (brotli q11 / gzip 9) and served by the
# negotiated /assets route below. 2026-06-10 perf slice.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)


# Slice-6: translate ``DependencyDownError`` (breaker open or all retries
# exhausted) to a structured HTTP 503 body the frontend's degraded-state
# banner can read. Keeping this handler on the app (not per-router)
# means every future router gets the same contract for free.
from fastapi import Request  # noqa: E402 -- handler below needs it
from fastapi.responses import JSONResponse  # noqa: E402

from backend.services.error_sanitizer import safe_dependency_detail  # noqa: E402
from backend.services.resilience import DependencyDownError  # noqa: E402


@app.exception_handler(DependencyDownError)
async def _dependency_down_handler(_request: Request, exc: DependencyDownError) -> JSONResponse:
    """Return 503 with ``{detail, retryable, dependency, correlation_id}`` body.

    The frontend keys on ``retryable: true`` to turn on the
    DegradedBanner and start exponential-backoff re-fetch. We surface
    the dependency name so the banner copy can be specific
    ("warehouse is warming up" vs "lakebase is warming up") without
    parsing the free-text detail.

    Round-3 hole-finder #10: include ``correlation_id`` in the body (it's
    also in the ``X-Correlation-ID`` header, but operators pasting errors
    into incident channels lose the header; the body copy survives the
    paste). Clients can cite one trace id from either source.

    Round-5 R5-02: the ``detail`` field is a constant per-dependency
    string derived from ``safe_dependency_detail``; it MUST NOT echo
    ``str(exc)``. The underlying exception text (which for
    ``DatabricksSqlError`` contains ``state=``, ``statement_id=``, and
    warehouse-authored error text that routinely quotes column names and
    predicate values) is logged at WARNING with structured fields so
    operators retain full visibility without exposing the text on the
    wire.
    """
    emit(
        log,
        "dependency_down_handled",
        level=logging.WARNING,
        dependency=exc.dependency,
        reason=exc.reason,
        kind=exc.kind,
        # ``last_error_str`` is the full upstream message; kept in the
        # structured log line so ops can still pull state=/statement_id=/
        # err_msg from Splunk/Datadog/etc. without the browser seeing it.
        last_error_str=str(exc.last_error) if exc.last_error is not None else None,
        last_error_type=type(exc.last_error).__name__ if exc.last_error is not None else None,
        correlation_id=get_correlation_id(),
    )
    return JSONResponse(
        status_code=503,
        content={
            "detail": safe_dependency_detail(exc.dependency),
            "retryable": True,
            "dependency": exc.dependency,
            # R6-05: additive machine-readable classification. Values are
            # ``"warming_up"`` (cold-start, fast retry OK), ``"breaker_open"``
            # (breaker already tripped -- back off to cooldown window before
            # retrying), ``"retries_exhausted"`` (retry budget blown, harder
            # failure). The legacy ``retryable: true`` stays for
            # backward compatibility; the frontend (parallel cycle) can key
            # off ``reason`` for a smarter backoff curve.
            "reason": exc.kind,
            "correlation_id": get_correlation_id(),
        },
    )


@app.exception_handler(RequestValidationError)
async def _request_validation_handler(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Mirror FastAPI's 422 shape while adding the request correlation id.

    Operators usually receive pasted JSON bodies in incident threads, not
    response headers; the top-level ``correlation_id`` gives support a
    direct log join key for malformed request probes.

    PII posture: pydantic's per-error ``input`` (and sometimes ``ctx``)
    reflect the raw submitted value, and any route can receive PII in a
    malformed body — observed live on /api/genie/message echoing a
    street-address question (2026-07-07). The reflection is stripped
    app-wide, not just for growth-agent routes; ``loc``/``msg``/``type``
    keep the error actionable.
    """

    detail = [
        {key: value for key, value in item.items() if key not in ("input", "ctx", "url")}
        if isinstance(item, dict)
        else item
        for item in jsonable_encoder(exc.errors())
    ]

    return JSONResponse(
        status_code=422,
        content={
            "detail": detail,
            "correlation_id": get_correlation_id(),
        },
    )


API_ROUTERS = [
    health.router,
    config.router,
    data_estate.router,
    activation.router,
    admin.router,
    assets.router,
    analytics.router,
    home.router,
    portfolio.router,
    campaigns.router,
    segments.router,
    leads.router,
    borrowers.router,
    lookup.router,
    offers.router,
    outreach.router,
    sales.router,
    geo.router,
    growth_agent.router,
    growth_agent_compose_routes.router,
    genie.router,
    genie_feedback_routes.router,
    audit.router,
    telemetry.router,
    workspace.router,
]

for router in API_ROUTERS:
    app.include_router(router, prefix=CANONICAL_API_PREFIX)

for router in API_ROUTERS:
    app.include_router(router, prefix=COMPAT_API_PREFIX, deprecated=True)


if not settings.mip_expose_openapi:
    # Keep FastAPI's schema/documentation routes from falling through to the
    # SPA catch-all. Returning the same compact 404 shape as unmatched /api/*
    # paths avoids leaking whether the generated docs are merely hidden or
    # absent from this deployment.
    @app.get("/openapi.json", response_model=None, include_in_schema=False)
    def _openapi_disabled() -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "not found"})

    @app.get("/docs", response_model=None, include_in_schema=False)
    def _docs_disabled() -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "not found"})

    @app.get("/redoc", response_model=None, include_in_schema=False)
    def _redoc_disabled() -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "not found"})


# R5-15 (continued): a dedicated /api/* 404 handler that always runs,
# whether frontend/dist/ is present or not. The SPA catch-all below
# only registers when dist/ exists, and without it FastAPI's default
# 404 fires with "Not Found" (capital N). That broke the JSON contract
# clients parse (they expect {"detail":"not found"}). Registering this
# route unconditionally keeps the contract stable across local dev,
# CI, and Databricks Apps deploys.
@app.get("/api/{full_path:path}", response_model=None, include_in_schema=False)
def _api_404(full_path: str) -> JSONResponse:  # noqa: ARG001
    return JSONResponse(status_code=404, content={"detail": "not found"})


# Serve the built Vite SPA. Frontend bundle lands in `frontend/dist/` after
# `npm run build`. On Databricks Apps this happens during deployment; locally
# it happens when the user runs `make build` or `npm --prefix frontend run
# build`. If dist/ is absent (e.g. running the API standalone during dev),
# skip the mount and let the router-only API serve /api/* normally.
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if _FRONTEND_DIST.is_dir() and (_FRONTEND_DIST / "index.html").is_file():
    # Hashed Vite assets — explicit route instead of a StaticFiles mount so
    # we can serve the build-time precompressed ``.br``/``.gz`` siblings
    # emitted by ``tools/precompress_assets.mjs`` (2026-06-10 perf slice).
    # The request path never pays compression CPU for immutable assets and
    # brotli's smaller payloads reach the wire. Identity fallback keeps the
    # pre-precompress behaviour: GZipMiddleware compresses dynamically
    # (Starlette skips responses that already carry Content-Encoding, so
    # nothing is double-compressed). SecurityHeadersMiddleware continues to
    # stamp the immutable Cache-Control for every /assets/ response.
    _ASSETS_DIR = _FRONTEND_DIST / "assets"

    @app.get("/assets/{asset_path:path}", response_model=None, include_in_schema=False)
    def _hashed_asset(asset_path: str, request: Request) -> FileResponse | JSONResponse:
        variant = select_asset_variant(
            _ASSETS_DIR,
            asset_path,
            request.headers.get("accept-encoding"),
        )
        if variant is None:
            return JSONResponse(status_code=404, content={"detail": "not found"})
        headers = {"Vary": "Accept-Encoding"}
        if variant.content_encoding is not None:
            headers["Content-Encoding"] = variant.content_encoding
        return FileResponse(
            variant.path,
            media_type=variant.media_type,
            headers=headers,
        )

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
    @app.get("/{full_path:path}", response_model=None)
    def _spa_fallback(full_path: str) -> FileResponse | JSONResponse:
        # R5-15: API paths that don't match a registered route must 404,
        # not fall through to index.html. The prior behavior silently
        # returned HTML for typos / trailing-slash edge cases, which
        # suppressed monitoring signal (clients parsing JSON would see
        # a parse error, logs would show 200 OK) and masked real
        # routing bugs. FastAPI strips the leading slash into
        # ``full_path``, so an inbound ``/api/xxx`` arrives here as
        # ``api/xxx``.
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "not found"})
        candidate = (_FRONTEND_DIST / full_path).resolve()
        try:
            # Path-traversal guard: candidate must stay inside dist/.
            candidate.relative_to(_FRONTEND_DIST.resolve())
        except ValueError:
            # R6-14: a candidate outside dist/ means a traversal attempt
            # (``../../etc/passwd``-style). Serving index.html is still
            # the correct SPA behaviour -- React Router will resolve the
            # client route, or show its own 404 for a bogus path -- but
            # we MUST surface the probe to ops. Without this log line an
            # attacker's recon of the static surface is completely silent
            # in the structured log trail. We log the attempted path at
            # WARNING + emit ``event=spa_path_traversal_blocked`` with
            # the original raw path so SOC/SIEM dashboards can pattern-
            # match. The path is capped at 256 chars to bound log line
            # size; longer probe strings are truncated with an ellipsis.
            attempted = full_path if len(full_path) <= 256 else full_path[:256] + "..."
            emit(
                log,
                "spa_path_traversal_blocked",
                level=logging.WARNING,
                path=attempted,
            )
            candidate = _FRONTEND_DIST / "index.html"
        if candidate.is_file() and candidate.name != "index.html":
            return FileResponse(candidate)
        return FileResponse(
            _FRONTEND_DIST / "index.html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

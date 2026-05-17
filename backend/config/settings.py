"""App settings for the Mortgage Intelligence Platform backend.

Invariant: the running app always reads live Unity Catalog data through
the Databricks SQL warehouse. Missing warehouse credentials are a
fail-fast startup error -- they do NOT silently fall back to fixtures.

The ``Databricks*`` fields below are required at import time EXCEPT in
test processes (detected via ``PYTEST_CURRENT_TEST``), which inject
stub repositories through FastAPI dependency overrides and therefore
never open a warehouse connection.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Documented, shared error message so every fail-fast site reads the
# same -- helps operators who see it in a container log.
_MISSING_CREDS_MSG = (
    "Mortgage Intelligence Platform refuses to start without live "
    "Databricks warehouse credentials. Set DATABRICKS_HOST, "
    "DATABRICKS_TOKEN, and DATABRICKS_WAREHOUSE_ID in .env.local "
    "(see .env.example). The app runs on real Unity Catalog data in "
    "every environment; it fails visibly when a credential is missing "
    "rather than substituting synthesized data."
)


def _running_under_pytest() -> bool:
    """True when the current process was launched by pytest.

    Pytest exports ``PYTEST_CURRENT_TEST`` for the duration of each
    test item; it's also set during collection in recent pytest
    versions. We additionally honour ``MIP_BYPASS_STARTUP_CHECKS=1`` as
    an explicit escape hatch for CI phases (lint / type-check /
    schema-only) that import ``backend.config.settings`` without ever
    hitting the warehouse.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    if os.environ.get("MIP_BYPASS_STARTUP_CHECKS") == "1":
        return True
    # Fallback: the pytest runner imports ``pytest`` before any user
    # code; if it's in sys.modules we're in a test process.
    import sys

    return "pytest" in sys.modules


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "local"
    mip_lender_name: str = "Summit Mortgage"
    mip_default_catalog: str = "mip"
    mip_default_schema: str = "gold"
    mip_lakebase_schema: str = "mip_app"
    # FastAPI's generated OpenAPI/Swagger/ReDoc surfaces expose every
    # route and schema to any authenticated workspace user. Keep them
    # off by default for demo/customer deploys; developers can opt in
    # locally with MIP_EXPOSE_OPENAPI=1 when they need schema browsing.
    mip_expose_openapi: bool = False

    # In-the-money contract: matches tests/fixtures/rate_spread_golden.json
    # (market_rate_constant) and tests/fixtures/in_the_money_golden.json
    # (default_thresholds). Overridable via admin config at runtime.
    mip_market_rate: float = 0.04875
    mip_min_spread_bps: int = 75
    mip_min_equity_pct: int = 15

    # Next-best-offer thresholds: matches
    # tests/fixtures/next_best_offer_golden.json (default_thresholds).
    # `heloc_equity_min_pct > min_equity_pct` is intentional -- HELOC
    # underwriting demands more equity cushion than plain refi.
    # `retention_min_spread < min_spread_bps` is intentional -- we reach
    # out earlier on existing relationships.
    mip_heloc_equity_min_pct: int = 35
    mip_cashout_equity_min_pct: int = 25
    mip_retention_min_spread_bps: int = 50

    # Databricks SQL warehouse credentials -- required for every
    # non-test process. Validated by ``require_databricks_creds()``.
    databricks_host: str | None = None
    databricks_token: SecretStr | None = Field(default=None, repr=False)
    databricks_warehouse_id: str | None = None
    databricks_timeout_s: int = 30

    # Genie space id -- loaded by ``backend.services.genie_client`` with
    # a repo-committed fallback at ``genie/space_id.txt`` so a fresh
    # checkout has a working Genie target without env plumbing. The env
    # var always overrides the file so deploy-time wiring picks up a
    # per-environment space id.
    genie_space_id: str | None = None

    # Server-side HMAC key for Genie action confirmation tokens. When
    # unset, the app generates a process-local key at boot, which keeps
    # tokens unforgeable but invalidates outstanding confirmations after
    # a restart. Set MIP_GENIE_ACTION_SECRET in production to preserve
    # still-visible answer actions across app restarts.
    mip_genie_action_secret: SecretStr | None = Field(default=None, repr=False)

    # Lakebase Postgres credentials -- required for the durable audit
    # trail introduced in Slice 5. Missing values make the audit write
    # path raise ``LakebaseError`` (audit router returns 503); they do
    # NOT gate FastAPI startup because Slice 6 adds resilience. For now
    # the audit router's 503 is a visible, non-silent failure -- which
    # is the desired posture per the no-mock-fallback feedback memory.
    lakebase_host: str | None = None
    lakebase_port: int = 5432
    lakebase_database: str = "mip_app_state"
    lakebase_user: str | None = None
    lakebase_password: SecretStr | None = Field(default=None, repr=False)
    lakebase_sslmode: str = "require"

    # Default actor identifier used when ``X-Forwarded-Email`` is absent.
    # In practice this fires for: (1) local dev / pytest paths that
    # don't install the X-Forwarded-Email header, (2) background tasks
    # that re-enter the audit writer through a worker thread without a
    # request context, (3) Databricks Apps warm-up probes that hit the
    # endpoints before the user is OAuth'd through. The string is shown
    # verbatim in the FE audit log, so it should READ like an honest
    # system attribution — not a confusing placeholder. Prior value
    # ``"unknown-actor@local"`` made users ask "who is that?" — the new
    # value names the runtime explicitly so the row reads as "this was
    # the system, not a user". The audit writer still logs a warning
    # every time the fallback kicks in so operators see it in structured
    # logs. Overrideable via the MIP_DEFAULT_ACTOR env var.
    default_actor: str = "system@databricks-apps"

    # Admin RBAC gate for /api/admin/* endpoints. Two recognition paths
    # in ``backend/services/rbac.py::require_admin``:
    #
    # 1. Group membership — ``X-Forwarded-Groups`` includes this name or
    #    the hard-coded fallback ``"admins"``. Overrideable via
    #    ``MIP_ADMIN_GROUP_NAME``.
    # 2. Email allowlist — ``X-Forwarded-Email`` is in the comma-
    #    separated list below. Primary path for day-0 customer deploys
    #    where workspace groups aren't pre-provisioned. Overrideable
    #    via ``MIP_ADMIN_EMAILS``.
    admin_group_name: str = "mip-admin"
    admin_emails: str = "skyler@entrada.ai"

    # R5-09 trust boundary. Databricks Apps is the authoritative
    # identity edge: it strips inbound ``X-Forwarded-*`` headers and
    # injects its own based on the authenticated workspace user. That's
    # the posture the default (True) assumes -- matching production.
    #
    # Flip to False for unusual deploys where an intermediate proxy
    # does NOT strip client-supplied ``X-Forwarded-Email`` /
    # ``X-Forwarded-Groups`` headers. With trust disabled:
    #
    # * ``backend.services.rbac.require_admin`` ignores the forwarded
    #   group list and denies unless the email allowlist admits the
    #   caller (fail-closed).
    # * ``backend.services.audit_store.resolve_actor`` ignores the
    #   forwarded email/user and returns a marker string
    #   (``"unknown-actor@untrusted-edge"``) so every audit row is
    #   attributable to "we don't know who did this", not to a caller-
    #   spoofed identity.
    #
    # See ``docs/security/GRANTS.md`` §Trust boundary for the
    # deployment shapes where flipping this matters.
    trust_forwarded_headers: bool = True

    # Slice-6 TTL cache: short-window memoization on aggregate KPIs that
    # tolerate staleness (segments count, portfolio preview). Fresh-only
    # endpoints (audit, outreach, borrower dossier) never consult the
    # cache. Set to 0 to disable caching entirely (tests do this).
    mip_cache_ttl_s: float = 30.0

    # Slice-13 follow-up: optional OTLP log exporter.
    #
    # When ``MIP_OTEL_ENDPOINT`` is set at boot we ship every JSON log
    # line through the OpenTelemetry OTLP *logs* exporter so a workspace-
    # external sink (Splunk HEC, Datadog, Grafana Loki, etc.) receives a
    # durable copy. Docs/observability.md walks through the common sinks.
    #
    # When unset, the app behaves exactly as before -- stdout JSON only.
    # The production App image includes the OTLP exporter wheels. Minimal
    # local installs may omit them; if the env var is set but the packages
    # are not importable the runtime logs a warning and keeps serving
    # traffic on stdout-only.
    #
    # ``MIP_OTEL_HEADERS`` is a comma-separated ``k=v,k2=v2`` string the
    # exporter passes as OTLP metadata (typically the Splunk HEC token
    # or the Datadog API key). We never log the value.
    mip_otel_endpoint: str | None = None
    mip_otel_headers: SecretStr | None = Field(default=None, repr=False)
    # Browser Real User Monitoring. The client sends only sanitized route
    # patterns and aggregate performance metrics; no query strings,
    # borrower IDs, UUIDs, email addresses, or free-form text are accepted.
    mip_rum_enabled: bool = True
    # Slice-13 performance follow-up: portfolio preview is an expensive
    # aggregate over 5.16M rows; its cache-miss cost shows up as a
    # p95 ~1.1 s tail on /api/portfolio/preview (load-baseline.md). The
    # aggregate refreshes at most once per gold-refresh cycle, so a
    # 120-second TTL is comfortably shorter than the data ages while
    # letting burst traffic hit the cache. Override back to
    # `mip_cache_ttl_s` on dev laptops where snappier dev UX trumps
    # warehouse-query minimisation.
    mip_portfolio_preview_ttl_s: float = 120.0

    # App-level load protection. These are process-local guards, not a
    # replacement for Databricks workspace quotas. They prevent one
    # authenticated browser or automation loop from saturating expensive
    # dependencies before the request reaches the warehouse/Lakebase/Genie
    # client. Over-budget callers receive HTTP 429 + Retry-After.
    mip_backpressure_enabled: bool = True
    mip_rate_limit_default_per_minute: int = 600
    mip_rate_limit_expensive_per_minute: int = 180
    mip_rate_limit_mutation_per_minute: int = 120
    mip_rate_limit_genie_per_minute: int = 30
    mip_rate_limit_telemetry_per_minute: int = 1200
    mip_warehouse_concurrency_limit: int = 24
    mip_lakebase_concurrency_limit: int = 16
    mip_genie_concurrency_limit: int = 4
    # Lakebase connection pooling. Connections use short-lived workspace
    # OAuth credentials in Databricks Apps, so reuse is bounded by a max
    # lifetime comfortably below the token expiry. Set max size to 0 to
    # force one-connection-per-call behavior for local diagnostics.
    mip_lakebase_pool_max_size: int = 8
    mip_lakebase_pool_timeout_s: float = 2.0
    mip_lakebase_pool_max_lifetime_s: float = 3000.0

    def require_databricks_creds(self) -> tuple[str, Callable[[], str], str]:
        """Return ``(host, token_provider, warehouse_id)`` or raise at startup.

        ``token_provider`` is a zero-arg callable that returns a fresh
        bearer string each call. Two supported pathways:

        1. **Local / CI with a PAT** -- set ``DATABRICKS_TOKEN`` directly.
           ``token_provider`` returns the literal PAT every time.

        2. **Databricks Apps (workspace identity)** -- on Databricks
           Apps the runtime injects ``DATABRICKS_HOST`` and the service-
           principal OAuth credentials (``DATABRICKS_CLIENT_ID`` /
           ``DATABRICKS_CLIENT_SECRET``) but NOT a PAT. When the PAT is
           absent we hand back the SDK's authenticate-callback as the
           provider. The SDK caches and refreshes the bearer
           internally, so each call returns a non-expired token. This
           replaces the prior contract that minted ONCE at startup and
           cached the result -- which expired after ~1h and produced
           HTTP 403 ``Invalid Token`` on every warehouse call until
           someone restarted the app. 2026-04-25 incident.

        Never call this from a path that imports ``settings`` at module-
        import time unless you want the process to refuse to boot on a
        missing env var -- that is the intended behavior for the live
        SQL client and its factory, but not for simple utility imports.
        """
        host = self.databricks_host
        warehouse = self.databricks_warehouse_id
        if not host or not warehouse:
            raise RuntimeError(_MISSING_CREDS_MSG)
        # Normalise host shape: strip trailing slash, ensure scheme.
        if not host.startswith("http"):
            host = "https://" + host
        host = host.rstrip("/")

        if self.databricks_token is not None:
            literal = self.databricks_token.get_secret_value()
            return host, (lambda: literal), warehouse

        sdk_provider = _build_workspace_identity_provider(host)
        if sdk_provider is None:
            raise RuntimeError(_MISSING_CREDS_MSG)
        return host, sdk_provider, warehouse


def _build_workspace_identity_provider(host: str) -> Callable[[], str] | None:
    """Build a per-request token provider from workspace identity.

    Returns a zero-arg callable that, on each call, asks the Databricks
    SDK for an Authorization header and extracts the Bearer value. The
    SDK's ``Config.authenticate`` caches and refreshes the underlying
    OAuth token internally, so the callable form gives our SQL/HTTP
    clients a non-expiring source of truth without us having to
    re-implement OAuth refresh.

    Returns None when the SDK can't authenticate at construction time
    (no service-principal credentials in env, not running on Databricks
    Apps, etc.) so the caller falls through to the missing-creds error.

    The SDK picks up credentials from standard env vars that Databricks
    Apps populates automatically: ``DATABRICKS_CLIENT_ID``,
    ``DATABRICKS_CLIENT_SECRET``, or service-principal OAuth token
    exchange. Locally, if you've run ``databricks auth login`` the SDK
    reads ``~/.databrickscfg`` too.

    Emits a diagnostic line to stderr on the construction failure path
    so operator triage in container logs sees exactly which auth step
    failed. We also probe the credentials once here so a misconfigured
    environment fails at startup rather than on the first request.
    """
    import sys  # local; only needed on the auth-debug path

    try:
        from databricks.sdk.core import Config as _Config  # pragma: no cover

        cfg = _Config(host=host)
        # Probe once so we surface bad creds at startup, not at first
        # request. The SDK caches the result; the next call from
        # ``provider()`` will be a no-op cache hit until expiry.
        probe_headers: dict[str, str] = cfg.authenticate()
        probe_auth = (
            probe_headers.get("Authorization", "") if isinstance(probe_headers, dict) else ""
        )
        if not probe_auth.startswith("Bearer "):
            print(
                f"[mip-runtime] workspace-identity auth returned no Bearer header; "
                f"keys={list(probe_headers.keys()) if isinstance(probe_headers, dict) else 'non-dict'}",
                file=sys.stderr,
            )
            return None
        print(
            "[mip-runtime] workspace-identity auth ok "
            f"(auth_type={getattr(cfg, 'auth_type', 'unknown')}); "
            "tokens will be refreshed per-request via SDK Config.authenticate",
            file=sys.stderr,
        )

        def provider() -> str:
            # Re-read the Authorization header on every call. The SDK
            # caches the underlying OAuth token internally and refreshes
            # before expiry, so this is a fast in-memory lookup in the
            # common case and a fresh OAuth exchange when the cached
            # token is near expiry.
            headers = cfg.authenticate()
            auth = headers.get("Authorization", "") if isinstance(headers, dict) else ""
            if not auth.startswith("Bearer "):
                raise RuntimeError("Databricks SDK auth returned no Bearer header on refresh")
            return auth.removeprefix("Bearer ").strip()

        return provider
    except Exception as exc:  # noqa: BLE001 -- surface reason to operator
        print(
            f"[mip-runtime] workspace-identity auth FAILED: "
            f"{type(exc).__name__}: {str(exc)[:400]}",
            file=sys.stderr,
        )
        return None


def looks_like_databricks_app_deploy() -> bool:
    """Return True when the runtime environment looks like Databricks Apps.

    R6-10 trust boundary guard. ``trust_forwarded_headers`` defaults to
    True because Databricks Apps is the identity edge that strips
    inbound client headers and injects its own. On a non-Apps deploy
    (Azure App Service, GKE, plain uvicorn behind nginx, a local laptop)
    the default is a trivial RBAC / audit-attribution bypass: any
    caller can forge ``X-Forwarded-Email``.

    Databricks Apps injects ``DATABRICKS_APP_PORT`` into the container
    environment before uvicorn binds (see ``backend/runtime.py`` line
    65 which uses the same env var to choose the listen port). That's
    the most reliable single marker; we also accept
    ``DATABRICKS_APP_URL`` (set on newer Apps images) as a secondary
    signal so a future platform rename doesn't silently disable the
    guard. The check is intentionally forgiving -- a false positive
    (we say "Apps" when we're not) is a silent warning miss; a false
    negative (we say "not Apps" when we are) is a noisy but harmless
    log line. We bias toward false positives to avoid log spam on the
    real production path.
    """
    return bool(os.environ.get("DATABRICKS_APP_PORT") or os.environ.get("DATABRICKS_APP_URL"))


def check_trust_boundary_at_startup() -> None:
    """Emit a structured WARNING when trust is enabled outside Apps.

    Called from the FastAPI lifespan. The warning is non-fatal because
    some unusual deploys (a reverse proxy that DOES strip inbound
    X-Forwarded-* headers) are legitimate with trust=True even without
    the Apps marker. Operators see the WARNING in stdout JSON logs and
    decide whether to flip the flag; the audit ledger separately tracks
    ``fallback_identity_fallbacks_total`` which a non-Apps trusted deploy
    would still pin at zero under legitimate authenticated traffic, so
    the two signals reinforce each other.

    See ``docs/security/GRANTS.md`` §10 Trust boundary for the
    deployment-shape matrix this guards.
    """
    import logging

    log = logging.getLogger("mip-runtime")
    # Re-read settings here rather than using the module-level
    # ``settings`` constant, because tests monkeypatch the flag and want
    # the current value.
    trust = get_settings().trust_forwarded_headers
    if trust and not looks_like_databricks_app_deploy():
        log.log(
            logging.WARNING,
            "rbac_trust_boundary_unclear",
            extra={
                "event": "rbac_trust_boundary_unclear",
                "trust_forwarded_headers": trust,
                "databricks_app_marker": False,
                "mip_event": "rbac_trust_boundary_unclear",
                "mip_extras": {
                    "trust_forwarded_headers": trust,
                    "databricks_app_marker": False,
                    "recommended_action": "set MIP_TRUST_FORWARDED_HEADERS=false outside Databricks Apps unless an upstream proxy strips forwarded identity headers",
                    "docs_ref": "docs/security/GRANTS.md#10",
                },
            },
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

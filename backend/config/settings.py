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

    # Default actor identifier used when ``X-Forwarded-Email`` is absent
    # (local dev / test / broken identity header). Generic, non-customer-
    # specific so a non-Entrada workspace doesn't surface an Entrada email
    # address in its own audit rows. The audit writer logs a warning every
    # time the fallback kicks in so operators see it in structured logs.
    # Overrideable via the MIP_DEFAULT_ACTOR env var.
    default_actor: str = "unknown-actor@local"

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
    # The extra dependencies (``opentelemetry-sdk`` +
    # ``opentelemetry-exporter-otlp``) are intentionally OPTIONAL: if the
    # env var is set but the packages are not importable the runtime
    # logs a warning and keeps serving traffic. This keeps the zero-
    # dependency-on-wheels promise on Databricks Apps while giving
    # operators a single-env-var switch to turn on durable logs.
    #
    # ``MIP_OTEL_HEADERS`` is a comma-separated ``k=v,k2=v2`` string the
    # exporter passes as OTLP metadata (typically the Splunk HEC token
    # or the Datadog API key). We never log the value.
    mip_otel_endpoint: str | None = None
    mip_otel_headers: SecretStr | None = Field(default=None, repr=False)
    # Slice-13 performance follow-up: portfolio preview is an expensive
    # aggregate over 5.16M rows; its cache-miss cost shows up as a
    # p95 ~1.1 s tail on /api/portfolio/preview (load-baseline.md). The
    # aggregate refreshes at most once per gold-refresh cycle, so a
    # 120-second TTL is comfortably shorter than the data ages while
    # letting burst traffic hit the cache. Override back to
    # `mip_cache_ttl_s` on dev laptops where snappier dev UX trumps
    # warehouse-query minimisation.
    mip_portfolio_preview_ttl_s: float = 120.0

    def require_databricks_creds(self) -> tuple[str, SecretStr, str]:
        """Return ``(host, token, warehouse_id)`` or raise at startup.

        Two supported auth pathways:

        1. **Local / CI with a PAT** -- set ``DATABRICKS_TOKEN`` directly.
           Fastest path; used for developer laptops and the nightly
           GitHub Actions workflow which has a PAT in repo secrets.

        2. **Databricks Apps (workspace identity)** -- on Databricks
           Apps the runtime injects ``DATABRICKS_HOST`` and the service-
           principal OAuth credentials (``DATABRICKS_CLIENT_ID`` /
           ``DATABRICKS_CLIENT_SECRET``) but NOT a PAT. When the PAT is
           absent we use the Databricks SDK's auth resolver to mint a
           bearer token from the workspace identity; the rest of our
           stdlib urllib SQL client reads it as an ordinary string.

        Never call this from a path that imports ``settings`` at module-
        import time unless you want the process to refuse to boot on a
        missing env var -- that is the intended behavior for the live
        SQL client and its factory, but not for simple utility imports.
        """
        host = self.databricks_host
        token = self.databricks_token
        warehouse = self.databricks_warehouse_id
        if not host or not warehouse:
            raise RuntimeError(_MISSING_CREDS_MSG)
        if token is None:
            token = _mint_workspace_identity_token(host)
            if token is None:
                raise RuntimeError(_MISSING_CREDS_MSG)
        # Normalise host shape: strip trailing slash, ensure scheme.
        if not host.startswith("http"):
            host = "https://" + host
        host = host.rstrip("/")
        return host, token, warehouse


def _mint_workspace_identity_token(host: str) -> SecretStr | None:
    """Use the Databricks SDK to mint a bearer token from workspace identity.

    Returns None when the SDK can't authenticate (no service-principal
    credentials in env, not running on Databricks Apps, etc.) so the
    caller falls through to the PAT-missing RuntimeError.

    The SDK picks up credentials from standard env vars that Databricks
    Apps populates automatically: ``DATABRICKS_CLIENT_ID``,
    ``DATABRICKS_CLIENT_SECRET``, or service-principal OAuth token
    exchange. Locally, if you've run ``databricks auth login`` the SDK
    reads ``~/.databrickscfg`` too.

    Emits a diagnostic line to stderr on every failure path so operator
    triage in container logs sees exactly which auth step failed.
    """
    import sys  # local; only needed on the auth-debug path
    try:
        from databricks.sdk.core import Config as _Config  # pragma: no cover

        cfg = _Config(host=host)
        headers_cb = cfg.authenticate
        headers: dict[str, str] = headers_cb()
        auth_header = headers.get("Authorization", "") if isinstance(headers, dict) else ""
        if not auth_header.startswith("Bearer "):
            print(
                f"[mip-runtime] workspace-identity auth returned no Bearer header; "
                f"keys={list(headers.keys()) if isinstance(headers, dict) else 'non-dict'}",
                file=sys.stderr,
            )
            return None
        token_val = auth_header.removeprefix("Bearer ").strip()
        print(
            "[mip-runtime] workspace-identity auth ok "
            f"(token_len={len(token_val)}, auth_type={getattr(cfg, 'auth_type', 'unknown')})",
            file=sys.stderr,
        )
        return SecretStr(token_val)
    except Exception as exc:  # noqa: BLE001 -- surface reason to operator
        print(
            f"[mip-runtime] workspace-identity auth FAILED: "
            f"{type(exc).__name__}: {str(exc)[:400]}",
            file=sys.stderr,
        )
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

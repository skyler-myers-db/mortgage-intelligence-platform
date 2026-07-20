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
import re
from collections.abc import Callable
from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.schemas._validators import set_public_lender_name_provider
from backend.schemas.lender_identity import (
    effective_public_tenant_id,
    validate_public_lender_identity,
)

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


_PLACEHOLDER_TOKEN_VALUES = {
    "<pat-or-leave-unset-for-oauth>",
    "<token>",
    "your-token",
    "your-databricks-token",
}
_PLACEHOLDER_WAREHOUSE_VALUES = {
    "<sql-warehouse-id>",
    "<warehouse-id>",
    "sql-warehouse-id",
    "warehouse-id",
    "your-warehouse-id",
    "00000000placeholder",
}
_PLACEHOLDER_HOSTS = {
    "<workspace-host>.cloud.databricks.com",
    "dbc.example",
    "example.cloud.databricks.com",
}

AI_GATEWAY_PROOF_FRESHNESS_MAX_S = 26 * 60 * 60


def _has_angle_bracket_placeholder(value: str | None) -> bool:
    text = (value or "").strip()
    return "<" in text and ">" in text


def _normalized_databricks_host(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"^https?://", "", text)
    return text.rstrip("/")


def is_placeholder_databricks_config(
    *,
    host: str | None = None,
    warehouse_id: str | None = None,
    token: str | None = None,
) -> bool:
    """Return True for documented/example Databricks config values."""
    normalized_host = _normalized_databricks_host(host)
    normalized_warehouse = (warehouse_id or "").strip().lower()
    normalized_token = (token or "").strip().lower()
    return (
        _has_angle_bracket_placeholder(host)
        or _has_angle_bracket_placeholder(warehouse_id)
        or _has_angle_bracket_placeholder(token)
        or normalized_host in _PLACEHOLDER_HOSTS
        or normalized_host.endswith(".example")
        or normalized_host.endswith(".example.com")
        or normalized_host.endswith(".example.invalid")
        or normalized_warehouse in _PLACEHOLDER_WAREHOUSE_VALUES
        or normalized_token in _PLACEHOLDER_TOKEN_VALUES
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
        populate_by_name=True,
    )

    app_env: str = "local"
    mip_git_sha: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MIP_GIT_SHA", "GIT_SHA", "SOURCE_VERSION"),
    )
    mip_app_deployment_lease_id: str | None = None
    mip_lender_name: str = "Summit Mortgage"
    mip_lender_nmls_id: str = ""
    mip_tenant_id: str | None = None
    mip_default_catalog: str = "mip"
    mip_default_schema: str = "gold"
    mip_lakebase_schema: str = "mip_app"
    # FastAPI's generated OpenAPI/Swagger/ReDoc surfaces expose every
    # route and schema to any authenticated workspace user. Keep them
    # off by default for demo/customer deploys; developers can opt in
    # locally with MIP_EXPOSE_OPENAPI=1 when they need schema browsing.
    mip_expose_openapi: bool = False

    # --- DAIS 2026 agentic build: feature flags ------------------------
    # These gate the governed Mortgage Growth Agent stack. They default to
    # OFF so a fresh deploy never *claims* a capability it cannot back with
    # a real, provisioned dependency (the no-overclaim posture). The
    # capability probe (``backend.services.capabilities``) further narrows
    # each flag to what is actually importable/configured in the running
    # workspace, so flipping a flag on without the backing library/creds
    # surfaces as an honest "not provisioned" capability, never a broken UI.
    #
    # ``mip_agent_orchestrator`` — route /ask-genie through the multi-agent
    #   orchestrator (Phase 3). When off, the deterministic Genie path and
    #   the existing Growth Agent slice remain the only surfaces.
    # ``mip_ai_gateway`` — surface Unity AI Gateway governance signals
    #   (Phase 6) read from real inference/system tables. When off, no
    #   gateway chips render.
    # ``mip_lakebase_sync`` — read hot aggregates from synced gold→Lakebase
    #   tables (Phase 7) instead of the warehouse. When off, reads stay on
    #   the warehouse path.
    # ``mip_preview_mirror`` — show preview-gated DAIS capabilities
    #   (CustomerLake, App Spaces, declarative Genie Agents, Lakehouse//RT)
    #   as clearly-labelled *roadmap* patterns. When off, they are hidden
    #   entirely. They are NEVER presented as integrated regardless.
    mip_agent_orchestrator: bool = Field(
        default=False,
        validation_alias=AliasChoices("MIP_AGENT_ORCHESTRATOR", "AGENT_ORCHESTRATOR"),
    )
    mip_ai_gateway: bool = Field(
        default=False,
        validation_alias=AliasChoices("MIP_AI_GATEWAY", "AI_GATEWAY"),
    )
    mip_lakebase_sync: bool = Field(
        default=False,
        validation_alias=AliasChoices("MIP_LAKEBASE_SYNC", "LAKEBASE_SYNC"),
    )
    mip_preview_mirror: bool = Field(
        default=False,
        validation_alias=AliasChoices("MIP_PREVIEW_MIRROR", "PREVIEW_MIRROR"),
    )
    # Default Databricks-hosted model for the orchestrator/specialists.
    mip_agent_model: str = Field(
        default="databricks-claude-sonnet-4-5",
        validation_alias=AliasChoices("MIP_AGENT_MODEL", "AGENT_MODEL"),
    )
    # Optional serving-endpoint name for the hybrid-hosted orchestrator
    # (Phase 3b). When set AND reachable, the in-App route may delegate to
    # the served endpoint; otherwise it runs the orchestrator in-process.
    mip_agent_serving_endpoint: str | None = None
    mip_agent_supervisor_endpoint: str | None = None
    mip_agent_gateway_model: str = Field(
        default="mip.audit.mortgage_growth_supervisor_proxy",
        validation_alias=AliasChoices(
            "MIP_AI_GATEWAY_AGENT_MODEL",
            "AI_GATEWAY_AGENT_MODEL",
        ),
    )
    mip_agent_gateway_model_version: int | None = Field(
        default=None,
        ge=1,
        validation_alias=AliasChoices(
            "MIP_AI_GATEWAY_AGENT_MODEL_VERSION",
            "AI_GATEWAY_AGENT_MODEL_VERSION",
        ),
    )
    mip_ai_gateway_endpoint: str | None = None
    mip_ai_gateway_inference_table: str | None = None
    mip_ai_gateway_agent_model_source: str | None = None
    mip_ai_gateway_experiment_name: str | None = None
    mip_ai_gateway_experiment_id: str | None = None
    mip_expected_agent_gateway_binding_sha256: str | None = None
    mip_expected_agent_gateway_resource_sha256: str | None = None
    mip_expected_agent_gateway_resource_contract_json: str | None = None
    mip_expected_agent_gateway_resource_signature: str | None = None
    mip_gateway_model_attestation_verify_key: str | None = None
    mip_gateway_model_attestation_previous_verify_key: str | None = None
    # Public Ed25519 key for exact inference-row proof attestations. The
    # verifier-only private key is never injected into the App runtime.
    mip_ai_gateway_proof_verify_key: str | None = None
    mip_ai_gateway_proof_freshness_s: float = Field(
        default=AI_GATEWAY_PROOF_FRESHNESS_MAX_S,
        gt=0,
        le=AI_GATEWAY_PROOF_FRESHNESS_MAX_S,
        validation_alias=AliasChoices(
            "MIP_AI_GATEWAY_PROOF_FRESHNESS_S",
            "AI_GATEWAY_PROOF_FRESHNESS_S",
        ),
    )
    mip_agent_eval_experiment: str | None = None
    mip_agent_eval_run_id: str | None = None
    mip_agent_supervisor_id: str | None = None
    mip_agent_supervisor_name: str | None = None
    mip_agent_runtime_client_id: str | None = None
    mip_lakebase_sync_catalog: str = "mip_app_state"
    mip_lakebase_sync_schema: str = "mip_sync"
    mip_lakebase_sync_tables: str = "source_readiness,segment_population,funnel_snapshot_daily"

    def effective_tenant_id(self) -> str:
        """Return the Lakebase disclosure namespace for this deployment."""

        return effective_public_tenant_id(
            self.mip_tenant_id,
            lender_name=self.mip_lender_name,
        )

    # In-the-money contract: matches tests/fixtures/rate_spread_golden.json
    # (market_rate_constant) and tests/fixtures/in_the_money_golden.json
    # (default_thresholds). These are Python/local defaults; deployed gold
    # scoring reads mip.ref.offer_rules_config at refresh time.
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

    # Server-side HMAC key for Genie action confirmation tokens. The
    # rotation-aware path uses MIP_GENIE_ACTION_SECRET_CURRENT +
    # MIP_GENIE_ACTION_SECRET_PREVIOUS with key ids below. The legacy
    # MIP_GENIE_ACTION_SECRET remains accepted as the current key so older
    # deploys can move to the rotation contract without a flag day.
    #
    # Local development and tests may use a process-local key. Deployed
    # sandbox/customer runtimes must receive a stable current key from the
    # deployment payload so outstanding confirmations survive process and
    # replica changes.
    mip_genie_action_secret: SecretStr | None = Field(default=None, repr=False)
    mip_genie_action_secret_current: SecretStr | None = Field(default=None, repr=False)
    mip_genie_action_secret_previous: SecretStr | None = Field(default=None, repr=False)
    mip_genie_action_secret_kid: str = "v1"
    mip_genie_action_secret_previous_kid: str | None = None

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

    # Salesforce activation-delivery adapter (Feature B). OPTIONAL: when
    # any required credential is absent the activation outbox stays in its
    # honest staged/dry_run state and records
    # ``delivery_metadata={delivered:false, reason:"salesforce_not_configured"}``
    # -- the app NEVER claims a write happened without a real HTTP success.
    # See ``backend/services/salesforce_client.py`` and
    # ``backend/services/activation_delivery.py``.
    #
    # The minimal real flow is OAuth 2.0 username-password. Production
    # should migrate to the JWT bearer flow (no password/security-token on
    # the wire); see the client docstring.
    salesforce_instance_url: str | None = None
    salesforce_client_id: str | None = None
    salesforce_client_secret: SecretStr | None = Field(default=None, repr=False)
    salesforce_username: str | None = None
    salesforce_password: SecretStr | None = Field(default=None, repr=False)
    salesforce_security_token: SecretStr | None = Field(default=None, repr=False)
    salesforce_api_version: str = "v60.0"
    salesforce_sobject: str = "Task"
    # Customer-created Salesforce External ID field used for idempotent
    # upsert. A connected destination remains staged unless this is set;
    # ordinary sObject POST cannot provide exactly-once retry semantics.
    salesforce_external_id_field: str | None = None
    # Per-HTTP-call timeout for the Salesforce REST client. Kept short because
    # delivery runs synchronously inside POST /activation/stage; the circuit
    # breaker (failure_threshold=3) fast-fails after a few slow calls so a
    # Salesforce outage can't stall the request path for long.
    salesforce_timeout_s: float = 10.0

    @property
    def salesforce_configured(self) -> bool:
        """True only when every credential the real OAuth + create-record
        flow needs is present. The security token may legitimately be empty
        for orgs that allowlist the app's IP, so it is NOT required here;
        the OAuth call simply appends an empty string."""
        return all(
            (
                self.salesforce_instance_url,
                self.salesforce_client_id,
                self.salesforce_client_secret,
                self.salesforce_username,
                self.salesforce_password,
                self.salesforce_external_id_field,
            )
        )

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
    default_actor: str = Field(
        default="system@databricks-apps",
        validation_alias=AliasChoices("MIP_DEFAULT_ACTOR", "DEFAULT_ACTOR"),
    )

    # Deployed admin automation is authorized by exact actor identities.
    # Human email allowlists are additive. Group matching exists only as a
    # local/test compatibility path because Databricks Apps does not document
    # a forwarded group header as an application authorization contract.
    admin_group_name: str = Field(
        default="mip-admin",
        validation_alias=AliasChoices("MIP_ADMIN_GROUP_NAME", "ADMIN_GROUP_NAME"),
    )
    admin_emails: str = Field(
        default="",
        validation_alias=AliasChoices("MIP_ADMIN_EMAILS", "ADMIN_EMAILS"),
    )
    admin_identities: str = Field(
        default="",
        validation_alias=AliasChoices("MIP_ADMIN_IDENTITIES", "ADMIN_IDENTITIES"),
    )
    # Human decision endpoints admit exact automation identities, explicit
    # approver emails, or admins. The group name is local/test compatibility
    # only and is not provisioned onto the two automation principals.
    approver_group_name: str = Field(
        default="mip-approver",
        validation_alias=AliasChoices("MIP_APPROVER_GROUP_NAME", "APPROVER_GROUP_NAME"),
    )
    approver_emails: str = Field(
        default="",
        validation_alias=AliasChoices("MIP_APPROVER_EMAILS", "APPROVER_EMAILS"),
    )
    approver_identities: str = Field(
        default="",
        validation_alias=AliasChoices("MIP_APPROVER_IDENTITIES", "APPROVER_IDENTITIES"),
    )

    # R5-09 trust boundary. Databricks Apps is the authoritative
    # identity edge: it strips inbound ``X-Forwarded-*`` headers and
    # injects its own based on the authenticated workspace user. That's
    # the posture the default (True) assumes -- matching production.
    #
    # Flip to False for unusual deploys where an intermediate proxy does NOT
    # strip client-supplied ``X-Forwarded-Email`` / ``X-Forwarded-User``.
    # With trust disabled:
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
    trust_forwarded_headers: bool = Field(
        default=True,
        validation_alias=AliasChoices("MIP_TRUST_FORWARDED_HEADERS", "TRUST_FORWARDED_HEADERS"),
    )

    # Slice-6 TTL cache: short-window memoization on gold-layer reads that
    # tolerate staleness (segments count, portfolio preview, lead queue,
    # borrower dossier projection). Fresh Lakebase workflow state is still
    # hydrated after cached gold reads, and audit/outreach writes never use
    # the cache. Set to 0 to disable caching entirely (tests do this).
    # Five minutes covers the sustained warm-load harness including its
    # pre-measurement cache warmup while staying much shorter than the
    # warehouse gold-refresh cadence.
    mip_cache_ttl_s: float = 300.0
    # Live capability checks can execute real Databricks work (including a
    # bounded AI Gateway inference + exact proof-ledger lookup). Cache the
    # admin request-scoped live snapshot briefly so repeated page refreshes do
    # not spam billable endpoints. Set to 0 to disable for tests/debugging.
    mip_live_capability_probe_ttl_s: float = 60.0
    # Optional refresh-ahead interval for the default lead page (the slowest
    # hot-path query, 3.6-6.6s cold). backend.main warms the default
    # `/api/leads` list + count at startup, then re-warms every this-many
    # seconds only when the value is positive. MUST stay below
    # `mip_cache_ttl_s` when enabled. Deployed Apps set this to 0 for idle
    # cost control; operators can temporarily enable it for staffed demos.
    mip_leads_warm_interval_s: float = 0.0
    # Shorter TTL for Lakebase sales workflow read-through state (assignment,
    # disposition, approval rollups). Mutating sales-state paths clear this
    # process-local cache immediately; the TTL covers out-of-band updates.
    mip_sales_state_cache_ttl_s: float = 600.0

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
    mip_rum_enabled: bool = False
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
    # The default 20-user Locust profile produces roughly 270 lead/segment
    # reads per minute under warm load. Keep the app-side token bucket above
    # that supported release gate while the warehouse semaphore still caps
    # simultaneous SQL work.
    mip_rate_limit_expensive_per_minute: int = 360
    mip_rate_limit_mutation_per_minute: int = 120
    mip_rate_limit_genie_per_minute: int = 30
    mip_rate_limit_telemetry_per_minute: int = 1200
    mip_warehouse_concurrency_limit: int = 24
    mip_lakebase_concurrency_limit: int = 16
    # Six concurrent Genie turns covers demo-panel usage without letting
    # LLM calls swamp the warehouse/Lakebase lanes. Customer targets can
    # raise this with MIP_GENIE_CONCURRENCY_LIMIT after quota review.
    mip_genie_concurrency_limit: int = 6
    # Genie routing posture (env MIP_GENIE_LIVE_FIRST). True is the PRODUCT
    # posture: every guardrail-passing question goes to LIVE Genie first so the
    # answer is genuinely generated, not scripted. The reviewed deterministic
    # canonical answers are then consulted ONLY as an honest degraded-mode
    # fallback -- when the Genie circuit breaker is open or a live turn raises a
    # dependency-down error -- and those fallback answers disclose that live
    # Genie was unavailable. False is the LEGACY / EMERGENCY booth posture:
    # the canonical interceptors are consulted BEFORE live Genie so an offline
    # or rate-limited booth still answers demo-typical questions deterministically
    # with zero LLM latency. No other behavior differs between the two settings.
    mip_genie_live_first: bool = True
    # Lakebase connection pooling. Connections use short-lived workspace
    # OAuth credentials in Databricks Apps, so reuse is bounded by a max
    # lifetime comfortably below the token expiry. The default max size
    # intentionally matches mip_lakebase_concurrency_limit so pool checkout
    # does not bottleneck before the app-side semaphore. Set max size to 0
    # to force one-connection-per-call behavior for local diagnostics.
    mip_lakebase_pool_max_size: int = Field(default=16, ge=0)
    mip_lakebase_pool_timeout_s: float = Field(
        default=2.0,
        ge=0,
        allow_inf_nan=False,
    )
    mip_lakebase_pool_max_lifetime_s: float = Field(
        default=3000.0,
        gt=0,
        allow_inf_nan=False,
    )
    mip_lakebase_connect_timeout_s: int = Field(default=2, ge=1)
    mip_lakebase_transport_timeout_s: int = Field(default=2, ge=1)
    mip_lakebase_health_statement_timeout_s: float = Field(
        default=2.0,
        gt=0,
        allow_inf_nan=False,
    )
    # Authenticated health callers wait against one request-level deadline.
    # Keep it above each Lakebase I/O phase ceiling so a contended but healthy
    # connection is not mislabeled down; late work still updates the health
    # cache after an individual caller stops waiting.
    mip_health_cold_wait_budget_s: float = Field(
        default=3.0,
        gt=0,
        allow_inf_nan=False,
    )

    @model_validator(mode="after")
    def _validate_public_lender_identity(self) -> Settings:
        self.mip_lender_name, self.mip_lender_nmls_id = validate_public_lender_identity(
            self.mip_lender_name,
            self.mip_lender_nmls_id,
        )
        if self.mip_tenant_id is not None:
            self.mip_tenant_id = effective_public_tenant_id(
                self.mip_tenant_id,
                lender_name=self.mip_lender_name,
            )
        return self

    @model_validator(mode="after")
    def _validate_health_wait_budget(self) -> Settings:
        dependency_deadline_floor = max(
            float(self.mip_lakebase_pool_timeout_s),
            float(self.mip_lakebase_connect_timeout_s),
            float(self.mip_lakebase_transport_timeout_s),
            self.mip_lakebase_health_statement_timeout_s,
        )
        if self.mip_health_cold_wait_budget_s <= dependency_deadline_floor:
            raise ValueError(
                "mip_health_cold_wait_budget_s must be strictly greater than "
                "the Lakebase pool, connect, transport, and health-statement timeouts"
            )
        return self

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
        if (
            not host
            or not warehouse
            or is_placeholder_databricks_config(host=host, warehouse_id=warehouse)
        ):
            raise RuntimeError(_MISSING_CREDS_MSG)
        # Normalise host shape: strip trailing slash, ensure scheme.
        if not host.startswith("http"):
            host = "https://" + host
        host = host.rstrip("/")

        if self.databricks_token is not None:
            literal = self.databricks_token.get_secret_value()
            if is_placeholder_databricks_config(token=literal):
                raise RuntimeError(_MISSING_CREDS_MSG)
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
    if os.environ.get("MIP_DISABLE_DOTENV", "").strip() == "1":
        # `_env_file` is a pydantic-settings runtime init control; its generated
        # static signature does not expose the keyword.
        return Settings(_env_file=None)  # type: ignore[call-arg]
    return Settings()


settings = get_settings()
set_public_lender_name_provider(lambda: settings.mip_lender_name)

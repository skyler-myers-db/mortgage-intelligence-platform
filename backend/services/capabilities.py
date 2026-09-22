"""Capability probe for the DAIS 2026 agentic build.

The Mortgage Growth Agent stack can layer several Databricks capabilities, each
at a different maturity. Some are generally available but still require concrete
workspace configuration before MIP may claim them live (Genie Conversation API,
metric-view certification, UC-registered tools, Mosaic Agent Framework /
Agent Bricks style orchestration, per-endpoint AI Gateway, Lakebase synced
tables). Others are Public Preview, Beta, or have no public API at all (Genie
Ontology, CustomerLake, App Spaces / serverless micro-apps, Lakehouse//RT,
declarative Genie Agents, UC Glossary / Domains).

This module computes — at startup and on demand — an HONEST snapshot of what is
actually provisioned in the *running* workspace, so the product never claims a
capability it cannot back with a real dependency. It is the enforcement point
for the no-overclaim posture: a feature flag turned on without the backing
library or credentials resolves to ``not_provisioned`` (an honest "roadmap /
not provisioned" chip), and preview-only capabilities resolve to
``preview_mirror`` (clearly labelled roadmap) or ``hidden`` — NEVER to an
"integrated" claim. The default snapshot is cheap and dependency-free. Request
handlers may opt into conservative live probes, which can only upgrade rows
when the exact deployed dependency responds.

The capability row shapes live in ``capabilities_models``; the bounded live
workspace probes live in ``capabilities_live_probes``. This module keeps
discovery -- configuration presence checks, the status shaping that folds a
live result into a row, and the one public snapshot API -- and re-exports the
models and the live-probe entry point, so ``backend.services.capabilities``
stays the single import surface for every caller.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
from functools import lru_cache
from pathlib import Path

from backend.config.settings import Settings, get_settings
from backend.services.capabilities_live_probes import (
    collect_live_capability_statuses as collect_live_capability_statuses,
)
from backend.services.capabilities_models import (
    _CLAIMABLE as _CLAIMABLE,
)
from backend.services.capabilities_models import (
    Capability,
    CapabilityStatus,
    LiveCapabilityMap,
    _configured_csv,
)
from backend.services.capabilities_models import (
    LiveCapabilityStatus as LiveCapabilityStatus,
)


def _module_present(name: str) -> bool:
    """True if ``name`` is importable without importing it."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def _version_at_least(distribution: str, minimum: tuple[int, ...]) -> bool:
    """True if the installed ``distribution`` version >= ``minimum``."""
    try:
        raw = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return False
    parts: list[int] = []
    for token in raw.split(".")[: len(minimum)]:
        digits = "".join(ch for ch in token if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) >= minimum


def _warehouse_configured(settings: Settings) -> bool:
    """True when live warehouse creds appear present (not placeholders)."""
    from backend.config.settings import is_placeholder_databricks_config

    host = settings.databricks_host
    warehouse = settings.databricks_warehouse_id
    if not host or not warehouse:
        return False
    return not is_placeholder_databricks_config(host=host, warehouse_id=warehouse)


def _lakebase_configured(settings: Settings) -> bool:
    return bool(settings.lakebase_host and settings.lakebase_user)


def _genie_space_configured(settings: Settings) -> bool:
    raw = (settings.genie_space_id or "").strip()
    if not raw:
        return False
    normalized = raw.lower()
    return "placeholder" not in normalized and not ("<" in raw and ">" in raw)


def _preview_status(mirror_on: bool) -> CapabilityStatus:
    return CapabilityStatus.PREVIEW_MIRROR if mirror_on else CapabilityStatus.HIDDEN


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _certified_metric_view_contract_present() -> bool:
    metric_dir = _repo_root() / "sql" / "metric_views"
    if not metric_dir.exists():
        return False
    expected = {
        "certified_borrower_opportunity_metric_view",
        "certified_lead_generation_metric_view",
        "certified_segment_performance_metric_view",
    }
    for path in metric_dir.glob("*.sql"):
        try:
            text = path.read_text(encoding="utf-8").lower()
        except OSError:
            continue
        if path.stem.lower() in expected and "with metrics" in text and "certification" in text:
            expected.remove(path.stem.lower())
    return not expected


def _uc_agent_tool_contract_present() -> bool:
    functions_dir = _repo_root() / "sql" / "uc_functions"
    if not functions_dir.exists():
        return False
    expected = {"fn_build_cohort", "fn_segment_counts", "fn_lead_queue_url"}
    found: set[str] = set()
    for path in functions_dir.glob("*.sql"):
        name = path.stem.lower()
        if name in expected:
            found.add(name)
    return expected <= found


def _agent_eval_contract_present(settings: Settings) -> bool:
    if not settings.mip_agent_eval_experiment:
        return False
    eval_dir = _repo_root() / "tests" / "eval"
    return (eval_dir / "golden_agent_cases.jsonl").exists() and (eval_dir / "scorers.py").exists()


def _status_from_live(
    *,
    key: str,
    configured: bool,
    configured_detail: str,
    not_provisioned_detail: str,
    live_statuses: LiveCapabilityMap | None,
) -> tuple[CapabilityStatus, str]:
    if not configured:
        return CapabilityStatus.NOT_PROVISIONED, not_provisioned_detail
    live = live_statuses.get(key) if live_statuses else None
    if live is None:
        return CapabilityStatus.CONFIGURED, configured_detail
    if live.available:
        return CapabilityStatus.AVAILABLE, live.detail
    return (
        CapabilityStatus.CONFIGURED,
        f"{configured_detail} Live probe did not pass: {live.detail}",
    )


def probe_capabilities(
    settings: Settings | None = None,
    *,
    live_statuses: LiveCapabilityMap | None = None,
) -> list[Capability]:
    """Return the honest capability snapshot for the running workspace."""
    s = settings or get_settings()

    sdk = _module_present("databricks.sdk")
    warehouse = _warehouse_configured(s)
    genie_configured = _genie_space_configured(s)
    mirror = s.mip_preview_mirror
    certified_metric_contract = _certified_metric_view_contract_present()
    uc_tool_contract = _uc_agent_tool_contract_present()
    agent_eval_contract = _agent_eval_contract_present(s)
    genie_status, genie_detail = _status_from_live(
        key="genie_conversation_api",
        configured=sdk and warehouse and genie_configured,
        configured_detail=(
            "Genie Conversation API dependencies are configured; a live Genie "
            "probe must pass before this row is claimable."
        ),
        not_provisioned_detail="Needs databricks-sdk, warehouse creds, and a Genie space id.",
        live_statuses=live_statuses,
    )
    genie_viz_status, genie_viz_detail = _status_from_live(
        key="genie_native_visualization",
        configured=sdk and warehouse and genie_configured,
        configured_detail=(
            "Genie returns a native-visualization attachment in-band; the Beta "
            "download endpoint must return content in a live probe before this "
            "row is claimable."
        ),
        not_provisioned_detail="Needs databricks-sdk, warehouse creds, and a Genie space id.",
        live_statuses=live_statuses,
    )
    certified_status, certified_detail = _status_from_live(
        key="certified_metric_views",
        configured=warehouse and certified_metric_contract,
        configured_detail=(
            "Certified metric-view SQL contracts are bundled; live UC deployment "
            "must be verified before claiming them active."
        ),
        not_provisioned_detail="Metric views exist, but certification metadata/probe is not provisioned.",
        live_statuses=live_statuses,
    )
    uc_tool_status, uc_tool_detail = _status_from_live(
        key="uc_function_tools",
        configured=warehouse and uc_tool_contract,
        configured_detail=(
            "Reviewed UC-function SQL contracts are bundled; live registration "
            "must be verified before claiming them active."
        ),
        not_provisioned_detail=(
            "Growth workflows use reviewed in-app SQL tools; UC-function agent "
            "tools are not registered."
        ),
        live_statuses=live_statuses,
    )
    eval_status, eval_detail = _status_from_live(
        key="agent_eval",
        configured=agent_eval_contract,
        configured_detail=(
            "Golden-case Agent Evaluation assets are configured, but no live "
            "Agent Evaluation run has been verified for this deployment."
        ),
        not_provisioned_detail=(
            "Needs a configured Databricks experiment and golden eval cases."
        ),
        live_statuses=live_statuses,
    )

    caps: list[Capability] = []

    # --- GA, usable today --------------------------------------------------
    caps.append(
        Capability(
            key="genie_conversation_api",
            label="Genie Conversation API",
            ga=True,
            status=genie_status,
            detail=genie_detail,
        )
    )
    # ga=False -> the UI renders a "· preview" suffix. The Beta download
    # endpoint that would make native visualizations fully consumable is not
    # rolled out on this workspace, so this can only reach AVAILABLE when a
    # live probe actually downloads a viz attachment.
    caps.append(
        Capability(
            key="genie_native_visualization",
            label="Genie native visualizations",
            ga=False,
            status=genie_viz_status,
            detail=genie_viz_detail,
        )
    )
    caps.append(
        Capability(
            key="certified_metric_views",
            label="UC metric-view certification",
            ga=True,
            status=certified_status,
            detail=certified_detail,
        )
    )
    caps.append(
        Capability(
            key="uc_function_tools",
            label="Application-reviewed SQL tools",
            ga=True,
            status=uc_tool_status,
            detail=uc_tool_detail,
        )
    )
    caps.append(
        Capability(
            key="agent_eval",
            label="MLflow Agent Evaluation",
            ga=True,
            status=eval_status,
            detail=eval_detail,
        )
    )

    # --- GA capability, gated behind a feature flag ------------------------
    orchestrator_configured = bool(
        s.mip_agent_orchestrator
        and s.mip_agent_supervisor_id
        and s.mip_agent_serving_endpoint
    )
    if not s.mip_agent_orchestrator:
        orchestrator_status = CapabilityStatus.NOT_PROVISIONED
        orchestrator_detail = "Disabled (MIP_AGENT_ORCHESTRATOR off)."
    elif orchestrator_configured:
        orchestrator_status, orchestrator_detail = _status_from_live(
            key="agent_orchestrator",
            configured=True,
            configured_detail=(
                "Agent id metadata and an Agent Responses serving endpoint are configured; "
                "an exact live endpoint probe must pass before this row is claimable."
            ),
            not_provisioned_detail="Agent id metadata or serving endpoint missing.",
            live_statuses=live_statuses,
        )
    else:
        orchestrator_status = CapabilityStatus.NOT_PROVISIONED
        orchestrator_detail = (
            "Flag on, but MIP_AGENT_SUPERVISOR_ID and MIP_AGENT_SERVING_ENDPOINT "
            "are not both configured."
        )
    caps.append(
        Capability(
            key="agent_orchestrator",
            label="Agent Responses orchestration",
            ga=True,
            status=orchestrator_status,
            detail=orchestrator_detail,
        )
    )

    gateway_configured = bool(
        s.mip_ai_gateway and s.mip_ai_gateway_endpoint and s.mip_ai_gateway_inference_table
    )
    if not s.mip_ai_gateway:
        gateway_status = CapabilityStatus.NOT_PROVISIONED
        gateway_detail = "Disabled (MIP_AI_GATEWAY off)."
    elif sdk and gateway_configured:
        gateway_status, gateway_detail = _status_from_live(
            key="ai_gateway",
            configured=True,
            configured_detail=(
                "AI Gateway endpoint and inference-table config are present; a live "
                "serving endpoint probe plus fresh proof-ledger exact row must pass "
                "before this row is claimable."
            ),
            not_provisioned_detail="Gateway endpoint or inference-table config missing.",
            live_statuses=live_statuses,
        )
    else:
        gateway_status = CapabilityStatus.NOT_PROVISIONED
        gateway_detail = (
            "Flag on, but gateway endpoint/inference-table config or warehouse/sdk is missing."
        )
    caps.append(
        Capability(
            key="ai_gateway",
            label="Unity AI Gateway governance",
            ga=True,
            status=gateway_status,
            detail=gateway_detail,
        )
    )

    sync_tables = _configured_csv(s.mip_lakebase_sync_tables)
    sync_configured = bool(
        s.mip_lakebase_sync
        and s.mip_lakebase_sync_catalog
        and s.mip_lakebase_sync_schema
        and sync_tables
    )
    if not s.mip_lakebase_sync:
        sync_status = CapabilityStatus.NOT_PROVISIONED
        sync_detail = "Disabled (MIP_LAKEBASE_SYNC off); reads stay on the warehouse path."
    elif sdk and warehouse and sync_configured:
        sync_status, sync_detail = _status_from_live(
            key="lakebase_sync",
            configured=True,
            configured_detail=(
                "Lakebase synced-table serving config is present; live synced-table "
                "row-count probes must pass before this row is claimable."
            ),
            not_provisioned_detail="Synced-table catalog/schema/table config missing.",
            live_statuses=live_statuses,
        )
    else:
        sync_status = CapabilityStatus.NOT_PROVISIONED
        sync_detail = (
            "Flag on, but synced-table catalog/schema/table config, warehouse creds, "
            "or databricks-sdk is missing."
        )
    caps.append(
        Capability(
            key="lakebase_sync",
            label="Lakebase synced-table serving",
            ga=True,
            status=sync_status,
            detail=sync_detail,
        )
    )

    # --- Preview / no-public-API: mirror-the-pattern, never "integrated" ----
    preview = _preview_status(mirror)
    caps.extend(
        Capability(key=key, label=label, ga=False, status=preview, detail=detail)
        for key, label, detail in (
            (
                "genie_ontology",
                "Genie Ontology",
                "Public Preview. Grounded today via certified metric views (GA); "
                "ontology features tracked as roadmap.",
            ),
            (
                "customerlake",
                "CustomerLake (Agentic CDP)",
                "Private Preview, no public API. MIP mirrors the pattern as the "
                "mortgage-vertical expression; not integrated.",
            ),
            (
                "app_spaces_microapps",
                "App Spaces / serverless micro-apps",
                "Private previews 'coming soon'. Module boundaries designed for a "
                "future split; not shipped.",
            ),
            (
                "lakehouse_rt",
                "Lakehouse//RT",
                "Beta (read-only). Serving abstraction is the future swap point; "
                "not integrated.",
            ),
            (
                "declarative_genie_agents",
                "Declarative Genie Agents",
                "No declarative authoring API. Authored in UI / consumed via "
                "Conversation API + serving endpoints.",
            ),
            (
                "uc_glossary_domains",
                "UC Glossary / Domains",
                "Glossary 'coming soon'; Domains UI-only Public Preview. Interim "
                "meaning lives in metric-view synonyms.",
            ),
        )
    )

    return caps


@lru_cache(maxsize=1)
def _cached_snapshot() -> tuple[Capability, ...]:
    return tuple(probe_capabilities())


def get_capabilities_snapshot(*, refresh: bool = False) -> list[Capability]:
    """Return the cached capability snapshot (process-local).

    The snapshot is derived from installed modules + settings, both fixed for
    the process lifetime, so caching is safe. ``refresh=True`` recomputes (used
    by tests that monkeypatch settings).
    """
    if refresh:
        _cached_snapshot.cache_clear()
    return list(_cached_snapshot())

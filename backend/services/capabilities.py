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
"integrated" claim.

The probe is intentionally cheap and side-effect free: it inspects installed
modules (via :func:`importlib.util.find_spec`, which does not import them),
package versions, and configured settings. It performs no network calls, so it
is safe to call from a request handler and from tests without live creds.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path

from backend.config.settings import Settings, get_settings


class CapabilityStatus(str, Enum):
    """Honest maturity/availability state for a single capability."""

    #: GA underlying capability + backing dependency present + configured.
    AVAILABLE = "available"
    #: Flag/credential on and dependency present, but not yet exercised.
    CONFIGURED = "configured"
    #: GA-capable but the backing dependency or credential is missing, OR
    #: the gating feature flag is off. Renders as an honest "not provisioned".
    NOT_PROVISIONED = "not_provisioned"
    #: Preview / no-public-API capability, shown only as a labelled roadmap
    #: pattern (mirror flag on). Never "integrated".
    PREVIEW_MIRROR = "preview_mirror"
    #: Preview capability, mirror flag off -> hidden from the product.
    HIDDEN = "hidden"


# Statuses the product MAY present as an active, working capability. Configured
# means the local/deploy contract exists but has not been live-probed; it must
# render as "configured" rather than "working".
_CLAIMABLE = frozenset({CapabilityStatus.AVAILABLE})


@dataclass(frozen=True)
class Capability:
    """One row of the capability snapshot."""

    key: str
    label: str
    #: Is the underlying Databricks capability Generally Available?
    ga: bool
    status: CapabilityStatus
    detail: str

    @property
    def claimable(self) -> bool:
        """True only when the product may present this as a live capability."""
        return self.status in _CLAIMABLE

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "ga": self.ga,
            "status": self.status.value,
            "claimable": self.claimable,
            "detail": self.detail,
        }


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


def probe_capabilities(settings: Settings | None = None) -> list[Capability]:
    """Return the honest capability snapshot for the running workspace."""
    s = settings or get_settings()

    sdk = _module_present("databricks.sdk")
    mlflow_ok = _version_at_least("mlflow", (3, 1, 3))
    agents_lib = _module_present("databricks.agents")
    warehouse = _warehouse_configured(s)
    lakebase = _lakebase_configured(s)
    genie_configured = _genie_space_configured(s)
    mirror = s.mip_preview_mirror
    certified_metric_contract = _certified_metric_view_contract_present()
    uc_tool_contract = _uc_agent_tool_contract_present()
    agent_eval_contract = _agent_eval_contract_present(s)

    caps: list[Capability] = []

    # --- GA, usable today --------------------------------------------------
    caps.append(
        Capability(
            key="genie_conversation_api",
            label="Genie Conversation API",
            ga=True,
            status=(
                CapabilityStatus.CONFIGURED
                if sdk and warehouse and genie_configured
                else CapabilityStatus.NOT_PROVISIONED
            ),
            detail=(
                "Genie Conversation API dependencies are configured; a live "
                "Genie probe must pass before this row is claimable."
                if sdk and warehouse and genie_configured
                else "Needs databricks-sdk, warehouse creds, and a Genie space id."
            ),
        )
    )
    caps.append(
        Capability(
            key="certified_metric_views",
            label="UC metric-view certification",
            ga=True,
            status=CapabilityStatus.CONFIGURED if warehouse and certified_metric_contract else CapabilityStatus.NOT_PROVISIONED,
            detail=(
                "Certified metric-view SQL contracts are bundled; live UC deployment must be verified before claiming them active."
                if warehouse and certified_metric_contract
                else "Metric views exist, but certification metadata/probe is not provisioned."
            ),
        )
    )
    caps.append(
        Capability(
            key="uc_function_tools",
            label="Application-reviewed SQL tools",
            ga=True,
            status=CapabilityStatus.CONFIGURED if warehouse and uc_tool_contract else CapabilityStatus.NOT_PROVISIONED,
            detail=(
                "Reviewed UC-function SQL contracts are bundled; live registration must be verified before claiming them active."
                if warehouse and uc_tool_contract
                else "Growth workflows use reviewed in-app SQL tools; UC-function agent tools are not registered."
            ),
        )
    )
    caps.append(
        Capability(
            key="agent_eval",
            label="MLflow Agent Evaluation",
            ga=True,
            status=CapabilityStatus.AVAILABLE if mlflow_ok and agent_eval_contract else CapabilityStatus.NOT_PROVISIONED,
            detail=(
                "Configured MLflow GenAI eval experiment with golden agent cases."
                if mlflow_ok and agent_eval_contract
                else "Needs mlflow>=3.1.3 plus configured experiment and golden eval cases."
            ),
        )
    )

    # --- GA capability, gated behind a feature flag ------------------------
    if not s.mip_agent_orchestrator:
        orchestrator_status = CapabilityStatus.NOT_PROVISIONED
        orchestrator_detail = "Disabled (MIP_AGENT_ORCHESTRATOR off)."
    elif mlflow_ok and agents_lib and warehouse:
        orchestrator_status = CapabilityStatus.AVAILABLE
        orchestrator_detail = "Multi-agent Mortgage Growth Agent over governed tools."
    else:
        orchestrator_status = CapabilityStatus.NOT_PROVISIONED
        orchestrator_detail = "Flag on, but mlflow>=3.1.3 / databricks-agents / warehouse missing."
    caps.append(
        Capability(
            key="agent_orchestrator",
            label="Agent Framework orchestration",
            ga=True,
            status=orchestrator_status,
            detail=orchestrator_detail,
        )
    )

    if not s.mip_ai_gateway:
        gateway_status = CapabilityStatus.NOT_PROVISIONED
        gateway_detail = "Disabled (MIP_AI_GATEWAY off)."
    elif sdk and warehouse and s.mip_ai_gateway_endpoint and s.mip_ai_gateway_inference_table:
        gateway_status = CapabilityStatus.NOT_PROVISIONED
        gateway_detail = "Gateway endpoint and inference-table config are present, but the live signal probe is not implemented."
    else:
        gateway_status = CapabilityStatus.NOT_PROVISIONED
        gateway_detail = "Flag on, but gateway endpoint/inference-table config or warehouse/sdk is missing."
    caps.append(
        Capability(
            key="ai_gateway",
            label="Unity AI Gateway governance",
            ga=True,
            status=gateway_status,
            detail=gateway_detail,
        )
    )

    if not s.mip_lakebase_sync:
        sync_status = CapabilityStatus.NOT_PROVISIONED
        sync_detail = "Disabled (MIP_LAKEBASE_SYNC off); reads stay on the warehouse path."
    elif lakebase:
        sync_status = CapabilityStatus.CONFIGURED
        sync_detail = "Hot gold aggregates served low-latency from synced Lakebase tables."
    else:
        sync_status = CapabilityStatus.NOT_PROVISIONED
        sync_detail = "Flag on, but Lakebase creds missing."
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

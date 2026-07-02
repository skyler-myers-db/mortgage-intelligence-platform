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
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.config.settings import Settings, get_settings
from backend.services.ai_gateway_capability_probe import probe_ai_gateway
from backend.services.capability_serving_probes import (
    query_serving_endpoint,
    serving_response_has_payload,
)
from backend.services.databricks_sql_helpers import _validate_identifier, qualify


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


@dataclass(frozen=True)
class LiveCapabilityStatus:
    """Result of a bounded live capability probe."""

    available: bool
    detail: str


LiveCapabilityMap = Mapping[str, LiveCapabilityStatus]
MIN_GROWTH_AGENT_EVAL_CASES = 5
_AI_GATEWAY_CAPABILITY_REQUEST_PREFIX = "mip-capability-"
_AI_GATEWAY_EXACT_LOG_WAIT_S = 90.0
_AI_GATEWAY_EXACT_LOG_ATTEMPTS = 1


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


def _configured_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _enum_value(value: Any) -> str:
    """Return SDK enum values as their wire value for stable comparisons."""

    raw = getattr(value, "value", value)
    return str(raw or "")


def _synced_table_is_ready(state: str) -> bool:
    normalized = state.upper()
    return "ONLINE" in normalized or normalized.endswith("NO_PENDING_UPDATE")


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
                "Supervisor agent id and serving endpoint are configured; a live "
                "serving endpoint probe must pass before this row is claimable."
            ),
            not_provisioned_detail="Supervisor agent id or serving endpoint missing.",
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
            label="Agent Framework orchestration",
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


def collect_live_capability_statuses(
    *,
    settings: Settings | None = None,
    sql_client: Any | None = None,
    genie_client: Any | None = None,
    lakebase: Any | None = None,
    workspace_client: Any | None = None,
) -> dict[str, LiveCapabilityStatus]:
    """Run bounded live probes for capabilities that can be live-proven.

    The probes are intentionally conservative. They only upgrade a row to
    ``available`` when the exact deployed dependencies respond to a functional
    check. They never write MIP business state, but some checks intentionally
    create provider-side proof artifacts (for example a Genie conversation turn
    or a bounded serving endpoint query). AI Gateway row proof is read from the
    deployment verifier's Lakebase ledger, not written by runtime probes. Any
    exception is captured as a non-claimable ``configured`` row instead of
    failing the admin surface or implying the dependency works.
    """

    statuses: dict[str, LiveCapabilityStatus] = {}
    if genie_client is not None:
        statuses["genie_conversation_api"] = _probe_genie(genie_client)
    if sql_client is not None:
        statuses["certified_metric_views"] = _probe_metric_views(sql_client)
        statuses["uc_function_tools"] = _probe_uc_functions(sql_client)
    s = settings or get_settings()
    if s.mip_lakebase_sync and sql_client is not None:
        statuses["lakebase_sync"] = _probe_lakebase_synced_tables(
            sql_client=sql_client,
            workspace_client=workspace_client,
            settings=s,
        )
    if workspace_client is not None and s.mip_agent_eval_experiment:
        statuses["agent_eval"] = _probe_agent_eval(workspace_client, s)
    if workspace_client is not None and s.mip_agent_orchestrator:
        statuses["agent_orchestrator"] = _probe_agent_orchestrator(workspace_client, s)
    if workspace_client is not None and s.mip_ai_gateway:
        statuses["ai_gateway"] = _probe_ai_gateway(
            workspace_client,
            s,
            sql_client=sql_client,
            lakebase=lakebase,
        )
    return statuses


def _probe_genie(genie_client: Any) -> LiveCapabilityStatus:
    try:
        ask = getattr(genie_client, "ask", None)
        if not callable(ask):
            return LiveCapabilityStatus(
                False,
                "Genie client does not expose a Conversation API turn probe.",
            )
        response = ask(
            "Capability readiness check: reply with one short sentence about the Mortgage Lead Intelligence trusted assets."
        )
    except Exception as exc:  # noqa: BLE001 - probe must not fail the surface
        return LiveCapabilityStatus(False, f"Genie Conversation API turn raised {type(exc).__name__}.")
    conversation_id = str(getattr(response, "conversation_id", "") or "").strip()
    message_id = str(getattr(response, "message_id", "") or "").strip()
    if conversation_id and message_id:
        return LiveCapabilityStatus(
            True,
            "Live Genie Conversation API turn completed for this workspace.",
        )
    return LiveCapabilityStatus(False, "Genie turn did not return a conversation and message id.")


def _probe_metric_views(sql_client: Any) -> LiveCapabilityStatus:
    assets = (
        qualify("semantics", "certified_lead_generation_metric_view"),
        qualify("semantics", "certified_segment_performance_metric_view"),
        qualify("semantics", "certified_borrower_opportunity_metric_view"),
    )
    try:
        for asset in assets:
            sql_client.execute(f"SELECT 1 AS ok FROM {asset} LIMIT 1")
    except Exception as exc:  # noqa: BLE001 - dependency details stay internal
        return LiveCapabilityStatus(False, f"Metric-view query failed ({type(exc).__name__}).")
    return LiveCapabilityStatus(True, "Live UC metric-view probes passed for all certified assets.")


def _probe_uc_functions(sql_client: Any) -> LiveCapabilityStatus:
    build = qualify("gold", "fn_build_cohort")
    counts = qualify("gold", "fn_segment_counts")
    route = qualify("gold", "fn_lead_queue_url")
    try:
        sql_client.execute(f"SELECT {build}(array('itm'), 'any', array()) AS n")
        sql_client.execute(f"SELECT {counts}(array('itm'), 'any', array()) AS n")
        sql_client.execute(f"SELECT {route}(array('itm'), 'any', array()) AS route")
    except Exception as exc:  # noqa: BLE001 - dependency details stay internal
        return LiveCapabilityStatus(False, f"UC-function probe failed ({type(exc).__name__}).")
    return LiveCapabilityStatus(
        True, "Live UC function probes passed for all reviewed Growth Agent tools."
    )


def _probe_lakebase_synced_tables(
    *,
    sql_client: Any,
    workspace_client: Any | None,
    settings: Settings,
) -> LiveCapabilityStatus:
    catalog = settings.mip_lakebase_sync_catalog
    schema = settings.mip_lakebase_sync_schema
    tables = _configured_csv(settings.mip_lakebase_sync_tables)
    if not (catalog and schema and tables):
        return LiveCapabilityStatus(False, "Synced-table catalog/schema/table config is incomplete.")
    if workspace_client is None:
        return LiveCapabilityStatus(False, "WorkspaceClient is required to verify synced-table metadata.")
    try:
        _validate_identifier("catalog", catalog)
        _validate_identifier("schema", schema)
        for table in tables:
            _validate_identifier("table", table)
    except ValueError as exc:
        return LiveCapabilityStatus(False, str(exc))
    metadata_permission_denied = False
    try:
        for table in tables:
            full_name = f"{catalog}.{schema}.{table}"
            try:
                synced = workspace_client.database.get_synced_database_table(full_name)
                status = synced.data_synchronization_status
                state = _enum_value(getattr(status, "detailed_state", ""))
                if not _synced_table_is_ready(state):
                    return LiveCapabilityStatus(False, f"{full_name} sync state is {state or 'unknown'}.")
            except Exception as exc:  # noqa: BLE001 - app SP may have SQL access but not Database API metadata
                if type(exc).__name__ != "PermissionDenied":
                    raise
                metadata_permission_denied = True
            row_count = _count_relation_rows(sql_client, full_name)
            if row_count <= 0:
                return LiveCapabilityStatus(
                    False,
                    f"{full_name} is online but returned zero rows.",
                )
    except Exception as exc:  # noqa: BLE001 - dependency details stay bounded
        return LiveCapabilityStatus(False, f"Lakebase synced-table probe failed ({type(exc).__name__}).")
    metadata_detail = (
        "; Database API metadata was not visible to the app service principal, so SQL row-count proof was used"
        if metadata_permission_denied
        else " with metadata state verification"
    )
    return LiveCapabilityStatus(
        True,
        f"Live Lakebase synced-table probes passed for {len(tables)} MIP-owned serving tables{metadata_detail}.",
    )


def _count_relation_rows(sql_client: Any, relation: str) -> int:
    rows = sql_client.execute(f"SELECT COUNT(*) AS row_count FROM {relation}")
    if not rows:
        return 0
    raw = rows[0].get("row_count")
    if raw is None:
        raw = rows[0].get("n")
    return int(raw or 0)


def _probe_agent_eval(workspace_client: Any, settings: Settings) -> LiveCapabilityStatus:
    experiment_name = (settings.mip_agent_eval_experiment or "").strip()
    if not experiment_name:
        return LiveCapabilityStatus(False, "MIP_AGENT_EVAL_EXPERIMENT is not configured.")
    try:
        experiment = workspace_client.experiments.get_by_name(experiment_name).experiment
        experiment_id = getattr(experiment, "experiment_id", None)
        if not experiment_id:
            return LiveCapabilityStatus(False, f"Experiment {experiment_name} has no id.")
        run_id = (settings.mip_agent_eval_run_id or "").strip()
        runs = []
        if run_id:
            runs = [workspace_client.experiments.get_run(run_id).run]
        else:
            runs = list(
                workspace_client.experiments.search_runs(
                    experiment_ids=[experiment_id],
                    filter="tags.mip_eval_type = 'growth_agent_golden'",
                    max_results=1,
                    order_by=["attributes.start_time DESC"],
                )
            )
        if not runs:
            return LiveCapabilityStatus(False, "No MIP growth-agent golden eval run found.")
        run = runs[0]
        info = getattr(run, "info", None)
        if run_id:
            run_experiment_id = str(getattr(info, "experiment_id", "") or "").strip()
            if not run_experiment_id:
                return LiveCapabilityStatus(False, f"Eval run {run_id} did not expose an experiment id.")
            if run_experiment_id != str(experiment_id):
                return LiveCapabilityStatus(
                    False,
                    f"Eval run {run_id} belongs to experiment {run_experiment_id}, not {experiment_id}.",
                )
        data = getattr(run, "data", None)
        metrics = {m.key: m.value for m in (getattr(data, "metrics", None) or [])}
        params = {p.key: p.value for p in (getattr(data, "params", None) or [])}
        tags = {t.key: t.value for t in (getattr(data, "tags", None) or [])}
        eval_type = str(tags.get("mip_eval_type") or "").strip()
        if eval_type != "growth_agent_golden":
            return LiveCapabilityStatus(False, "Latest eval run is not tagged as a MIP growth-agent golden eval.")
        genai_evaluate_used = str(tags.get("mip_mlflow_genai_evaluate") or "").strip().lower()
        if genai_evaluate_used != "true":
            return LiveCapabilityStatus(
                False,
                "Latest eval run did not execute mlflow.genai.evaluate.",
            )
        genai_tracking_uri = str(tags.get("mip_mlflow_genai_tracking_uri") or "").strip().lower()
        if not (genai_tracking_uri == "databricks" or genai_tracking_uri.startswith("databricks://")):
            return LiveCapabilityStatus(
                False,
                "Latest eval run did not use Databricks MLflow tracking.",
            )
        if str(tags.get("mip_mlflow_genai_databricks_run_verified") or "").strip().lower() != "true":
            return LiveCapabilityStatus(
                False,
                "Latest eval run did not verify the GenAI Evaluation run in Databricks MLflow.",
            )
        genai_run_id = str(params.get("mlflow_genai_evaluate_run_id") or "").strip()
        if not genai_run_id:
            return LiveCapabilityStatus(False, "Latest eval run has no mlflow.genai.evaluate run id.")
        try:
            genai_run = workspace_client.experiments.get_run(genai_run_id).run
        except Exception:  # noqa: BLE001 - surfaced as non-claimable proof.
            return LiveCapabilityStatus(
                False,
                f"GenAI Evaluation run {genai_run_id} is not resolvable in Databricks MLflow.",
            )
        genai_data = getattr(genai_run, "data", None)
        genai_metrics = {
            m.key: m.value for m in (getattr(genai_data, "metrics", None) or [])
        }
        genai_count_metric = None
        for key, value in genai_metrics.items():
            if "count_reconciles" not in str(key).lower() or "error" in str(key).lower():
                continue
            try:
                genai_count_metric = float(value)
            except (TypeError, ValueError):
                continue
            break
        parent_count_metric = float(metrics.get("mlflow_genai_count_reconciles_score", 0.0) or 0.0)
        if (genai_count_metric is None or genai_count_metric < 1.0) and parent_count_metric < 1.0:
            return LiveCapabilityStatus(
                False,
                "GenAI Evaluation run did not record a passing count_reconciles scorer metric.",
            )
        genai_info = getattr(genai_run, "info", None)
        genai_experiment_id = str(getattr(genai_info, "experiment_id", "") or "").strip()
        if genai_experiment_id and genai_experiment_id != str(experiment_id):
            return LiveCapabilityStatus(
                False,
                f"GenAI Evaluation run {genai_run_id} belongs to experiment {genai_experiment_id}, not {experiment_id}.",
            )
        score = float(metrics.get("score", 0.0) or 0.0)
        passed = int(float(metrics.get("passed", 0.0) or 0.0))
        total = int(float(metrics.get("total", 0.0) or 0.0))
        count_reconciles_passed = int(float(metrics.get("count_reconciles_passed", 0.0) or 0.0))
        sha = params.get("git_sha") or "unknown SHA"
        expected_sha = (settings.mip_git_sha or "").strip()
        if not expected_sha:
            return LiveCapabilityStatus(False, "MIP_GIT_SHA is required to prove Agent Evaluation matches this deployment.")
        if expected_sha and sha != expected_sha:
            return LiveCapabilityStatus(
                False,
                f"Latest eval run was for {sha}, not deployed SHA {expected_sha}.",
            )
        if total < MIN_GROWTH_AGENT_EVAL_CASES:
            return LiveCapabilityStatus(
                False,
                f"Latest eval run covered only {total} cases; minimum is {MIN_GROWTH_AGENT_EVAL_CASES}.",
            )
        if count_reconciles_passed < total:
            return LiveCapabilityStatus(
                False,
                f"Latest eval run only reconciled {count_reconciles_passed}/{total} actionability handoffs.",
            )
        if passed != total or score < 1.0:
            return LiveCapabilityStatus(False, f"Latest eval run did not pass ({passed}/{total}, score={score:.3f}).")
        return LiveCapabilityStatus(
            True,
            f"Live MLflow GenAI Evaluation passed ({passed}/{total}) for {sha}.",
        )
    except Exception as exc:  # noqa: BLE001
        return LiveCapabilityStatus(False, f"Agent Evaluation probe failed ({type(exc).__name__}).")


def _probe_agent_orchestrator(workspace_client: Any, settings: Settings) -> LiveCapabilityStatus:
    endpoint = (settings.mip_agent_serving_endpoint or "").strip()
    supervisor_id = (settings.mip_agent_supervisor_id or "").strip()
    if not endpoint or not supervisor_id:
        return LiveCapabilityStatus(False, "Supervisor id or serving endpoint is not configured.")
    try:
        details = workspace_client.serving_endpoints.get(endpoint)
        state = _enum_value(getattr(getattr(details, "state", None), "ready", None))
        task = getattr(details, "task", None)
        if state != "READY":
            return LiveCapabilityStatus(False, f"Agent endpoint {endpoint} is not READY ({state}).")
        if not _is_agent_responses_task(task):
            return LiveCapabilityStatus(False, f"Endpoint {endpoint} task is {task}, not agent.")
        response = query_serving_endpoint(
            workspace_client,
            endpoint,
            task=str(task),
            prompt=(
                "Capability readiness check. Reply with a one-sentence acknowledgement "
                "that the Mortgage Growth Agent endpoint is reachable."
            ),
        )
        if not serving_response_has_payload(response):
            return LiveCapabilityStatus(False, f"Agent endpoint {endpoint} returned no response payload.")
        return LiveCapabilityStatus(
            True,
            f"Live Supervisor Agent endpoint accepted a bounded query ({endpoint}, supervisor {supervisor_id}).",
        )
    except Exception as exc:  # noqa: BLE001
        return LiveCapabilityStatus(False, f"Agent Orchestrator probe failed ({type(exc).__name__}).")


def _probe_ai_gateway(
    workspace_client: Any,
    settings: Settings,
    *,
    sql_client: Any | None,
    lakebase: Any | None,
) -> LiveCapabilityStatus:
    return probe_ai_gateway(
        workspace_client,
        settings,
        sql_client=sql_client,
        lakebase=lakebase,
        make_status=LiveCapabilityStatus,
        request_prefix=_AI_GATEWAY_CAPABILITY_REQUEST_PREFIX,
        exact_log_wait_s=_AI_GATEWAY_EXACT_LOG_WAIT_S,
        exact_log_attempts=_AI_GATEWAY_EXACT_LOG_ATTEMPTS,
    )


def _is_agent_responses_task(task: Any) -> bool:
    raw = getattr(task, "value", task)
    normalized = str(raw or "").strip().lower()
    canonical = normalized.replace("-", "_").replace("/", "_")
    return canonical == "agent_v1_responses"


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

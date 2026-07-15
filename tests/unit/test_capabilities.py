"""Contract for the DAIS-2026 capability probe + ``/api/admin/capabilities``.

The probe is the enforcement point for the no-overclaim posture: a feature flag
turned on without its backing dependency must resolve to ``not_provisioned``
(claimable=False), and preview-only capabilities must resolve to
``preview_mirror``/``hidden`` (claimable=False) — never to an "integrated"
claim. These tests pin that behaviour against the real probe logic.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import backend.services.ai_gateway_capability_probe as ai_gateway_probe_module
import backend.services.capabilities as capabilities_module
import backend.services.capability_request as capability_request_module
from backend.config.settings import AI_GATEWAY_PROOF_FRESHNESS_MAX_S, Settings
from backend.main import app
from backend.services.ai_gateway_proof_ledger import AI_GATEWAY_PROOF_CLOCK_SKEW_S
from backend.services.capabilities import (
    CapabilityStatus,
    LiveCapabilityStatus,
    collect_live_capability_statuses,
    get_capabilities_snapshot,
    probe_capabilities,
)
from backend.services.capability_request import reset_live_capability_probe_cache
from backend.services.databricks_sql import get_sql_client
from backend.services.genie_client import get_genie_client
from backend.services.lakebase import get_lakebase_client
from backend.services.resilience import TTLCache

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEST_GIT_SHA = "69ff206fa7667589a28498c6554779f7f6c18c08"
_OTHER_GIT_SHA = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "databricks_host": "dbc-test.cloud.databricks.com",
        "databricks_warehouse_id": "wh-123",
        "genie_space_id": "space-abc",
        "lakebase_host": "lb-test",
        "lakebase_user": "mip_app",
    }
    base.update(overrides)
    return Settings(**base)


def _by_key(caps: list, key: str):
    return next(cap for cap in caps if cap.key == key)


class _LiveSqlClient:
    def __init__(
        self,
        *,
        fail: bool = False,
        count: int = 7,
        count_sequence: list[int] | None = None,
        count_by_request_id: dict[str, int] | None = None,
        table_names: list[str] | None = None,
        column_names: list[str] | None = None,
    ) -> None:
        self.fail = fail
        self.count = count
        self.count_sequence = list(count_sequence or [])
        self.count_by_request_id = dict(count_by_request_id or {})
        self.use_request_id_counts = count_by_request_id is not None
        self.table_names = (
            ["mip_agent_inference_payload"] if table_names is None else list(table_names)
        )
        self.column_names = (
            ["client_request_id", "request", "databricks_request_id"]
            if column_names is None
            else list(column_names)
        )
        self.count_calls = 0
        self.statements: list[str] = []
        self.parameters: list[object | None] = []

    def execute(self, statement: str, parameters: object | None = None) -> list[dict[str, object]]:
        self.statements.append(statement)
        self.parameters.append(parameters)
        if self.fail:
            raise RuntimeError("probe failed")
        if "system.information_schema.columns" in statement:
            return [{"column_name": column_name} for column_name in self.column_names]
        if "system.information_schema.tables" in statement:
            return [{"table_name": table_name} for table_name in self.table_names]
        if "COUNT(*) AS row_count" in statement:
            self.count_calls += 1
            if self.count_sequence:
                return [{"row_count": self.count_sequence.pop(0)}]
            if self.use_request_id_counts and isinstance(parameters, dict):
                request_id = str(parameters.get("client_request_id") or "")
                return [{"row_count": self.count_by_request_id.get(request_id, 0)}]
            return [{"row_count": self.count}]
        return [{"ok": 1}]


class _LiveGenieClient:
    def __init__(
        self,
        *,
        ok: bool = True,
        conversation_id: str = "conv-live",
        message_id: str | None = "msg-live",
        genie_status: str | None = "COMPLETED",
        native_visualization: dict[str, object] | None = None,
        download_ok: bool = False,
    ) -> None:
        self.ok = ok
        self.conversation_id = conversation_id
        self.message_id = message_id
        self.genie_status = genie_status
        self.native_visualization = native_visualization
        self.download_ok = download_ok
        self.download_calls: list[tuple[str, str, str]] = []

    def ask(self, question: str) -> object:
        _ = question
        if not self.ok:
            raise RuntimeError("genie unavailable")
        return SimpleNamespace(
            conversation_id=self.conversation_id,
            message_id=self.message_id,
            genie_status=self.genie_status,
            native_visualization=self.native_visualization,
        )

    def download_native_visualization(
        self,
        conversation_id: str,
        message_id: str,
        attachment_id: str,
    ) -> bool:
        self.download_calls.append((conversation_id, message_id, attachment_id))
        return self.download_ok


class _LiveLakebase:
    def __init__(self, proofs: list[dict[str, object]] | None = None) -> None:
        self.proofs = list(proofs or [])
        self.fetchone_calls: list[tuple[str, dict[str, object]]] = []

    @classmethod
    def verified(
        cls,
        git_sha: str = _TEST_GIT_SHA,
        *,
        endpoint_name: str = "mip-agent-gateway",
        inference_table: str = "mip_app_state.mip_sync.mip_agent_inference",
        verified_at: datetime | None = None,
        status: str = "verified",
    ) -> _LiveLakebase:
        sent = datetime.now(UTC) - timedelta(minutes=5)
        verified = verified_at or datetime.now(UTC) - timedelta(minutes=1)
        return cls(
            [
                {
                    "proof_id": "11111111-1111-4111-8111-111111111111",
                    "git_sha": git_sha,
                    "client_request_id": f"mip-capability-{git_sha}-abcdef1234567890",
                    "endpoint_name": endpoint_name,
                    "inference_table": inference_table,
                    "sent_at": sent,
                    "verified_at": verified,
                    "verify_latency_s": 240.0,
                    "status": status,
                }
            ]
        )

    def fetchone(
        self,
        sql: str,
        params: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        params = params or {}
        self.fetchone_calls.append((sql, params))
        if "FROM mip_app.ai_gateway_proof_ledger" not in sql:
            return None
        git_sha = str(params.get("git_sha") or "")
        endpoint_name = str(params.get("endpoint_name") or "")
        inference_table = str(params.get("inference_table") or "")
        cutoff = params.get("cutoff")
        future_cutoff = params.get("future_cutoff")
        clock_skew = timedelta(seconds=int(params.get("clock_skew_s") or 0))
        matches: list[dict[str, object]] = []
        for proof in self.proofs:
            if proof.get("git_sha") != git_sha or proof.get("status") != "verified":
                continue
            if proof.get("endpoint_name") != endpoint_name:
                continue
            if proof.get("inference_table") != inference_table:
                continue
            verified_at = proof.get("verified_at")
            if (
                isinstance(cutoff, datetime)
                and isinstance(verified_at, datetime)
                and verified_at < cutoff
            ):
                continue
            sent_at = proof.get("sent_at")
            if (
                isinstance(future_cutoff, datetime)
                and isinstance(verified_at, datetime)
                and verified_at > future_cutoff
            ):
                continue
            if (
                isinstance(future_cutoff, datetime)
                and isinstance(sent_at, datetime)
                and sent_at > future_cutoff
            ):
                continue
            if (
                isinstance(verified_at, datetime)
                and isinstance(sent_at, datetime)
                and verified_at < sent_at - clock_skew
            ):
                continue
            matches.append(dict(proof))
        matches.sort(
            key=lambda row: row.get("verified_at") or datetime.min.replace(tzinfo=UTC), reverse=True
        )
        return matches[0] if matches else None


class _SyncStatus:
    detailed_state = "SYNCED_TABLE_ONLINE_NO_PENDING_UPDATE"


class _SyncedTable:
    data_synchronization_status = _SyncStatus()


class _FakeDatabaseApi:
    def __init__(
        self,
        *,
        permission_denied: bool = False,
        metadata_error: Exception | None = None,
    ) -> None:
        self.requested: list[str] = []
        self.permission_denied = permission_denied
        self.metadata_error = metadata_error

    def get_synced_database_table(self, name: str) -> _SyncedTable:
        self.requested.append(name)
        if self.metadata_error is not None:
            raise self.metadata_error
        if self.permission_denied:

            class PermissionDenied(Exception):
                pass

            raise PermissionDenied("metadata denied")
        return _SyncedTable()


class _FakeWorkspaceClient:
    def __init__(
        self,
        *,
        permission_denied: bool = False,
        database_metadata_error: Exception | None = None,
        serving_ready: bool = True,
        serving_task: str = "agent/v1/responses",
        empty_serving_response: bool = False,
        supervisor_metadata_id: str = "supervisor-1",
        supervisor_metadata_endpoint: str = "mip-supervisor-endpoint",
        supervisor_metadata_error: Exception | None = None,
        inference_enabled: bool = True,
        inference_catalog: str = "mip_app_state",
        inference_schema: str = "mip_sync",
        inference_table_prefix: str = "mip_agent_inference",
        responses_api_error: Exception | None = None,
        serving_response_status: str = "completed",
        eval_total: int = 5,
        eval_passed: int | None = None,
        eval_score: float = 1.0,
        eval_sha: str = "sha-live",
        eval_tag: str = "growth_agent_golden",
        eval_genai_used: bool = True,
        eval_genai_tracking_uri: str = "databricks",
        eval_genai_run_verified: bool = True,
        eval_genai_run_resolvable: bool = True,
        eval_genai_run_id: str = "genai-run-1",
        eval_genai_run_experiment_id: str | None = None,
        eval_genai_count_reconciles_score: float = 1.0,
        eval_count_reconciles_passed: int | None = None,
        eval_experiment_id: str = "exp-1",
        eval_run_experiment_id: str | None = None,
    ) -> None:
        self.database = _FakeDatabaseApi(
            permission_denied=permission_denied,
            metadata_error=database_metadata_error,
        )
        self.api_client = _FakeApiClient(
            empty_response=empty_serving_response,
            error=responses_api_error,
            supervisor_id=supervisor_metadata_id,
            supervisor_endpoint=supervisor_metadata_endpoint,
            supervisor_error=supervisor_metadata_error,
            serving_response_status=serving_response_status,
        )
        self.serving_endpoints = _FakeServingEndpoints(
            ready=serving_ready,
            task=serving_task,
            empty_response=empty_serving_response,
            inference_enabled=inference_enabled,
            inference_catalog=inference_catalog,
            inference_schema=inference_schema,
            inference_table_prefix=inference_table_prefix,
        )
        self.experiments = _FakeExperiments(
            total=eval_total,
            passed=eval_passed if eval_passed is not None else eval_total,
            score=eval_score,
            sha=eval_sha,
            tag=eval_tag,
            genai_used=eval_genai_used,
            genai_tracking_uri=eval_genai_tracking_uri,
            genai_run_verified=eval_genai_run_verified,
            genai_run_resolvable=eval_genai_run_resolvable,
            genai_run_id=eval_genai_run_id,
            genai_run_experiment_id=eval_genai_run_experiment_id,
            genai_count_reconciles_score=eval_genai_count_reconciles_score,
            count_reconciles_passed=(
                eval_count_reconciles_passed
                if eval_count_reconciles_passed is not None
                else (eval_passed if eval_passed is not None else eval_total)
            ),
            experiment_id=eval_experiment_id,
            run_experiment_id=eval_run_experiment_id or eval_experiment_id,
        )


class _FakeServingEndpoints:
    def __init__(
        self,
        *,
        ready: bool = True,
        task: str = "agent/v1/responses",
        empty_response: bool = False,
        inference_enabled: bool = True,
        inference_catalog: str = "mip_app_state",
        inference_schema: str = "mip_sync",
        inference_table_prefix: str = "mip_agent_inference",
    ) -> None:
        self.ready = ready
        self.task = task
        self.empty_response = empty_response
        self.inference_enabled = inference_enabled
        self.inference_catalog = inference_catalog
        self.inference_schema = inference_schema
        self.inference_table_prefix = inference_table_prefix
        self.queries: list[tuple[str, dict[str, object]]] = []

    def get(self, name: str) -> object:
        _ = name
        return SimpleNamespace(
            state=SimpleNamespace(ready="READY" if self.ready else "NOT_READY"),
            task=self.task,
            ai_gateway=SimpleNamespace(
                inference_table_config=SimpleNamespace(
                    enabled=self.inference_enabled,
                    catalog_name=self.inference_catalog,
                    schema_name=self.inference_schema,
                    table_name_prefix=self.inference_table_prefix,
                )
            ),
        )

    def query(self, name: str, **kwargs: object) -> object:
        self.queries.append((name, kwargs))
        if self.empty_response:
            return {}
        return {"choices": [{"message": {"content": "ready"}}]}


class _FakeApiClient:
    def __init__(
        self,
        *,
        empty_response: bool = False,
        error: Exception | None = None,
        supervisor_id: str = "supervisor-1",
        supervisor_endpoint: str = "mip-supervisor-endpoint",
        supervisor_error: Exception | None = None,
        serving_response_status: str = "completed",
    ) -> None:
        self.empty_response = empty_response
        self.error = error
        self.supervisor_id = supervisor_id
        self.supervisor_endpoint = supervisor_endpoint
        self.supervisor_error = supervisor_error
        self.serving_response_status = serving_response_status
        self.requests: list[tuple[str, str, dict[str, object] | None]] = []

    def do(
        self, method: str, path: str, *, body: dict[str, object] | None = None, **_kwargs: object
    ) -> object:
        self.requests.append((method, path, body))
        if method == "GET" and path.startswith("/api/2.1/supervisor-agents/"):
            if self.supervisor_error is not None:
                raise self.supervisor_error
            return {
                "supervisor_agent_id": self.supervisor_id,
                "endpoint_name": self.supervisor_endpoint,
            }
        if self.error is not None:
            raise self.error
        if self.empty_response:
            return {}
        return {
            "status": self.serving_response_status,
            "output": [{"content": [{"text": "ready"}]}],
        }


class _FakeExperiments:
    def __init__(
        self,
        *,
        total: int,
        passed: int,
        score: float,
        sha: str,
        tag: str,
        genai_used: bool,
        genai_tracking_uri: str,
        genai_run_verified: bool,
        genai_run_resolvable: bool,
        genai_run_id: str,
        genai_run_experiment_id: str | None,
        genai_count_reconciles_score: float,
        count_reconciles_passed: int,
        experiment_id: str,
        run_experiment_id: str,
    ) -> None:
        self.experiment_id = experiment_id
        self.genai_run_verified = genai_run_verified
        self.genai_run_resolvable = genai_run_resolvable
        self.genai_run_id = genai_run_id
        self.genai_run = SimpleNamespace(
            info=SimpleNamespace(experiment_id=genai_run_experiment_id or experiment_id),
            data=SimpleNamespace(
                metrics=[
                    SimpleNamespace(
                        key="count_reconciles/mean", value=genai_count_reconciles_score
                    ),
                ],
                params=[],
                tags=[],
            ),
        )
        self.run = SimpleNamespace(
            info=SimpleNamespace(experiment_id=run_experiment_id),
            data=SimpleNamespace(
                metrics=[
                    SimpleNamespace(key="score", value=score),
                    SimpleNamespace(key="passed", value=passed),
                    SimpleNamespace(key="total", value=total),
                    SimpleNamespace(key="count_reconciles_passed", value=count_reconciles_passed),
                    SimpleNamespace(
                        key="mlflow_genai_count_reconciles_score",
                        value=genai_count_reconciles_score,
                    ),
                ],
                params=[
                    SimpleNamespace(key="git_sha", value=sha),
                    SimpleNamespace(key="mlflow_genai_evaluate_run_id", value=genai_run_id),
                ],
                tags=[
                    SimpleNamespace(key="mip_eval_type", value=tag),
                    SimpleNamespace(
                        key="mip_mlflow_genai_evaluate",
                        value="true" if genai_used else "false",
                    ),
                    SimpleNamespace(key="mip_mlflow_genai_tracking_uri", value=genai_tracking_uri),
                    SimpleNamespace(
                        key="mip_mlflow_genai_databricks_run_verified",
                        value="true" if genai_run_verified else "false",
                    ),
                ],
            ),
        )

    def get_by_name(self, name: str) -> object:
        _ = name
        return SimpleNamespace(experiment=SimpleNamespace(experiment_id=self.experiment_id))

    def get_run(self, run_id: str) -> object:
        if run_id == self.genai_run_id:
            if not self.genai_run_resolvable:
                raise RuntimeError("genai run missing")
            return SimpleNamespace(run=self.genai_run)
        return SimpleNamespace(run=self.run)

    def search_runs(self, **kwargs: object) -> list[object]:
        _ = kwargs
        return [self.run]


def test_preview_capabilities_are_never_claimable() -> None:
    """CustomerLake / App Spaces / Lakehouse//RT / declarative agents / glossary
    / ontology must never present as integrated, regardless of mirror flag."""
    preview_keys = {
        "genie_ontology",
        "customerlake",
        "app_spaces_microapps",
        "lakehouse_rt",
        "declarative_genie_agents",
        "uc_glossary_domains",
    }
    for mirror in (True, False):
        caps = probe_capabilities(_settings(mip_preview_mirror=mirror))
        for key in preview_keys:
            cap = _by_key(caps, key)
            assert cap.ga is False
            assert cap.claimable is False
            expected = CapabilityStatus.PREVIEW_MIRROR if mirror else CapabilityStatus.HIDDEN
            assert cap.status is expected, f"{key} -> {cap.status}"


def test_orchestrator_flag_on_without_libs_is_not_provisioned() -> None:
    """databricks-agents / mlflow>=3.1.3 are not installed in CI, so flipping the
    flag on must NOT yield a claimable capability — it surfaces honestly."""
    caps = probe_capabilities(_settings(mip_agent_orchestrator=True))
    orch = _by_key(caps, "agent_orchestrator")
    assert orch.ga is True
    assert orch.status is CapabilityStatus.NOT_PROVISIONED
    assert orch.claimable is False


def test_orchestrator_flag_off_is_not_provisioned() -> None:
    caps = probe_capabilities(_settings(mip_agent_orchestrator=False))
    orch = _by_key(caps, "agent_orchestrator")
    assert orch.status is CapabilityStatus.NOT_PROVISIONED
    assert "off" in orch.detail.lower()


def test_metric_certification_and_uc_agent_tools_are_configured_not_claimed() -> None:
    caps = probe_capabilities(_settings())
    for key in ("certified_metric_views", "uc_function_tools"):
        cap = _by_key(caps, key)
        assert cap.ga is True
        assert cap.status is CapabilityStatus.CONFIGURED
        assert cap.claimable is False
        assert "live" in cap.detail.lower()


def test_live_statuses_upgrade_configured_rows_only() -> None:
    caps = probe_capabilities(
        _settings(mip_lakebase_sync=True),
        live_statuses={
            "genie_conversation_api": LiveCapabilityStatus(True, "Genie live."),
            "certified_metric_views": LiveCapabilityStatus(True, "Metric views live."),
            "uc_function_tools": LiveCapabilityStatus(True, "UC tools live."),
            "lakebase_sync": LiveCapabilityStatus(True, "Lakebase sync live."),
            # Preview/no-public-API rows must ignore live evidence and stay non-claimable.
            "customerlake": LiveCapabilityStatus(True, "Should not matter."),
        },
    )

    for key in (
        "genie_conversation_api",
        "certified_metric_views",
        "uc_function_tools",
    ):
        cap = _by_key(caps, key)
        assert cap.status is CapabilityStatus.AVAILABLE
        assert cap.claimable is True
        assert "live" in cap.detail.lower()
    sync = _by_key(caps, "lakebase_sync")
    assert sync.status is CapabilityStatus.AVAILABLE
    assert sync.claimable is True
    assert "lakebase sync live" in sync.detail.lower()
    customerlake = _by_key(caps, "customerlake")
    assert customerlake.claimable is False


def test_live_status_failed_probe_stays_configured_not_claimable() -> None:
    caps = probe_capabilities(
        _settings(),
        live_statuses={
            "genie_conversation_api": LiveCapabilityStatus(False, "Genie ping failed."),
            "certified_metric_views": LiveCapabilityStatus(False, "Query failed."),
            "uc_function_tools": LiveCapabilityStatus(False, "Function failed."),
        },
    )

    for key in ("genie_conversation_api", "certified_metric_views", "uc_function_tools"):
        cap = _by_key(caps, key)
        assert cap.status is CapabilityStatus.CONFIGURED
        assert cap.claimable is False
        assert "did not pass" in cap.detail.lower()


def test_live_capability_probe_executes_exact_reviewed_contracts() -> None:
    sql = _LiveSqlClient()
    statuses = collect_live_capability_statuses(
        sql_client=sql,
        genie_client=_LiveGenieClient(ok=True),
        lakebase=_LiveLakebase(),
    )

    assert statuses["genie_conversation_api"].available is True
    assert statuses["certified_metric_views"].available is True
    assert statuses["uc_function_tools"].available is True
    assert "lakebase_sync" not in statuses
    assert len(sql.statements) == 6
    assert any(
        "semantics.certified_lead_generation_metric_view" in statement
        for statement in sql.statements
    )
    assert not any(
        "semantics.lead_generation_metric_view" in statement
        and "semantics.certified_lead_generation_metric_view" not in statement
        for statement in sql.statements
    )
    assert any(".gold.fn_build_cohort(" in statement for statement in sql.statements)


def test_live_capability_probe_failures_are_captured() -> None:
    statuses = collect_live_capability_statuses(
        sql_client=_LiveSqlClient(fail=True),
        genie_client=_LiveGenieClient(ok=False),
        lakebase=_LiveLakebase(),
    )

    assert statuses["genie_conversation_api"].available is False
    assert statuses["certified_metric_views"].available is False
    assert statuses["uc_function_tools"].available is False


def test_genie_live_probe_requires_conversation_and_message_ids() -> None:
    statuses = collect_live_capability_statuses(
        sql_client=_LiveSqlClient(),
        genie_client=_LiveGenieClient(conversation_id="conv-live", message_id=None),
        lakebase=_LiveLakebase(),
    )

    assert statuses["genie_conversation_api"].available is False
    assert "conversation and message id" in statuses["genie_conversation_api"].detail


def test_genie_live_probe_requires_completed_terminal_status() -> None:
    statuses = collect_live_capability_statuses(
        genie_client=_LiveGenieClient(genie_status=None),
    )

    assert statuses["genie_conversation_api"].available is False
    assert "terminal status was missing" in statuses["genie_conversation_api"].detail
    assert statuses["genie_native_visualization"].available is False


def test_genie_native_visualization_configured_not_claimed_by_default() -> None:
    """Genie configured but no live probe -> native-viz row is configured only."""
    cap = _by_key(probe_capabilities(_settings()), "genie_native_visualization")
    assert cap.ga is False
    assert cap.status is CapabilityStatus.CONFIGURED
    assert cap.claimable is False
    assert "live probe" in cap.detail.lower()


def test_genie_native_visualization_live_probe_available_when_download_ok() -> None:
    """A viz attachment on the probe turn + a 200 download -> AVAILABLE."""
    genie = _LiveGenieClient(
        ok=True,
        native_visualization={"attachment_id": "viz-1", "query_attachment_id": "q-1"},
        download_ok=True,
    )
    statuses = collect_live_capability_statuses(
        sql_client=_LiveSqlClient(),
        genie_client=genie,
        lakebase=_LiveLakebase(),
    )

    assert statuses["genie_native_visualization"].available is True
    # The download was attempted against the exact probe-turn attachment.
    assert genie.download_calls == [("conv-live", "msg-live", "viz-1")]
    # And no SECOND Genie question was issued for the viz row (one ask total).
    caps = probe_capabilities(_settings(), live_statuses=statuses)
    viz = _by_key(caps, "genie_native_visualization")
    assert viz.status is CapabilityStatus.AVAILABLE
    assert viz.claimable is True


def test_genie_native_visualization_live_probe_configured_when_download_404() -> None:
    """A viz attachment but the Beta download endpoint 404s -> configured only."""
    genie = _LiveGenieClient(
        ok=True,
        native_visualization={"attachment_id": "viz-1", "query_attachment_id": "q-1"},
        download_ok=False,
    )
    statuses = collect_live_capability_statuses(
        sql_client=_LiveSqlClient(),
        genie_client=genie,
        lakebase=_LiveLakebase(),
    )

    assert statuses["genie_native_visualization"].available is False
    assert "not yet available" in statuses["genie_native_visualization"].detail
    caps = probe_capabilities(_settings(), live_statuses=statuses)
    viz = _by_key(caps, "genie_native_visualization")
    assert viz.status is CapabilityStatus.CONFIGURED
    assert viz.claimable is False


def test_genie_native_visualization_live_probe_no_attachment_stays_configured() -> None:
    """No viz attachment on the probe turn -> not available, no download attempt."""
    genie = _LiveGenieClient(ok=True, native_visualization=None)
    statuses = collect_live_capability_statuses(
        sql_client=_LiveSqlClient(),
        genie_client=genie,
        lakebase=_LiveLakebase(),
    )

    assert statuses["genie_native_visualization"].available is False
    assert genie.download_calls == []


def test_lakebase_sync_live_probe_requires_synced_table_metadata() -> None:
    sql = _LiveSqlClient()
    statuses = collect_live_capability_statuses(
        settings=_settings(mip_lakebase_sync=True, mip_lakebase_sync_tables="source_readiness"),
        sql_client=sql,
        lakebase=_LiveLakebase(),
        workspace_client=_FakeWorkspaceClient(),
    )

    assert statuses["lakebase_sync"].available is True
    assert "source_readiness" in sql.statements[-1]


def test_lakebase_sync_live_probe_requires_nonzero_rows() -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(mip_lakebase_sync=True, mip_lakebase_sync_tables="source_readiness"),
        sql_client=_LiveSqlClient(count=0),
        lakebase=_LiveLakebase(),
        workspace_client=_FakeWorkspaceClient(),
    )

    assert statuses["lakebase_sync"].available is False
    assert "zero rows" in statuses["lakebase_sync"].detail


def test_lakebase_sync_capability_does_not_require_unused_lakebase_user() -> None:
    caps = probe_capabilities(
        _settings(
            lakebase_user=None,
            mip_lakebase_sync=True,
            mip_lakebase_sync_tables="source_readiness",
        ),
        live_statuses={"lakebase_sync": LiveCapabilityStatus(True, "Synced tables live.")},
    )

    sync = _by_key(caps, "lakebase_sync")
    assert sync.status is CapabilityStatus.AVAILABLE
    assert sync.claimable is True
    assert sync.detail == "Synced tables live."


def test_lakebase_sync_live_probe_runs_without_lakebase_client() -> None:
    sql = _LiveSqlClient()
    statuses = collect_live_capability_statuses(
        settings=_settings(mip_lakebase_sync=True, mip_lakebase_sync_tables="source_readiness"),
        sql_client=sql,
        lakebase=None,
        workspace_client=_FakeWorkspaceClient(),
    )

    assert statuses["lakebase_sync"].available is True
    assert "mip.gold.fn_build_cohort" not in sql.statements[-1]
    assert "source_readiness" in sql.statements[-1]


def test_lakebase_sync_probe_fails_closed_when_metadata_acl_denied() -> None:
    sql = _LiveSqlClient()
    statuses = collect_live_capability_statuses(
        settings=_settings(mip_lakebase_sync=True, mip_lakebase_sync_tables="source_readiness"),
        sql_client=sql,
        workspace_client=_FakeWorkspaceClient(permission_denied=True),
    )

    assert statuses["lakebase_sync"].available is False
    assert "Database API metadata" in statuses["lakebase_sync"].detail
    assert "SQL rows alone do not prove sync state" in statuses["lakebase_sync"].detail
    assert not any("source_readiness" in statement for statement in sql.statements)


def test_lakebase_sync_probe_fails_closed_when_metadata_api_is_unavailable() -> None:
    sql = _LiveSqlClient()
    statuses = collect_live_capability_statuses(
        settings=_settings(mip_lakebase_sync=True, mip_lakebase_sync_tables="source_readiness"),
        sql_client=sql,
        workspace_client=_FakeWorkspaceClient(
            database_metadata_error=RuntimeError("Database API unavailable")
        ),
    )

    assert statuses["lakebase_sync"].available is False
    assert "RuntimeError" in statuses["lakebase_sync"].detail
    assert not any("source_readiness" in statement for statement in sql.statements)


def test_agent_orchestrator_live_probe_requires_endpoint_query() -> None:
    workspace = _FakeWorkspaceClient()
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_agent_orchestrator=True,
            mip_agent_supervisor_id="supervisor-1",
            mip_agent_serving_endpoint="mip-supervisor-endpoint",
        ),
        workspace_client=workspace,
    )

    assert statuses["agent_orchestrator"].available is True
    assert len(workspace.api_client.requests) == 2
    metadata_method, metadata_path, _metadata_body = workspace.api_client.requests[0]
    assert metadata_method == "GET"
    assert metadata_path == "/api/2.1/supervisor-agents/supervisor-1"
    method, path, body = workspace.api_client.requests[1]
    assert method == "POST"
    assert path == "/serving-endpoints/responses"
    assert body is not None
    assert body["model"] == "mip-supervisor-endpoint"


def test_agent_orchestrator_rejects_supervisor_endpoint_metadata_mismatch() -> None:
    workspace = _FakeWorkspaceClient(supervisor_metadata_endpoint="different-endpoint")
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_agent_orchestrator=True,
            mip_agent_supervisor_id="supervisor-1",
            mip_agent_serving_endpoint="mip-supervisor-endpoint",
        ),
        workspace_client=workspace,
    )

    assert statuses["agent_orchestrator"].available is False
    assert "does not map" in statuses["agent_orchestrator"].detail
    assert len(workspace.api_client.requests) == 1
    assert workspace.serving_endpoints.queries == []


def test_agent_orchestrator_rejects_supervisor_identity_metadata_mismatch() -> None:
    workspace = _FakeWorkspaceClient(supervisor_metadata_id="different-supervisor")
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_agent_orchestrator=True,
            mip_agent_supervisor_id="supervisor-1",
            mip_agent_serving_endpoint="mip-supervisor-endpoint",
        ),
        workspace_client=workspace,
    )

    assert statuses["agent_orchestrator"].available is False
    assert "did not match" in statuses["agent_orchestrator"].detail
    assert len(workspace.api_client.requests) == 1
    assert workspace.serving_endpoints.queries == []


def test_agent_orchestrator_fails_closed_when_supervisor_metadata_is_unavailable() -> None:
    class PermissionDenied(Exception):
        pass

    workspace = _FakeWorkspaceClient(supervisor_metadata_error=PermissionDenied("metadata denied"))
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_agent_orchestrator=True,
            mip_agent_supervisor_id="supervisor-1",
            mip_agent_serving_endpoint="mip-supervisor-endpoint",
        ),
        workspace_client=workspace,
    )

    assert statuses["agent_orchestrator"].available is False
    assert "PermissionDenied" in statuses["agent_orchestrator"].detail
    assert len(workspace.api_client.requests) == 1
    assert workspace.serving_endpoints.queries == []


def test_agent_orchestrator_normalizes_sdk_enum_task_to_exact_responses_transport() -> None:
    workspace = _FakeWorkspaceClient()
    workspace.serving_endpoints.task = SimpleNamespace(value="AGENT_V1_RESPONSES")

    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_agent_orchestrator=True,
            mip_agent_supervisor_id="supervisor-1",
            mip_agent_serving_endpoint="mip-supervisor-endpoint",
        ),
        workspace_client=workspace,
    )

    assert statuses["agent_orchestrator"].available is True
    assert workspace.api_client.requests[1][1] == "/serving-endpoints/responses"


def test_agent_orchestrator_live_probe_does_not_retry_after_responses_route_failure() -> None:
    workspace = _FakeWorkspaceClient(responses_api_error=json.JSONDecodeError("bad", "", 0))
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_agent_orchestrator=True,
            mip_agent_supervisor_id="supervisor-1",
            mip_agent_serving_endpoint="mip-supervisor-endpoint",
        ),
        workspace_client=workspace,
    )

    assert statuses["agent_orchestrator"].available is False
    assert "JSONDecodeError" in statuses["agent_orchestrator"].detail
    assert len(workspace.api_client.requests) == 2
    assert workspace.serving_endpoints.queries == []


def test_agent_orchestrator_live_probe_rejects_empty_endpoint_response() -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_agent_orchestrator=True,
            mip_agent_supervisor_id="supervisor-1",
            mip_agent_serving_endpoint="mip-supervisor-endpoint",
        ),
        workspace_client=_FakeWorkspaceClient(empty_serving_response=True),
    )

    assert statuses["agent_orchestrator"].available is False
    assert "no response payload" in statuses["agent_orchestrator"].detail


@pytest.mark.parametrize("task", ["llm/v1/chat", "not_agent", "agentless-chat"])
def test_agent_orchestrator_live_probe_requires_exact_agent_responses_task(task: str) -> None:
    blocked = collect_live_capability_statuses(
        settings=_settings(
            mip_agent_orchestrator=True,
            mip_agent_supervisor_id="supervisor-1",
            mip_agent_serving_endpoint="mip-supervisor-endpoint",
        ),
        workspace_client=_FakeWorkspaceClient(serving_task=task),
    )

    assert blocked["agent_orchestrator"].available is False
    assert "not agent" in blocked["agent_orchestrator"].detail.lower()


def test_ai_gateway_live_probe_requires_endpoint_query_and_verified_ledger_proof() -> None:
    sql = _LiveSqlClient(count=3)
    workspace = _FakeWorkspaceClient()
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_git_sha=_TEST_GIT_SHA,
            mip_ai_gateway=True,
            mip_ai_gateway_endpoint="mip-agent-gateway",
            mip_ai_gateway_inference_table="mip_app_state.mip_sync.mip_agent_inference",
        ),
        sql_client=sql,
        lakebase=_LiveLakebase.verified(),
        workspace_client=workspace,
    )

    assert statuses["ai_gateway"].available is True
    assert "exact inference-row round-trip verified" in statuses["ai_gateway"].detail
    assert "Current deployment inference rows visible: 3" in statuses["ai_gateway"].detail
    assert workspace.api_client.requests
    method, path, body = workspace.api_client.requests[0]
    assert method == "POST"
    assert path == "/serving-endpoints/responses"
    assert body is not None
    assert body["model"] == "mip-agent-gateway"
    client_request_id = str(body.get("client_request_id") or "")
    assert client_request_id.startswith(f"mip-capability-{_TEST_GIT_SHA}-")
    assert len(client_request_id) > len(f"mip-capability-{_TEST_GIT_SHA}-")
    assert any("system.information_schema.tables" in statement for statement in sql.statements)
    assert any("mip_agent_inference_payload" in statement for statement in sql.statements)
    assert any(
        isinstance(params, dict)
        and params.get("prefix_0") == f"mip-capability-{_TEST_GIT_SHA}-%"
        and params.get("prefix_1") == f"mip-agent-run-{_TEST_GIT_SHA}-%"
        for params in sql.parameters
    )


def test_ai_gateway_live_probe_does_not_retry_or_write_ledger_at_runtime() -> None:
    sql = _LiveSqlClient(count=1)
    workspace = _FakeWorkspaceClient()
    lakebase = _LiveLakebase.verified()
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_git_sha=_TEST_GIT_SHA,
            mip_ai_gateway=True,
            mip_ai_gateway_endpoint="mip-agent-gateway",
            mip_ai_gateway_inference_table="mip_app_state.mip_sync.mip_agent_inference",
        ),
        sql_client=sql,
        lakebase=lakebase,
        workspace_client=workspace,
    )

    assert statuses["ai_gateway"].available is True
    assert len(workspace.api_client.requests) == 1
    assert len(lakebase.fetchone_calls) == 1


@pytest.mark.parametrize(
    ("workspace", "expected_path"),
    [
        (
            _FakeWorkspaceClient(serving_task="llm/v1/chat"),
            "/serving-endpoints/mip-agent-gateway/invocations",
        ),
        (
            _FakeWorkspaceClient(empty_serving_response=True),
            "/serving-endpoints/responses",
        ),
        (
            _FakeWorkspaceClient(serving_response_status="in_progress"),
            "/serving-endpoints/responses",
        ),
    ],
    ids=["generic-payload", "no-payload", "nonterminal"],
)
def test_ai_gateway_live_probe_requires_terminal_responses_execution(
    workspace: _FakeWorkspaceClient,
    expected_path: str,
) -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_git_sha=_TEST_GIT_SHA,
            mip_ai_gateway=True,
            mip_ai_gateway_endpoint="mip-agent-gateway",
            mip_ai_gateway_inference_table="mip_app_state.mip_sync.mip_agent_inference",
        ),
        sql_client=_LiveSqlClient(count=1),
        lakebase=_LiveLakebase.verified(),
        workspace_client=workspace,
    )

    assert statuses["ai_gateway"].available is False
    assert "terminal completed Responses payload" in statuses["ai_gateway"].detail
    assert len(workspace.api_client.requests) == 1
    assert workspace.api_client.requests[0][1] == expected_path


def test_ai_gateway_live_probe_rejects_prefix_rows_without_verified_ledger() -> None:
    sql = _LiveSqlClient(count=3)
    workspace = _FakeWorkspaceClient()
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_git_sha=_TEST_GIT_SHA,
            mip_ai_gateway=True,
            mip_ai_gateway_endpoint="mip-agent-gateway",
            mip_ai_gateway_inference_table="mip_app_state.mip_sync.mip_agent_inference",
        ),
        sql_client=sql,
        lakebase=_LiveLakebase(),
        workspace_client=workspace,
    )

    assert statuses["ai_gateway"].available is False
    assert "no fresh ledger-verified exact row" in statuses["ai_gateway"].detail
    assert workspace.api_client.requests
    assert not any("COUNT(*) AS recent_row_count" in statement for statement in sql.statements)


def test_ai_gateway_live_probe_rejects_verified_ledger_row_for_different_sha() -> None:
    sql = _LiveSqlClient(count=2)
    workspace = _FakeWorkspaceClient()
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_git_sha=_TEST_GIT_SHA,
            mip_ai_gateway=True,
            mip_ai_gateway_endpoint="mip-agent-gateway",
            mip_ai_gateway_inference_table="mip_app_state.mip_sync.mip_agent_inference",
        ),
        sql_client=sql,
        lakebase=_LiveLakebase.verified(_OTHER_GIT_SHA),
        workspace_client=workspace,
    )

    assert statuses["ai_gateway"].available is False
    assert "no fresh ledger-verified exact row" in statuses["ai_gateway"].detail


def test_ai_gateway_live_probe_rejects_verified_ledger_row_for_different_endpoint_or_table() -> (
    None
):
    base_settings = _settings(
        mip_git_sha=_TEST_GIT_SHA,
        mip_ai_gateway=True,
        mip_ai_gateway_endpoint="mip-agent-gateway",
        mip_ai_gateway_inference_table="mip_app_state.mip_sync.mip_agent_inference",
    )

    wrong_endpoint = collect_live_capability_statuses(
        settings=base_settings,
        sql_client=_LiveSqlClient(count=2),
        lakebase=_LiveLakebase.verified(endpoint_name="other-gateway"),
        workspace_client=_FakeWorkspaceClient(),
    )
    wrong_table = collect_live_capability_statuses(
        settings=base_settings,
        sql_client=_LiveSqlClient(count=2),
        lakebase=_LiveLakebase.verified(inference_table="mip_app_state.mip_sync.other_inference"),
        workspace_client=_FakeWorkspaceClient(),
    )

    assert wrong_endpoint["ai_gateway"].available is False
    assert "no fresh ledger-verified exact row" in wrong_endpoint["ai_gateway"].detail
    assert wrong_table["ai_gateway"].available is False
    assert "no fresh ledger-verified exact row" in wrong_table["ai_gateway"].detail


def test_ai_gateway_live_probe_rejects_stale_verified_ledger_row() -> None:
    stale_verified = datetime.now(UTC) - timedelta(hours=30)
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_git_sha=_TEST_GIT_SHA,
            mip_ai_gateway=True,
            mip_ai_gateway_endpoint="mip-agent-gateway",
            mip_ai_gateway_inference_table="mip_app_state.mip_sync.mip_agent_inference",
        ),
        sql_client=_LiveSqlClient(count=2),
        lakebase=_LiveLakebase.verified(verified_at=stale_verified),
        workspace_client=_FakeWorkspaceClient(),
    )

    assert statuses["ai_gateway"].available is False
    assert "no fresh ledger-verified exact row" in statuses["ai_gateway"].detail


def test_ai_gateway_live_probe_rejects_future_verified_ledger_row() -> None:
    future_verified = datetime.now(UTC) + timedelta(seconds=AI_GATEWAY_PROOF_CLOCK_SKEW_S + 30)
    lakebase = _LiveLakebase.verified(verified_at=future_verified)
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_git_sha=_TEST_GIT_SHA,
            mip_ai_gateway=True,
            mip_ai_gateway_endpoint="mip-agent-gateway",
            mip_ai_gateway_inference_table="mip_app_state.mip_sync.mip_agent_inference",
        ),
        sql_client=_LiveSqlClient(count=2),
        lakebase=lakebase,
        workspace_client=_FakeWorkspaceClient(),
    )

    assert statuses["ai_gateway"].available is False
    assert "no fresh ledger-verified exact row" in statuses["ai_gateway"].detail
    query, params = lakebase.fetchone_calls[-1]
    assert "verified_at <= %(future_cutoff)s" in query
    assert isinstance(params["future_cutoff"], datetime)


def test_ai_gateway_probe_caps_mutated_freshness_before_ledger_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        mip_git_sha=_TEST_GIT_SHA,
        mip_ai_gateway=True,
        mip_ai_gateway_endpoint="mip-agent-gateway",
        mip_ai_gateway_inference_table="mip_app_state.mip_sync.mip_agent_inference",
    )
    settings.mip_ai_gateway_proof_freshness_s = 7 * 24 * 60 * 60
    captured: dict[str, float] = {}

    def _latest_verified_proof(*_args: object, freshness_s: float, **_kwargs: object) -> None:
        captured["freshness_s"] = freshness_s
        return None

    monkeypatch.setattr(
        ai_gateway_probe_module,
        "latest_verified_proof",
        _latest_verified_proof,
    )

    collect_live_capability_statuses(
        settings=settings,
        sql_client=_LiveSqlClient(count=0),
        lakebase=_LiveLakebase(),
        workspace_client=_FakeWorkspaceClient(),
    )

    assert captured["freshness_s"] == AI_GATEWAY_PROOF_FRESHNESS_MAX_S


def test_ai_gateway_live_probe_rejects_without_deployed_sha() -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_ai_gateway=True,
            mip_ai_gateway_endpoint="mip-agent-gateway",
            mip_ai_gateway_inference_table="mip_app_state.mip_sync.mip_agent_inference",
        ),
        sql_client=_LiveSqlClient(count=1),
        lakebase=_LiveLakebase(),
        workspace_client=_FakeWorkspaceClient(),
    )

    assert statuses["ai_gateway"].available is False
    assert "MIP_GIT_SHA is required" in statuses["ai_gateway"].detail


def test_ai_gateway_live_probe_rejects_without_verified_ledger_row() -> None:
    sql = _LiveSqlClient(count=0)
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_git_sha=_TEST_GIT_SHA,
            mip_ai_gateway=True,
            mip_ai_gateway_endpoint="mip-agent-gateway",
            mip_ai_gateway_inference_table="mip_app_state.mip_sync.mip_agent_inference",
        ),
        sql_client=sql,
        lakebase=_LiveLakebase(),
        workspace_client=_FakeWorkspaceClient(),
    )

    assert statuses["ai_gateway"].available is False
    assert "no fresh ledger-verified exact row" in statuses["ai_gateway"].detail
    assert not any("COUNT(*) AS recent_row_count" in statement for statement in sql.statements)


def test_ai_gateway_live_probe_rejects_missing_row_level_proof() -> None:
    sql = _LiveSqlClient(count=0)
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_git_sha=_TEST_GIT_SHA,
            mip_ai_gateway=True,
            mip_ai_gateway_endpoint="mip-agent-gateway",
            mip_ai_gateway_inference_table="mip_app_state.mip_sync.mip_agent_inference",
        ),
        sql_client=sql,
        lakebase=_LiveLakebase(),
        workspace_client=_FakeWorkspaceClient(),
    )

    assert statuses["ai_gateway"].available is False
    assert "accepted a bounded query now" in statuses["ai_gateway"].detail
    assert "not claimable" in statuses["ai_gateway"].detail
    assert not any("COUNT(*) AS recent_row_count" in statement for statement in sql.statements)


def test_ai_gateway_live_probe_rejects_queryable_table_before_verified_ledger_row() -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_git_sha=_TEST_GIT_SHA,
            mip_ai_gateway=True,
            mip_ai_gateway_endpoint="mip-agent-gateway",
            mip_ai_gateway_inference_table="mip_app_state.mip_sync.mip_agent_inference",
        ),
        sql_client=_LiveSqlClient(count=0),
        lakebase=_LiveLakebase(),
        workspace_client=_FakeWorkspaceClient(),
    )

    assert statuses["ai_gateway"].available is False
    assert "inference logging is enabled/queryable" in statuses["ai_gateway"].detail
    assert "not claimable" in statuses["ai_gateway"].detail


def test_ai_gateway_live_probe_rejects_missing_inference_table() -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_git_sha=_TEST_GIT_SHA,
            mip_ai_gateway=True,
            mip_ai_gateway_endpoint="mip-agent-gateway",
            mip_ai_gateway_inference_table="mip_app_state.mip_sync.mip_agent_inference",
        ),
        sql_client=_LiveSqlClient(table_names=[]),
        lakebase=_LiveLakebase(),
        workspace_client=_FakeWorkspaceClient(),
    )

    assert statuses["ai_gateway"].available is False
    assert "No AI Gateway inference tables matching" in statuses["ai_gateway"].detail


def test_ai_gateway_live_probe_rejects_endpoint_not_ready() -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_git_sha=_TEST_GIT_SHA,
            mip_ai_gateway=True,
            mip_ai_gateway_endpoint="mip-agent-gateway",
            mip_ai_gateway_inference_table="mip_app_state.mip_sync.mip_agent_inference",
        ),
        sql_client=_LiveSqlClient(),
        lakebase=_LiveLakebase(),
        workspace_client=_FakeWorkspaceClient(serving_ready=False),
    )

    assert statuses["ai_gateway"].available is False
    assert "not READY" in statuses["ai_gateway"].detail


def test_ai_gateway_live_probe_rejects_disabled_inference_logging() -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_git_sha=_TEST_GIT_SHA,
            mip_ai_gateway=True,
            mip_ai_gateway_endpoint="mip-agent-gateway",
            mip_ai_gateway_inference_table="mip_app_state.mip_sync.mip_agent_inference",
        ),
        sql_client=_LiveSqlClient(),
        lakebase=_LiveLakebase(),
        workspace_client=_FakeWorkspaceClient(inference_enabled=False),
    )

    assert statuses["ai_gateway"].available is False
    assert "inference table is not enabled" in statuses["ai_gateway"].detail


def test_ai_gateway_live_probe_rejects_inference_table_mismatch() -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_git_sha=_TEST_GIT_SHA,
            mip_ai_gateway=True,
            mip_ai_gateway_endpoint="mip-agent-gateway",
            mip_ai_gateway_inference_table="mip_app_state.mip_sync.other_gateway_prefix",
        ),
        sql_client=_LiveSqlClient(),
        lakebase=_LiveLakebase(),
        workspace_client=_FakeWorkspaceClient(),
    )

    assert statuses["ai_gateway"].available is False
    assert "expected mip_app_state.mip_sync.other_gateway_prefix" in statuses["ai_gateway"].detail


def test_ai_gateway_live_probe_rejects_malformed_inference_table_config() -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_git_sha=_TEST_GIT_SHA,
            mip_ai_gateway=True,
            mip_ai_gateway_endpoint="mip-agent-gateway",
            mip_ai_gateway_inference_table="not-three-part",
        ),
        sql_client=_LiveSqlClient(),
        lakebase=_LiveLakebase(),
        workspace_client=_FakeWorkspaceClient(
            inference_catalog="not-three-part",
            inference_schema="",
            inference_table_prefix="",
        ),
    )

    assert statuses["ai_gateway"].available is False
    assert "AI Gateway probe failed (ValueError)" in statuses["ai_gateway"].detail


def test_ai_gateway_live_probe_rejects_missing_lakebase_proof_ledger() -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_git_sha=_TEST_GIT_SHA,
            mip_ai_gateway=True,
            mip_ai_gateway_endpoint="mip-agent-gateway",
            mip_ai_gateway_inference_table="mip_app_state.mip_sync.mip_agent_inference",
        ),
        sql_client=_LiveSqlClient(),
        lakebase=None,
        workspace_client=_FakeWorkspaceClient(),
    )

    assert statuses["ai_gateway"].available is False
    assert "Lakebase proof ledger is required" in statuses["ai_gateway"].detail


def test_agent_eval_live_probe_requires_full_case_floor_and_matching_sha() -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_agent_eval_experiment="/Shared/mip-agent-eval",
            mip_agent_eval_run_id="run-1",
            mip_git_sha="sha-live",
        ),
        workspace_client=_FakeWorkspaceClient(eval_total=5, eval_sha="sha-live"),
    )

    assert statuses["agent_eval"].available is True
    assert "5/5" in statuses["agent_eval"].detail
    assert "GenAI Evaluation" in statuses["agent_eval"].detail


def test_agent_eval_live_probe_requires_deployed_sha_to_be_configured() -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_agent_eval_experiment="/Shared/mip-agent-eval",
            mip_agent_eval_run_id="run-1",
        ),
        workspace_client=_FakeWorkspaceClient(eval_total=5, eval_sha="old-sha"),
    )

    assert statuses["agent_eval"].available is False
    assert "MIP_GIT_SHA is required" in statuses["agent_eval"].detail


def test_agent_eval_live_probe_rejects_too_few_cases() -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_agent_eval_experiment="/Shared/mip-agent-eval",
            mip_agent_eval_run_id="run-1",
            mip_git_sha="sha-live",
        ),
        workspace_client=_FakeWorkspaceClient(eval_total=1, eval_sha="sha-live"),
    )

    assert statuses["agent_eval"].available is False
    assert "minimum is 5" in statuses["agent_eval"].detail


def test_agent_eval_live_probe_rejects_stale_sha() -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_agent_eval_experiment="/Shared/mip-agent-eval",
            mip_agent_eval_run_id="run-1",
            mip_git_sha="deployed-sha",
        ),
        workspace_client=_FakeWorkspaceClient(eval_total=5, eval_sha="old-sha"),
    )

    assert statuses["agent_eval"].available is False
    assert "not deployed SHA" in statuses["agent_eval"].detail


def test_agent_eval_live_probe_rejects_untagged_run() -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_agent_eval_experiment="/Shared/mip-agent-eval",
            mip_agent_eval_run_id="run-1",
            mip_git_sha="sha-live",
        ),
        workspace_client=_FakeWorkspaceClient(
            eval_total=5,
            eval_sha="sha-live",
            eval_tag="manual_debug",
        ),
    )

    assert statuses["agent_eval"].available is False
    assert "golden eval" in statuses["agent_eval"].detail


def test_agent_eval_live_probe_requires_mlflow_genai_evaluate() -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_agent_eval_experiment="/Shared/mip-agent-eval",
            mip_agent_eval_run_id="run-1",
            mip_git_sha="sha-live",
        ),
        workspace_client=_FakeWorkspaceClient(
            eval_total=5,
            eval_sha="sha-live",
            eval_genai_used=False,
        ),
    )

    assert statuses["agent_eval"].available is False
    assert "mlflow.genai.evaluate" in statuses["agent_eval"].detail


def test_agent_eval_live_probe_requires_databricks_mlflow_tracking() -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_agent_eval_experiment="/Shared/mip-agent-eval",
            mip_agent_eval_run_id="run-1",
            mip_git_sha="sha-live",
        ),
        workspace_client=_FakeWorkspaceClient(
            eval_total=5,
            eval_sha="sha-live",
            eval_genai_tracking_uri="sqlite:////tmp/mlflow.db",
        ),
    )

    assert statuses["agent_eval"].available is False
    assert "Databricks MLflow tracking" in statuses["agent_eval"].detail


def test_agent_eval_live_probe_requires_resolvable_genai_run() -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_agent_eval_experiment="/Shared/mip-agent-eval",
            mip_agent_eval_run_id="run-1",
            mip_git_sha="sha-live",
        ),
        workspace_client=_FakeWorkspaceClient(
            eval_total=5,
            eval_sha="sha-live",
            eval_genai_run_resolvable=False,
        ),
    )

    assert statuses["agent_eval"].available is False
    assert "not resolvable in Databricks MLflow" in statuses["agent_eval"].detail


def test_agent_eval_live_probe_requires_genai_run_same_experiment() -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_agent_eval_experiment="/Shared/mip-agent-eval",
            mip_agent_eval_run_id="run-1",
            mip_git_sha="sha-live",
        ),
        workspace_client=_FakeWorkspaceClient(
            eval_total=5,
            eval_sha="sha-live",
            eval_genai_run_experiment_id="other-exp",
        ),
    )

    assert statuses["agent_eval"].available is False
    assert "belongs to experiment other-exp" in statuses["agent_eval"].detail


def test_agent_eval_live_probe_requires_count_reconciliation_scorer() -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_agent_eval_experiment="/Shared/mip-agent-eval",
            mip_agent_eval_run_id="run-1",
            mip_git_sha="sha-live",
        ),
        workspace_client=_FakeWorkspaceClient(
            eval_total=5,
            eval_sha="sha-live",
            eval_count_reconciles_passed=4,
        ),
    )

    assert statuses["agent_eval"].available is False
    assert "reconciled 4/5" in statuses["agent_eval"].detail


def test_agent_eval_live_probe_requires_genai_count_reconciliation_metric() -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_agent_eval_experiment="/Shared/mip-agent-eval",
            mip_agent_eval_run_id="run-1",
            mip_git_sha="sha-live",
        ),
        workspace_client=_FakeWorkspaceClient(
            eval_total=5,
            eval_sha="sha-live",
            eval_genai_count_reconciles_score=0.0,
        ),
    )

    assert statuses["agent_eval"].available is False
    assert "count_reconciles scorer metric" in statuses["agent_eval"].detail


def test_agent_eval_live_probe_rejects_run_from_different_experiment() -> None:
    statuses = collect_live_capability_statuses(
        settings=_settings(
            mip_agent_eval_experiment="/Shared/mip-agent-eval",
            mip_agent_eval_run_id="run-1",
            mip_git_sha="sha-live",
        ),
        workspace_client=_FakeWorkspaceClient(
            eval_total=5,
            eval_sha="sha-live",
            eval_experiment_id="exp-1",
            eval_run_experiment_id="other-exp",
        ),
    )

    assert statuses["agent_eval"].available is False
    assert "not exp-1" in statuses["agent_eval"].detail


def test_certified_metric_view_sql_contracts_are_present() -> None:
    metric_dir = _REPO_ROOT / "sql" / "metric_views"
    expected = {
        "certified_borrower_opportunity_metric_view.sql",
        "certified_lead_generation_metric_view.sql",
        "certified_segment_performance_metric_view.sql",
    }
    for filename in expected:
        text = (metric_dir / filename).read_text(encoding="utf-8").lower()
        assert "with metrics" in text
        assert "language yaml" in text
        assert "certification:" in text
        assert "synonyms:" in text


def test_uc_growth_agent_tool_sql_contracts_are_present() -> None:
    function_dir = _REPO_ROOT / "sql" / "uc_functions"
    expected = {
        "fn_build_cohort.sql",
        "fn_segment_counts.sql",
        "fn_lead_queue_url.sql",
    }
    for filename in expected:
        text = (function_dir / filename).read_text(encoding="utf-8").lower()
        assert "create or replace function" in text
        assert "mortgage growth agent" in text
        assert "read-only" in text or "no outreach or state write" in text


def test_growth_agent_function_grants_are_documented_and_deployed() -> None:
    deploy = (_REPO_ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    grants = (_REPO_ROOT / "docs" / "security" / "GRANTS.md").read_text(encoding="utf-8")
    for function_name in ("fn_build_cohort", "fn_segment_counts", "fn_lead_queue_url"):
        grant = "GRANT EXECUTE ON FUNCTION"
        assert grant in deploy
        assert f".gold.{function_name}" in deploy
        assert grant in grants
        assert f"mip.gold.{function_name}" in grants


def test_ai_gateway_audit_grant_is_table_scoped() -> None:
    deploy = (_REPO_ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    grants = (_REPO_ROOT / "docs" / "security" / "GRANTS.md").read_text(encoding="utf-8")
    assert "GRANT USE SCHEMA, SELECT ON SCHEMA ${_GRANTS_CATALOG}.audit" not in deploy
    assert "GRANT USE SCHEMA ON SCHEMA ${_GRANTS_CATALOG}.audit" in deploy
    assert "grant_ai_gateway_inference_table.py" in deploy
    assert "GRANT SELECT ON TABLE mip.audit.mip_agent_gateway_llama_payload" in grants
    assert "Do not grant `SELECT ON SCHEMA mip.audit`" in grants


def test_genie_conversation_needs_space_and_warehouse() -> None:
    ok = _by_key(probe_capabilities(_settings()), "genie_conversation_api")
    assert ok.status is CapabilityStatus.CONFIGURED
    assert ok.claimable is False
    assert "live genie probe" in ok.detail.lower()
    missing = _by_key(probe_capabilities(_settings(genie_space_id=None)), "genie_conversation_api")
    assert missing.status is CapabilityStatus.NOT_PROVISIONED
    assert missing.claimable is False
    placeholder = _by_key(
        probe_capabilities(_settings(genie_space_id="00000000PLACEHOLDER")),
        "genie_conversation_api",
    )
    assert placeholder.status is CapabilityStatus.NOT_PROVISIONED
    assert placeholder.claimable is False


def test_ai_gateway_and_lakebase_sync_gated_by_flags() -> None:
    off = probe_capabilities(_settings())
    assert _by_key(off, "ai_gateway").status is CapabilityStatus.NOT_PROVISIONED
    assert _by_key(off, "lakebase_sync").status is CapabilityStatus.NOT_PROVISIONED
    on = probe_capabilities(_settings(mip_ai_gateway=True, mip_lakebase_sync=True))
    assert _by_key(on, "ai_gateway").status is CapabilityStatus.NOT_PROVISIONED
    assert _by_key(on, "lakebase_sync").status is CapabilityStatus.CONFIGURED


def test_ai_gateway_requires_endpoint_and_inference_table_config() -> None:
    caps = probe_capabilities(
        _settings(
            mip_ai_gateway=True,
            mip_ai_gateway_endpoint="mip-agent-gateway",
            mip_ai_gateway_inference_table="mip_app.agent_inference_logs",
        )
    )
    gateway = _by_key(caps, "ai_gateway")
    assert gateway.status is CapabilityStatus.CONFIGURED
    assert gateway.claimable is False
    assert "live" in gateway.detail.lower()


def test_warehouse_placeholder_is_not_provisioned() -> None:
    caps = probe_capabilities(_settings(databricks_host="<workspace-host>.cloud.databricks.com"))
    assert _by_key(caps, "certified_metric_views").status is CapabilityStatus.NOT_PROVISIONED


def test_snapshot_to_dict_shape() -> None:
    caps = get_capabilities_snapshot(refresh=True)
    assert caps, "snapshot must be non-empty"
    row = caps[0].to_dict()
    assert set(row) == {"key", "label", "ga", "status", "claimable", "detail"}
    assert isinstance(row["claimable"], bool)


def test_capabilities_endpoint_admin_gated_and_shaped() -> None:
    client = TestClient(app)  # conftest stamps the admin group header
    resp = client.get("/api/admin/capabilities")
    assert resp.status_code == 200
    body = resp.json()
    assert "capabilities" in body and body["capabilities"]
    keys = {row["key"] for row in body["capabilities"]}
    assert {"genie_conversation_api", "agent_orchestrator", "customerlake"} <= keys
    # Every preview row must be non-claimable in the live (flag-default) app.
    for row in body["capabilities"]:
        if row["ga"] is False:
            assert row["claimable"] is False


def test_capabilities_endpoint_live_probe_marks_live_dependencies_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(capabilities_module, "get_settings", lambda: _settings())
    reset_live_capability_probe_cache()
    app.dependency_overrides[get_sql_client] = lambda: _LiveSqlClient()
    app.dependency_overrides[get_genie_client] = lambda: _LiveGenieClient(ok=True)
    app.dependency_overrides[get_lakebase_client] = lambda: _LiveLakebase.verified()
    try:
        client = TestClient(app)
        resp = client.get("/api/admin/capabilities?live=1")
    finally:
        app.dependency_overrides.pop(get_sql_client, None)
        app.dependency_overrides.pop(get_genie_client, None)
        app.dependency_overrides.pop(get_lakebase_client, None)

    assert resp.status_code == 200
    rows = {row["key"]: row for row in resp.json()["capabilities"]}
    assert rows["genie_conversation_api"]["status"] == "available"
    assert rows["certified_metric_views"]["status"] == "available"
    assert rows["uc_function_tools"]["status"] == "available"
    assert rows["genie_ontology"]["claimable"] is False


def test_capabilities_live_probe_bypasses_cache_for_ai_gateway_exact_precheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        mip_git_sha=_TEST_GIT_SHA,
        mip_ai_gateway=True,
        mip_ai_gateway_endpoint="mip-agent-gateway",
        mip_ai_gateway_inference_table="mip_app_state.mip_sync.mip_agent_inference",
        mip_live_capability_probe_ttl_s=60.0,
    )
    workspace = _FakeWorkspaceClient()
    sql = _LiveSqlClient(count=1)
    monkeypatch.setattr(capabilities_module, "get_settings", lambda: settings)
    monkeypatch.setattr(capability_request_module, "get_settings", lambda: settings)
    monkeypatch.setattr(capability_request_module, "_workspace_client", lambda: (workspace, None))
    reset_live_capability_probe_cache()
    app.dependency_overrides[get_sql_client] = lambda: sql
    app.dependency_overrides[get_genie_client] = lambda: _LiveGenieClient(ok=True)
    app.dependency_overrides[get_lakebase_client] = lambda: _LiveLakebase.verified()
    try:
        client = TestClient(app)
        first = client.get("/api/admin/capabilities?live=1")
        second = client.get("/api/admin/capabilities?live=1")
    finally:
        app.dependency_overrides.pop(get_sql_client, None)
        app.dependency_overrides.pop(get_genie_client, None)
        app.dependency_overrides.pop(get_lakebase_client, None)
        reset_live_capability_probe_cache()

    assert first.status_code == 200
    assert second.status_code == 200
    first_rows = {row["key"]: row for row in first.json()["capabilities"]}
    second_rows = {row["key"]: row for row in second.json()["capabilities"]}
    assert first_rows["ai_gateway"]["status"] == "available"
    assert second_rows["ai_gateway"]["status"] == "available"
    assert len(workspace.api_client.requests) == 2


def test_capabilities_live_probe_cache_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    monkeypatch.setattr(
        capability_request_module, "_LIVE_CAPABILITY_CACHE", TTLCache(now=lambda: now[0])
    )
    settings = _settings(
        mip_git_sha=_TEST_GIT_SHA,
        mip_ai_gateway=True,
        mip_ai_gateway_endpoint="mip-agent-gateway",
        mip_ai_gateway_inference_table="mip_app_state.mip_sync.mip_agent_inference",
        mip_live_capability_probe_ttl_s=10.0,
    )
    workspace = _FakeWorkspaceClient()
    sql = _LiveSqlClient(count=1)
    monkeypatch.setattr(capabilities_module, "get_settings", lambda: settings)
    monkeypatch.setattr(capability_request_module, "get_settings", lambda: settings)
    monkeypatch.setattr(capability_request_module, "_workspace_client", lambda: (workspace, None))
    reset_live_capability_probe_cache()
    app.dependency_overrides[get_sql_client] = lambda: sql
    app.dependency_overrides[get_genie_client] = lambda: _LiveGenieClient(ok=True)
    app.dependency_overrides[get_lakebase_client] = lambda: _LiveLakebase.verified()
    try:
        client = TestClient(app)
        first = client.get("/api/admin/capabilities?live=1")
        now[0] = 11.0
        second = client.get("/api/admin/capabilities?live=1")
    finally:
        app.dependency_overrides.pop(get_sql_client, None)
        app.dependency_overrides.pop(get_genie_client, None)
        app.dependency_overrides.pop(get_lakebase_client, None)
        reset_live_capability_probe_cache()

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(workspace.api_client.requests) == 2


def test_capabilities_live_probe_ttl_zero_disables_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        mip_git_sha=_TEST_GIT_SHA,
        mip_ai_gateway=True,
        mip_ai_gateway_endpoint="mip-agent-gateway",
        mip_ai_gateway_inference_table="mip_app_state.mip_sync.mip_agent_inference",
        mip_live_capability_probe_ttl_s=0.0,
    )
    workspace = _FakeWorkspaceClient()
    sql = _LiveSqlClient(count=1)
    monkeypatch.setattr(capabilities_module, "get_settings", lambda: settings)
    monkeypatch.setattr(capability_request_module, "get_settings", lambda: settings)
    monkeypatch.setattr(capability_request_module, "_workspace_client", lambda: (workspace, None))
    reset_live_capability_probe_cache()
    app.dependency_overrides[get_sql_client] = lambda: sql
    app.dependency_overrides[get_genie_client] = lambda: _LiveGenieClient(ok=True)
    app.dependency_overrides[get_lakebase_client] = lambda: _LiveLakebase.verified()
    try:
        client = TestClient(app)
        first = client.get("/api/admin/capabilities?live=1")
        second = client.get("/api/admin/capabilities?live=1")
    finally:
        app.dependency_overrides.pop(get_sql_client, None)
        app.dependency_overrides.pop(get_genie_client, None)
        app.dependency_overrides.pop(get_lakebase_client, None)
        reset_live_capability_probe_cache()

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(workspace.api_client.requests) == 2


def test_capabilities_live_probe_cache_key_tracks_gateway_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_a = _settings(
        mip_git_sha=_TEST_GIT_SHA,
        mip_ai_gateway=True,
        mip_ai_gateway_endpoint="mip-agent-gateway-a",
        mip_ai_gateway_inference_table="mip_app_state.mip_sync.mip_agent_inference",
        mip_live_capability_probe_ttl_s=60.0,
    )
    settings_b = _settings(
        mip_git_sha=_TEST_GIT_SHA,
        mip_ai_gateway=True,
        mip_ai_gateway_endpoint="mip-agent-gateway-b",
        mip_ai_gateway_inference_table="mip_app_state.mip_sync.mip_agent_inference",
        mip_live_capability_probe_ttl_s=60.0,
    )
    current = [settings_a]
    workspace = _FakeWorkspaceClient()
    sql = _LiveSqlClient(count=1)
    monkeypatch.setattr(capabilities_module, "get_settings", lambda: current[0])
    monkeypatch.setattr(capability_request_module, "get_settings", lambda: current[0])
    monkeypatch.setattr(capability_request_module, "_workspace_client", lambda: (workspace, None))
    reset_live_capability_probe_cache()
    app.dependency_overrides[get_sql_client] = lambda: sql
    app.dependency_overrides[get_genie_client] = lambda: _LiveGenieClient(ok=True)
    app.dependency_overrides[get_lakebase_client] = lambda: _LiveLakebase.verified()
    try:
        client = TestClient(app)
        first = client.get("/api/admin/capabilities?live=1")
        current[0] = settings_b
        second = client.get("/api/admin/capabilities?live=1")
    finally:
        app.dependency_overrides.pop(get_sql_client, None)
        app.dependency_overrides.pop(get_genie_client, None)
        app.dependency_overrides.pop(get_lakebase_client, None)
        reset_live_capability_probe_cache()

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(workspace.api_client.requests) == 2


def test_capabilities_endpoint_requires_admin() -> None:
    client = TestClient(app)
    resp = client.get("/api/admin/capabilities", headers={"X-Forwarded-Groups": ""})
    assert resp.status_code == 403


def test_query_serving_endpoint_chat_path_omits_temperature() -> None:
    """system.ai HF-served FMs reject temperature=0.0 ("has to be a strictly
    positive float"); the probe must not send temperature at all (observed
    live on mip-agent-gateway / llama_v3_2_3b_instruct, 2026-07-07). The
    proof only needs a bounded round-trip, so the model default is fine."""
    from backend.services.capability_serving_probes import query_serving_endpoint

    class _RecordingApiClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict]] = []

        def do(self, method: str, path: str, *, body: dict):
            self.calls.append((method, path, body))
            return {"choices": [{"message": {"content": "ok"}}]}

    class _Workspace:
        def __init__(self) -> None:
            self.api_client = _RecordingApiClient()

    workspace = _Workspace()
    query_serving_endpoint(
        workspace,
        "mip-agent-gateway",
        prompt="ping",
        client_request_id="mip-capability-x",
        task="llm/v1/chat",
    )
    method, path, body = workspace.api_client.calls[0]
    assert method == "POST"
    assert path == "/serving-endpoints/mip-agent-gateway/invocations"
    assert "temperature" not in body
    assert body["max_tokens"] == 64
    assert body["client_request_id"] == "mip-capability-x"


def test_query_serving_endpoint_uses_raw_invocation_without_sdk_retry() -> None:
    """The probe uses one untyped request so parsing cannot trigger a second inference."""
    from backend.services.capability_serving_probes import (
        query_serving_endpoint,
        serving_response_has_payload,
    )

    class _RecordingApiClient:
        def __init__(self) -> None:
            self.requests: list[tuple[str, str, dict]] = []

        def do(self, method: str, path: str, body=None):
            self.requests.append((method, path, body))
            return [{"output": "ok"}]

    class _Workspace:
        def __init__(self) -> None:
            self.api_client = _RecordingApiClient()

    workspace = _Workspace()
    response = query_serving_endpoint(
        workspace,
        "mip-agent-gateway",
        prompt="ping",
        client_request_id="mip-capability-y",
        task="llm/v1/chat",
    )

    method, path, body = workspace.api_client.requests[0]
    assert method == "POST"
    assert path == "/serving-endpoints/mip-agent-gateway/invocations"
    assert body["messages"] == [{"role": "user", "content": "ping"}]
    assert body["max_tokens"] == 64
    assert body["client_request_id"] == "mip-capability-y"
    assert "temperature" not in body
    assert serving_response_has_payload(response) is True


def test_serving_response_has_payload_accepts_list_response() -> None:
    from backend.services.capability_serving_probes import serving_response_has_payload

    assert serving_response_has_payload([{"output": "ok"}]) is True
    assert serving_response_has_payload([{"generated_text": "ok"}]) is True
    assert serving_response_has_payload([]) is False
    assert serving_response_has_payload({"id": "resp-only"}) is False
    assert (
        serving_response_has_payload(
            {"id": "resp-failed", "status": "failed", "output": [{"content": "not proof"}]}
        )
        is False
    )


def test_count_inference_log_rows_content_matches_when_no_client_request_id_column() -> None:
    """Gateway payload tables evolve their schema on first flush; when the
    evolved schema lacks client_request_id, the nonce embedded in the logged
    request body is the exact-row binding."""
    from backend.services.capability_serving_probes import count_inference_log_rows

    sql = _LiveSqlClient(
        count=3,
        table_names=["mip_agent_gateway_llama_payload"],
        column_names=["databricks_request_id", "request", "response"],
    )
    total = count_inference_log_rows(
        sql,
        "mip.audit.mip_agent_gateway_llama",
        client_request_id="mip-capability-abc123",
    )
    assert total == 3
    count_statement = next(s for s in sql.statements if "COUNT(*)" in s)
    assert "request LIKE :client_request_marker" in count_statement
    assert "client_request_id =" not in count_statement
    count_params = next(
        p for p in sql.parameters if isinstance(p, dict) and "client_request_marker" in p
    )
    assert count_params["client_request_marker"] == "%mip-capability-abc123%"


def test_count_inference_log_rows_returns_zero_for_preflush_stub_table() -> None:
    """Freshly-provisioned payload tables carry only databricks_request_id
    until the first flush (observed live 2026-07-07). Nothing is logged yet:
    report zero without issuing an unresolvable COUNT query so wait loops
    keep polling instead of crashing through the resilience layer."""
    from backend.services.capability_serving_probes import count_inference_log_rows

    sql = _LiveSqlClient(
        count=99,
        table_names=["mip_agent_gateway_llama_payload"],
        column_names=["databricks_request_id"],
    )
    total = count_inference_log_rows(
        sql,
        "mip.audit.mip_agent_gateway_llama",
        client_request_id="mip-capability-abc123",
    )
    assert total == 0
    assert not any("COUNT(*)" in s for s in sql.statements)


def test_count_inference_log_rows_by_prefixes_content_matches_without_column() -> None:
    from backend.services.capability_serving_probes import (
        count_inference_log_rows_by_prefixes,
    )

    sql = _LiveSqlClient(
        count=2,
        table_names=["mip_agent_gateway_llama_payload"],
        column_names=["databricks_request_id", "request"],
    )
    total = count_inference_log_rows_by_prefixes(
        sql,
        "mip.audit.mip_agent_gateway_llama",
        client_request_prefixes=["mip-capability-abc"],
    )
    assert total == 2
    count_statement = next(s for s in sql.statements if "COUNT(*)" in s)
    assert "request LIKE :prefix_0" in count_statement
    count_params = next(p for p in sql.parameters if isinstance(p, dict) and "prefix_0" in p)
    assert count_params["prefix_0"] == "%mip-capability-abc%"

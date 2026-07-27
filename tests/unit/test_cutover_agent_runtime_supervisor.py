from __future__ import annotations

import base64
import io
import json
import shlex
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import ResourceDoesNotExist
from databricks.sdk.service.workspace import ImportFormat

from backend.agents.gateway_contract import (
    DEFAULT_GATEWAY_AGENT_EXPERIMENT,
    gateway_inference_table_family,
    gateway_model_family,
)
from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tools.databricks import app_gateway_access_mode as gateway_access
from tools.databricks import cutover_agent_runtime_supervisor as cutover
from tools.databricks import cutover_retirement_inventory as retirement_inventory
from tools.databricks import cutover_stale_journal_recovery as stale_recovery
from tools.databricks import cutover_supervisor_inventory as supervisor_inventory
from tools.databricks import retired_serving_query_groups as retired_groups
from tools.databricks.agentic_supervisor_endpoint import (
    managed_query_supervisor_replacement_name,
)
from tools.databricks.app_gateway_access_mode import (
    classify_cutover_journal_against_signed_blue,
)
from tools.databricks.cutover_journal_attestation import sign_cutover_journal
from tools.databricks.cutover_journal_store import journal_path
from tools.databricks.gateway_resource_identity import GatewayAgentDeployment
from tools.databricks.provision_gateway_responses_agent import (
    gateway_agent_model_name,
    gateway_agent_source_hash,
    gateway_experiment_name,
    gateway_inference_table_prefix,
    gateway_resource_hash,
)
from tools.databricks.serving_query_group_access import (
    managed_query_group_external_id,
    managed_query_group_name,
)

RUNTIME_ID = "runtime-client"
NEW_ID = "new-supervisor"
NEW_ENDPOINT = "mas-new-endpoint"
NEW_ENDPOINT_ID = "new-endpoint-id"
OLD_ID = "old-supervisor"
OLD_ENDPOINT = "mas-old-endpoint"
OLD_ENDPOINT_ID = "old-endpoint-id"
GATEWAY = "mip-growth-agent-gateway"
OLD_GATEWAY = "mip-growth-agent-gateway-old123456789"
OLD_GATEWAY_ID = "old-gateway-id"
PROXY_CLIENT_ID = "proxy-client"
PROXY_CREDENTIAL_ID = "proxy-credential"
PROXY_SECRET_REFERENCE = "{{secrets/mip-agent-proxy/oauth-client-secret-proxy-credential}}"
SIGNING_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
PREVIOUS_SIGNING_KEY = base64.urlsafe_b64encode(bytes(reversed(range(32)))).decode().rstrip("=")
MODEL_VERIFY_KEY = derive_gateway_proof_verify_key(
    base64.urlsafe_b64encode(b"m" * 32).decode().rstrip("=")
)


def _assert_lease() -> None:
    return None


def _clear_journal(
    workspace: object,
    *,
    assert_single_writer: object = _assert_lease,
) -> None:
    assert callable(assert_single_writer)
    cutover.clear_journal(
        workspace,
        runtime_application_id=RUNTIME_ID,
        app_application_id="app-client",
        app_scim_id="app-scim-id",
        verifier_application_id="verifier-client",
        verifier_scim_id="verifier-scim-id",
        proxy_application_id="proxy-client",
        assert_single_writer=assert_single_writer,
    )


@pytest.fixture(autouse=True)
def _attestation_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv(
        "MIP_AI_GATEWAY_PROOF_VERIFY_KEY",
        derive_gateway_proof_verify_key(SIGNING_KEY),
    )
    monkeypatch.setattr(
        cutover,
        "revoke_managed_app_access",
        lambda *_a, **kw: kw["assert_before_mutation"]() or "managed",
    )
    monkeypatch.setattr(
        cutover,
        "assert_pinned_access_retirement_authority",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        cutover,
        "_retirement_supervisor_by_id",
        lambda _workspace, supervisor_id: cutover._agent_by_id(supervisor_id),
    )


def _endpoint(*, gateway: bool = False) -> object:
    return SimpleNamespace(
        id="gateway-endpoint-id" if gateway else NEW_ENDPOINT_ID,
        creator=RUNTIME_ID,
        state=SimpleNamespace(ready="READY", config_update="NOT_UPDATING"),
        config=(
            SimpleNamespace(
                served_entities=[
                    SimpleNamespace(
                        environment_vars={
                            "MIP_UPSTREAM_SUPERVISOR_ENDPOINT": NEW_ENDPOINT,
                            "MIP_UPSTREAM_PROXY_CLIENT_ID": PROXY_CLIENT_ID,
                            "MIP_UPSTREAM_PROXY_CREDENTIAL_ID": PROXY_CREDENTIAL_ID,
                            "MIP_UPSTREAM_PROXY_CLIENT_SECRET": PROXY_SECRET_REFERENCE,
                            "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY": MODEL_VERIFY_KEY,
                            "MLFLOW_EXPERIMENT_ID": "experiment-7",
                        }
                    )
                ]
            )
            if gateway
            else None
        ),
    )


def _workspace() -> object:
    class _Endpoints:
        def get(self, name: str) -> object:
            if name == NEW_ENDPOINT:
                return _endpoint()
            if name == GATEWAY:
                return _endpoint(gateway=True)
            if name == OLD_ENDPOINT:
                raise ResourceDoesNotExist("deleted")
            raise AssertionError(name)

    return SimpleNamespace(
        workspace=_WorkspaceFiles(),
        serving_endpoints=_Endpoints(),
        apps=SimpleNamespace(
            get=lambda _name: {
                "service_principal_client_id": "app-client",
                "service_principal_id": "app-scim-id",
            }
        ),
    )


def _agents() -> list[dict[str, str]]:
    return [
        {
            "supervisor_agent_id": NEW_ID,
            "display_name": "Mortgage Growth Agent [mip-agent-runtime]",
            "endpoint_name": NEW_ENDPOINT,
            "creator": RUNTIME_ID,
            "create_time": "new-time",
        },
        {
            "supervisor_agent_id": OLD_ID,
            "display_name": "Mortgage Growth Agent",
            "endpoint_name": OLD_ENDPOINT,
            "creator": "skyler@entrada.ai",
            "create_time": "old-time",
        },
    ]


def _green_kwargs() -> dict[str, object]:
    model_family = gateway_model_family(catalog="mip")
    _catalog, schema, table_prefix = gateway_inference_table_family(catalog="mip").split(".", 2)
    source_hash = gateway_agent_source_hash(
        upstream_endpoint=NEW_ENDPOINT,
        catalog="mip",
        genie_space_id="space-123",
    )
    resource_hash = gateway_resource_hash(
        source_hash=source_hash,
        supervisor_id=NEW_ID,
        supervisor_endpoint_id=NEW_ENDPOINT_ID,
        runtime_application_id=RUNTIME_ID,
        model_name=model_family,
        experiment_name=DEFAULT_GATEWAY_AGENT_EXPERIMENT,
        inference_schema=schema,
        inference_table_prefix=table_prefix,
        attestation_verify_key=MODEL_VERIFY_KEY,
        proxy_caller_application_id=PROXY_CLIENT_ID,
        proxy_caller_credential_id=PROXY_CREDENTIAL_ID,
        proxy_caller_secret_reference=PROXY_SECRET_REFERENCE,
    )
    return {
        "canonical_name": "Mortgage Growth Agent",
        "replacement_id": NEW_ID,
        "replacement_endpoint": NEW_ENDPOINT,
        "gateway_endpoint": GATEWAY,
        "gateway_model": gateway_agent_model_name(
            base_model_name=model_family,
            contract_hash=resource_hash,
        ),
        "gateway_model_version": 7,
        "gateway_model_family": model_family,
        "gateway_experiment_base": DEFAULT_GATEWAY_AGENT_EXPERIMENT,
        "gateway_table_prefix": table_prefix,
        "gateway_inference_table": ".".join(
            [
                "mip",
                schema,
                gateway_inference_table_prefix(
                    base_prefix=table_prefix,
                    contract_hash=resource_hash,
                ),
            ]
        ),
        "catalog": "mip",
        "genie_space_id": "space-123",
        "runtime_application_id": RUNTIME_ID,
        "assert_single_writer": lambda: None,
    }


def _install_supervisor_journal(workspace: object) -> None:
    payload = sign_cutover_journal(
        {
            "version": 3,
            "canonical_name": "Mortgage Growth Agent",
            "old_id": OLD_ID,
            "old_endpoint": OLD_ENDPOINT,
            "old_endpoint_id": OLD_ENDPOINT_ID,
            "old_creator": "skyler@entrada.ai",
            "old_create_time": "old-time",
        }
    )
    workspace.workspace.data[journal_path(RUNTIME_ID)] = json.dumps(payload).encode()


def test_retire_rejects_missing_journal_before_any_acl_or_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        cutover,
        "_assert_green_path",
        lambda *_args, **_kwargs: events.append("green-proved"),
    )
    monkeypatch.setattr(
        cutover,
        "revoke_managed_app_access",
        lambda *_args, **_kwargs: events.append("revoke") or True,
    )
    monkeypatch.setattr(cutover, "_run_no_json", lambda *_args, **_kwargs: events.append("delete"))

    with pytest.raises(RuntimeError, match="requires a signed cutover journal"):
        cutover.retire(
            _workspace(),
            app_name="mip-app",
            old_id=OLD_ID,
            old_endpoint=OLD_ENDPOINT,
            old_endpoint_id=OLD_ENDPOINT_ID,
            old_creator="skyler@entrada.ai",
            old_create_time="old-time",
            timeout_s=1,
            **_green_kwargs(),
        )

    assert events == []


def test_retire_rejects_partial_signed_journal_before_any_acl_or_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    partial = sign_cutover_journal(
        {
            "version": 3,
            "canonical_name": "Mortgage Growth Agent",
            "old_id": OLD_ID,
            "old_endpoint": OLD_ENDPOINT,
            "old_creator": "skyler@entrada.ai",
            "old_create_time": "old-time",
        }
    )
    workspace.workspace.data[journal_path(RUNTIME_ID)] = json.dumps(partial).encode()
    events: list[str] = []
    monkeypatch.setattr(
        cutover,
        "_assert_green_path",
        lambda *_args, **_kwargs: events.append("green-proved"),
    )
    monkeypatch.setattr(
        cutover,
        "revoke_managed_app_access",
        lambda *_args, **_kwargs: events.append("revoke") or True,
    )

    with pytest.raises(RuntimeError, match="incomplete Supervisor tuple"):
        cutover.retire(
            workspace,
            app_name="mip-app",
            old_id=OLD_ID,
            old_endpoint=OLD_ENDPOINT,
            old_endpoint_id=OLD_ENDPOINT_ID,
            old_creator="skyler@entrada.ai",
            old_create_time="old-time",
            timeout_s=1,
            **_green_kwargs(),
        )

    assert events == []


def test_retire_rejects_tuple_mismatch_before_any_acl_or_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    _install_supervisor_journal(workspace)
    events: list[str] = []
    monkeypatch.setattr(
        cutover,
        "_assert_green_path",
        lambda *_args, **_kwargs: events.append("green-proved"),
    )
    monkeypatch.setattr(
        cutover,
        "revoke_managed_app_access",
        lambda *_args, **_kwargs: events.append("revoke") or True,
    )
    monkeypatch.setattr(cutover, "_run_no_json", lambda *_args, **_kwargs: events.append("delete"))

    with pytest.raises(RuntimeError, match="does not match the signed journal"):
        cutover.retire(
            workspace,
            app_name="mip-app",
            old_id=OLD_ID,
            old_endpoint=OLD_ENDPOINT,
            old_endpoint_id="attacker-selected-endpoint-id",
            old_creator="skyler@entrada.ai",
            old_create_time="old-time",
            timeout_s=1,
            **_green_kwargs(),
        )

    assert events == []


def test_retire_rejects_unpinned_preserve_endpoint_before_any_acl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    _install_supervisor_journal(workspace)
    events: list[str] = []
    monkeypatch.setattr(
        cutover,
        "_assert_green_path",
        lambda *_args, **_kwargs: events.append("green-proved"),
    )
    monkeypatch.setattr(
        cutover,
        "revoke_managed_app_access",
        lambda *_args, **_kwargs: events.append("revoke") or True,
    )

    with pytest.raises(RuntimeError, match="endpoint absent from the signed journal"):
        cutover.retire(
            workspace,
            app_name="mip-app",
            old_id=OLD_ID,
            old_endpoint=OLD_ENDPOINT,
            old_endpoint_id=OLD_ENDPOINT_ID,
            old_creator="skyler@entrada.ai",
            old_create_time="old-time",
            preserve_endpoint=("unrelated-serving-endpoint",),
            timeout_s=1,
            **_green_kwargs(),
        )

    assert events == []


def test_prepare_then_retire_proves_green_and_deletes_only_pinned_old_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted = False
    events: list[str] = []

    def rows() -> list[dict[str, str]]:
        return [row for row in _agents() if not (deleted and row["supervisor_agent_id"] == OLD_ID)]

    def delete(args: list[str]) -> None:
        nonlocal deleted
        assert args[-1] == f"supervisor-agents/{OLD_ID}"
        events.append("delete-old")
        deleted = True

    def revoke(*_args: object, **kwargs: object) -> str:
        assert kwargs["app_scim_id"] == "app-scim-id"
        kwargs["assert_before_mutation"]()
        events.append("revoke-old")
        return "managed"

    monkeypatch.setattr(cutover, "_supervisor_agents", rows)
    monkeypatch.setattr(
        cutover,
        "_assert_green_path",
        lambda *_args, **_kwargs: events.append("green-proved"),
    )
    monkeypatch.setattr(
        cutover,
        "_converge_app_gateway_permissions",
        lambda *_args, **_kwargs: events.append("grant-outer-revoke-new"),
    )
    monkeypatch.setattr(
        cutover,
        "revoke_managed_app_access",
        revoke,
    )
    monkeypatch.setattr(cutover, "_run_no_json", delete)
    monkeypatch.setattr(
        cutover,
        "_endpoint_identity",
        lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(ResourceDoesNotExist("deleted"))
            if deleted
            else (OLD_ENDPOINT_ID, "skyler@entrada.ai")
        ),
    )

    workspace = _workspace()
    _install_supervisor_journal(workspace)
    cutover.prepare(
        workspace,
        app_name="mip-app",
        verifier_application_id="verifier-client",
        verifier_scim_id="verifier-scim-id",
        **_green_kwargs(),
    )
    cutover.retire(
        workspace,
        app_name="mip-app",
        old_id=OLD_ID,
        old_endpoint=OLD_ENDPOINT,
        old_endpoint_id=OLD_ENDPOINT_ID,
        old_creator="skyler@entrada.ai",
        old_create_time="old-time",
        timeout_s=1,
        **_green_kwargs(),
    )

    assert events == [
        "green-proved",
        "grant-outer-revoke-new",
        "green-proved",
        "revoke-old",
        "delete-old",
    ]


def test_cutover_refuses_changed_old_identity_before_revoke_or_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    changed = _agents()
    changed[1]["endpoint_name"] = "unexpected-endpoint"
    monkeypatch.setattr(cutover, "_supervisor_agents", lambda: changed)
    monkeypatch.setattr(
        cutover,
        "_assert_green_path",
        lambda *_args, **_kwargs: events.append("green-proved"),
    )
    monkeypatch.setattr(
        cutover,
        "revoke_managed_app_access",
        lambda *_args, **_kwargs: events.append("revoke") or True,
    )
    monkeypatch.setattr(
        cutover,
        "_run_no_json",
        lambda *_args, **_kwargs: events.append("delete"),
    )
    monkeypatch.setattr(
        cutover,
        "_endpoint_identity",
        lambda *_args, **_kwargs: (OLD_ENDPOINT_ID, "skyler@entrada.ai"),
    )

    workspace = _workspace()
    _install_supervisor_journal(workspace)
    with pytest.raises(RuntimeError, match="changed after provisioning"):
        cutover.retire(
            workspace,
            app_name="mip-app",
            old_id=OLD_ID,
            old_endpoint=OLD_ENDPOINT,
            old_endpoint_id=OLD_ENDPOINT_ID,
            old_creator="skyler@entrada.ai",
            old_create_time="old-time",
            timeout_s=1,
            **_green_kwargs(),
        )

    assert events == ["green-proved"]


def test_prepare_refuses_supervisor_contract_drift_before_app_acl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(cutover, "_supervisor_agents", lambda: [_agents()[0]])
    monkeypatch.setattr(
        cutover,
        "assert_exact_supervisor_contract",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("Supervisor instructions drifted")
        ),
    )
    monkeypatch.setattr(
        cutover,
        "_converge_app_gateway_permissions",
        lambda *_args, **_kwargs: events.append("acl-mutated"),
    )

    with pytest.raises(RuntimeError, match="instructions drifted"):
        cutover.prepare(
            _workspace(),
            app_name="mip-app",
            verifier_application_id="verifier-client",
            verifier_scim_id="verifier-scim-id",
            **_green_kwargs(),
        )

    assert events == []


def test_green_path_accepts_managed_query_epoch_supervisor_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    replacement_name = managed_query_supervisor_replacement_name(
        "Mortgage Growth Agent",
        genie_space_id="space-123",
        catalog="mip",
    )
    agents = _agents()
    agents[0]["display_name"] = replacement_name
    monkeypatch.setattr(cutover, "_supervisor_agents", lambda: agents)
    monkeypatch.setattr(
        cutover,
        "assert_exact_supervisor_contract",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        cutover,
        "verify_gateway_responses_agent",
        lambda *_a, **_kw: None,
    )
    green = _green_kwargs()
    expected_experiment_name = gateway_experiment_name(
        base_experiment_name=DEFAULT_GATEWAY_AGENT_EXPERIMENT,
        contract_hash=str(green["gateway_model"]).rsplit("_", 1)[-1],
        runtime_application_id=RUNTIME_ID,
    )
    monkeypatch.setattr(
        cutover,
        "MlflowClient",
        lambda **kw: (
            SimpleNamespace(
                get_model_version=lambda *_a: SimpleNamespace(source="models:/reviewed")
            )
            if kw.get("registry_uri")
            else SimpleNamespace(
                get_experiment=lambda _id: SimpleNamespace(
                    name=expected_experiment_name,
                    tags={"mlflow.ownerEmail": RUNTIME_ID},
                ),
                get_experiment_by_name=lambda _name: SimpleNamespace(experiment_id="experiment-7"),
            )
        ),
    )
    workspace.registered_models = SimpleNamespace(
        get=lambda _name: SimpleNamespace(owner=RUNTIME_ID)
    )

    cutover._assert_green_path(
        workspace,
        **green,
    )


def test_green_path_rejects_mlflow_experiment_name_id_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    workspace.registered_models = SimpleNamespace(
        get=lambda _name: SimpleNamespace(owner=RUNTIME_ID)
    )
    experiment = SimpleNamespace(
        experiment_id="experiment-7",
        name=f"/Users/{RUNTIME_ID}/rogue-experiment",
        lifecycle_stage="active",
        tags={"mlflow.ownerEmail": RUNTIME_ID},
    )
    monkeypatch.setattr(cutover, "_supervisor_agents", _agents)
    monkeypatch.setattr(
        cutover,
        "assert_exact_supervisor_contract",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        cutover,
        "MlflowClient",
        lambda **_kwargs: SimpleNamespace(
            get_experiment=lambda _id: experiment,
            get_experiment_by_name=lambda _name: experiment,
        ),
    )

    with pytest.raises(RuntimeError, match="experiment name/ID binding drifted"):
        cutover._assert_green_path(workspace, **_green_kwargs())


def test_green_path_passes_proxy_credential_binding_to_gateway_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    workspace.registered_models = SimpleNamespace(
        get=lambda _name: SimpleNamespace(owner=RUNTIME_ID)
    )
    green = _green_kwargs()
    model_family = str(green["gateway_model_family"])
    source_hash = gateway_agent_source_hash(
        upstream_endpoint=NEW_ENDPOINT,
        catalog="mip",
        genie_space_id="space-123",
    )
    _catalog, schema, table_prefix = gateway_inference_table_family(catalog="mip").split(".", 2)
    resource_hash = gateway_resource_hash(
        source_hash=source_hash,
        supervisor_id=NEW_ID,
        supervisor_endpoint_id=NEW_ENDPOINT_ID,
        runtime_application_id=RUNTIME_ID,
        model_name=model_family,
        experiment_name=DEFAULT_GATEWAY_AGENT_EXPERIMENT,
        inference_schema=schema,
        inference_table_prefix=table_prefix,
        attestation_verify_key=MODEL_VERIFY_KEY,
        proxy_caller_application_id=PROXY_CLIENT_ID,
        proxy_caller_credential_id=PROXY_CREDENTIAL_ID,
        proxy_caller_secret_reference=PROXY_SECRET_REFERENCE,
    )
    experiment = SimpleNamespace(
        experiment_id="experiment-7",
        name=gateway_experiment_name(
            base_experiment_name=DEFAULT_GATEWAY_AGENT_EXPERIMENT,
            contract_hash=resource_hash,
            runtime_application_id=RUNTIME_ID,
        ),
        lifecycle_stage="active",
        tags={"mlflow.ownerEmail": RUNTIME_ID},
    )
    mlflow = SimpleNamespace(
        get_experiment=lambda _id: experiment,
        get_experiment_by_name=lambda _name: experiment,
        get_model_version=lambda _name, _version: SimpleNamespace(source="models:/m-model-source"),
    )
    verified: list[GatewayAgentDeployment] = []
    monkeypatch.setattr(cutover, "_supervisor_agents", _agents)
    monkeypatch.setattr(
        cutover,
        "assert_exact_supervisor_contract",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(cutover, "MlflowClient", lambda **_kwargs: mlflow)
    monkeypatch.setattr(
        cutover,
        "verify_gateway_responses_agent",
        lambda _workspace, deployment, **_kwargs: verified.append(deployment),
    )

    cutover._assert_green_path(workspace, **green)

    assert len(verified) == 1
    deployment = verified[0]
    assert deployment.proxy_caller_application_id == PROXY_CLIENT_ID
    assert deployment.proxy_caller_credential_id == PROXY_CREDENTIAL_ID
    assert deployment.proxy_caller_secret_reference == PROXY_SECRET_REFERENCE


def test_retire_cleans_pinned_orphan_after_interrupted_agent_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orphan_deleted = False
    events: list[tuple[str, bool]] = []

    class _Endpoints:
        def get(self, name: str) -> object:
            if name != OLD_ENDPOINT or orphan_deleted:
                raise ResourceDoesNotExist("deleted")
            return SimpleNamespace(id=OLD_ENDPOINT_ID, creator="skyler@entrada.ai")

        def delete(self, name: str) -> None:
            nonlocal orphan_deleted
            assert name == OLD_ENDPOINT
            orphan_deleted = True

    workspace = SimpleNamespace(
        workspace=_WorkspaceFiles(),
        serving_endpoints=_Endpoints(),
        apps=SimpleNamespace(
            get=lambda _name: {
                "service_principal_client_id": "app-client",
                "service_principal_id": "app-scim-id",
            }
        ),
    )
    _install_supervisor_journal(workspace)
    monkeypatch.setattr(cutover, "_supervisor_agents", lambda: [_agents()[0]])
    monkeypatch.setattr(cutover, "_assert_green_path", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cutover,
        "revoke_managed_app_access",
        lambda *_args, **kwargs: (
            kwargs["assert_before_mutation"](),
            events.append((kwargs["endpoint_name"], kwargs["missing_ok"])),
            "managed",
        )[-1],
    )

    cutover.retire(
        workspace,
        app_name="mip-app",
        old_id=OLD_ID,
        old_endpoint=OLD_ENDPOINT,
        old_endpoint_id=OLD_ENDPOINT_ID,
        old_creator="skyler@entrada.ai",
        old_create_time="old-time",
        timeout_s=1,
        **_green_kwargs(),
    )

    assert orphan_deleted is True
    assert events == [(OLD_ENDPOINT, True)]


def test_finalize_renames_only_runtime_owned_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_agents()[0]]
    updates: list[list[str]] = []

    def update(args: list[str]) -> None:
        updates.append(args)
        rows[0]["display_name"] = "Mortgage Growth Agent"

    monkeypatch.setattr(cutover, "_supervisor_agents", lambda: rows)
    monkeypatch.setattr(cutover, "assert_current_runtime_identity", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        cutover,
        "assert_exact_supervisor_contract",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        cutover,
        "assert_unique_live_supervisor_binding",
        lambda *_args, **_kwargs: f"{NEW_ENDPOINT}-id",
    )
    monkeypatch.setattr(cutover, "_run_no_json", update)

    cutover.finalize(
        SimpleNamespace(),
        assert_single_writer=lambda: None,
        canonical_name="Mortgage Growth Agent",
        replacement_id=NEW_ID,
        replacement_endpoint=NEW_ENDPOINT,
        runtime_application_id=RUNTIME_ID,
        catalog="mip",
        genie_space_id="space-123",
    )

    assert updates == [
        [
            "supervisor-agents",
            "update-supervisor-agent",
            f"supervisor-agents/{NEW_ID}",
            "display_name",
            "Mortgage Growth Agent",
        ]
    ]


def test_finalize_rejects_canonical_collision_during_rename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_agents()[0]]

    class _Api:
        def do(
            self,
            method: str,
            path: str,
            *,
            query: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            assert method == "GET"
            if path == "/api/2.1/supervisor-agents":
                assert query == {"page_size": 100}
                return {"supervisor_agents": [dict(row) for row in rows]}
            supervisor_id = path.rsplit("/", 1)[-1]
            return next(dict(row) for row in rows if row["supervisor_agent_id"] == supervisor_id)

    workspace = SimpleNamespace(
        api_client=_Api(),
        serving_endpoints=SimpleNamespace(
            get=lambda _endpoint: SimpleNamespace(
                id=f"{NEW_ENDPOINT}-id",
                creator=RUNTIME_ID,
            )
        ),
    )

    def collide(_args: list[str]) -> None:
        rows[0]["display_name"] = "Mortgage Growth Agent"
        rows.append(
            {
                "supervisor_agent_id": "intruder",
                "display_name": "Mortgage Growth Agent",
                "endpoint_name": "intruder-endpoint",
                "creator": RUNTIME_ID,
            }
        )

    monkeypatch.setattr(cutover, "_supervisor_agents", lambda: rows)
    monkeypatch.setattr(
        cutover,
        "assert_current_runtime_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(cutover, "_run_no_json", collide)

    with pytest.raises(RuntimeError, match="absent or duplicated"):
        cutover.finalize(
            workspace,
            assert_single_writer=lambda: None,
            canonical_name="Mortgage Growth Agent",
            replacement_id=NEW_ID,
            replacement_endpoint=NEW_ENDPOINT,
            runtime_application_id=RUNTIME_ID,
            catalog="mip",
            genie_space_id="space-123",
        )


def test_finalize_resolves_committed_rename_with_lost_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_agents()[0]]
    proof_reads = 0

    def commit_then_timeout(_args: list[str]) -> None:
        rows[0]["display_name"] = "Mortgage Growth Agent"
        raise TimeoutError("provider response lost")

    def prove(*_args: object, **_kwargs: object) -> str:
        nonlocal proof_reads
        proof_reads += 1
        assert rows[0]["display_name"] == "Mortgage Growth Agent"
        return f"{NEW_ENDPOINT}-id"

    monkeypatch.setattr(cutover, "_supervisor_agents", lambda: rows)
    monkeypatch.setattr(
        cutover,
        "assert_current_runtime_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(cutover, "_run_no_json", commit_then_timeout)
    monkeypatch.setattr(
        cutover,
        "assert_exact_supervisor_contract",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        cutover,
        "assert_unique_live_supervisor_binding",
        prove,
    )

    cutover.finalize(
        SimpleNamespace(),
        assert_single_writer=lambda: None,
        canonical_name="Mortgage Growth Agent",
        replacement_id=NEW_ID,
        replacement_endpoint=NEW_ENDPOINT,
        runtime_application_id=RUNTIME_ID,
        catalog="mip",
        genie_space_id="space-123",
    )

    assert proof_reads == 3


class _WorkspaceFiles:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.upload_count = 0

    def mkdirs(self, _path: str) -> None:
        return None

    def upload(
        self,
        path: str,
        content: io.BytesIO,
        *,
        format: ImportFormat,
        overwrite: bool,
    ) -> None:
        assert format is ImportFormat.AUTO
        assert overwrite is True
        self.upload_count += 1
        self.data[path] = content.read()

    def download(self, path: str) -> io.BytesIO:
        if path not in self.data:
            raise ResourceDoesNotExist("missing")
        return io.BytesIO(self.data[path])

    def delete(self, path: str) -> None:
        if path not in self.data:
            raise ResourceDoesNotExist("missing")
        del self.data[path]


def _journal_workspace(*, endpoint_id: str = OLD_ENDPOINT_ID) -> object:
    files = _WorkspaceFiles()
    endpoint_deleted = False

    class _Endpoints:
        def get(self, name: str) -> object:
            if name != OLD_ENDPOINT or endpoint_deleted:
                raise ResourceDoesNotExist("missing")
            return SimpleNamespace(id=endpoint_id, creator="skyler@entrada.ai")

        def delete(self, name: str) -> None:
            nonlocal endpoint_deleted
            assert name == OLD_ENDPOINT
            endpoint_deleted = True

        def list(self) -> list[object]:
            if endpoint_deleted:
                return []
            return [
                SimpleNamespace(
                    name=OLD_ENDPOINT,
                    id=endpoint_id,
                    creator="skyler@entrada.ai",
                )
            ]

    class _Api:
        def do(
            self,
            method: str,
            path: str,
            *,
            query: object | None = None,
        ) -> object:
            assert method == "GET"
            if path == "/api/2.1/supervisor-agents":
                assert query == {"page_size": 100}
                return {"supervisor_agents": []}
            raise ResourceDoesNotExist("missing")

    return SimpleNamespace(
        workspace=files,
        serving_endpoints=_Endpoints(),
        api_client=_Api(),
        groups=SimpleNamespace(list=lambda **_kwargs: []),
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(
                user_name=RUNTIME_ID,
                display_name="mip-agent-runtime-ci-sp",
            )
        ),
        apps=SimpleNamespace(
            get=lambda _name: {
                "service_principal_client_id": "app-client",
                "service_principal_id": "app-scim-id",
            }
        ),
    )


def _pin_test_journal(
    workspace: object,
    monkeypatch: pytest.MonkeyPatch,
) -> str:
    monkeypatch.setattr(cutover, "_supervisor_agents", _agents)
    cutover.pin_journal(
        workspace,
        assert_single_writer=_assert_lease,
        runtime_application_id=RUNTIME_ID,
        canonical_name="Mortgage Growth Agent",
        old_id=OLD_ID,
        old_endpoint=OLD_ENDPOINT,
        old_creator="skyler@entrada.ai",
        old_create_time="old-time",
    )
    workspace.serving_endpoints.delete(OLD_ENDPOINT)
    return journal_path(RUNTIME_ID)


def _replace_signed_journal(workspace: object, path: str) -> None:
    current = json.loads(workspace.workspace.data[path])
    replacement = sign_cutover_journal(
        {
            key: value
            for key, value in current.items()
            if key
            not in {
                "attestation_alg",
                "attestation_verify_key",
                "attestation_signature",
            }
        }
        | {"old_endpoint_id": "replacement-endpoint-id"}
    )
    workspace.workspace.data[path] = json.dumps(replacement, sort_keys=True).encode()


def _signed_blue_pins() -> tuple[dict[str, str], dict[str, str]]:
    return (
        {
            "name": GATEWAY,
            "endpoint_id": "gateway-endpoint-id",
            "creator": RUNTIME_ID,
        },
        {
            "supervisor_id": NEW_ID,
            "endpoint": NEW_ENDPOINT,
            "endpoint_id": NEW_ENDPOINT_ID,
            "creator": RUNTIME_ID,
        },
    )


def _stale_journal_workspace(
    *,
    old_endpoint_name: str | None = None,
    old_endpoint_id: str = OLD_ENDPOINT_ID,
    supervisors: tuple[dict[str, str], ...] | None = None,
    direct_supervisors: tuple[dict[str, str], ...] | None = None,
    endpoint_list: list[object] | None = None,
    groups: list[object] | None = None,
) -> object:
    endpoints = {
        GATEWAY: SimpleNamespace(
            name=GATEWAY,
            id="gateway-endpoint-id",
            creator=RUNTIME_ID,
        ),
        NEW_ENDPOINT: SimpleNamespace(
            name=NEW_ENDPOINT,
            id=NEW_ENDPOINT_ID,
            creator=RUNTIME_ID,
        ),
    }
    if old_endpoint_name is not None:
        endpoints[old_endpoint_name] = SimpleNamespace(
            name=old_endpoint_name,
            id=old_endpoint_id,
            creator="skyler@entrada.ai",
        )

    class _Endpoints:
        def get(self, name: str) -> object:
            try:
                return endpoints[name]
            except KeyError as exc:
                raise ResourceDoesNotExist("missing") from exc

        def list(self) -> list[object]:
            return list(endpoints.values()) if endpoint_list is None else endpoint_list

        def delete(self, name: str) -> None:
            if name not in endpoints:
                raise ResourceDoesNotExist("missing")
            del endpoints[name]

    supervisor_rows = (_agents()[0],) if supervisors is None else supervisors
    direct_rows = supervisor_rows if direct_supervisors is None else direct_supervisors

    class _Api:
        def do(
            self,
            method: str,
            path: str,
            *,
            query: object | None = None,
        ) -> object:
            assert method == "GET"
            if path == "/api/2.1/supervisor-agents":
                assert query == {"page_size": 100}
                return {"supervisor_agents": list(supervisor_rows)}
            supervisor_id = path.rsplit("/", 1)[-1]
            match = next(
                (row for row in direct_rows if row["supervisor_agent_id"] == supervisor_id),
                None,
            )
            if match is None:
                raise ResourceDoesNotExist("missing")
            return match

    workspace = SimpleNamespace(
        workspace=_WorkspaceFiles(),
        serving_endpoints=_Endpoints(),
        api_client=_Api(),
        groups=SimpleNamespace(
            list=lambda **_kwargs: list(groups or []),
            get=lambda group_id: next(
                group for group in (groups or []) if getattr(group, "id", None) == group_id
            ),
        ),
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(
                user_name=RUNTIME_ID,
                display_name="mip-agent-runtime-ci-sp",
            )
        ),
        apps=SimpleNamespace(
            get=lambda _name: {
                "service_principal_client_id": "app-client",
                "service_principal_id": "app-scim-id",
            }
        ),
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: [
                SimpleNamespace(
                    application_id="verifier-client",
                    id="verifier-scim-id",
                ),
                SimpleNamespace(
                    application_id="proxy-client",
                    id="proxy-scim-id",
                ),
            ]
        ),
    )
    _install_supervisor_journal(workspace)
    return workspace


def _install_signed_blue_pin_env(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway, supervisor = _signed_blue_pins()
    monkeypatch.setenv(
        "MIP_CUTOVER_SIGNED_BLUE_GATEWAY_PIN_JSON",
        json.dumps(gateway, sort_keys=True, separators=(",", ":")),
    )
    monkeypatch.setenv(
        "MIP_CUTOVER_SIGNED_BLUE_SUPERVISOR_PIN_JSON",
        json.dumps(supervisor, sort_keys=True, separators=(",", ":")),
    )


def _managed_group(
    *,
    endpoint_id: str,
    application_id: str,
    principal_id: str | None,
) -> object:
    return SimpleNamespace(
        id=f"group-{application_id}",
        display_name=managed_query_group_name(
            endpoint_id=endpoint_id,
            application_id=application_id,
        ),
        external_id=managed_query_group_external_id(
            endpoint_id=endpoint_id,
            application_id=application_id,
        ),
        members=([SimpleNamespace(value=principal_id)] if principal_id is not None else []),
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
    )


def test_direct_supervisor_inventory_consumes_every_page_and_hydrates_exact_rows() -> None:
    first, second = _agents()
    calls: list[tuple[str, object | None]] = []

    class _Api:
        def do(
            self,
            method: str,
            path: str,
            *,
            query: object | None = None,
        ) -> object:
            assert method == "GET"
            calls.append((path, query))
            if path.endswith(f"/{NEW_ID}"):
                return first
            if path.endswith(f"/{OLD_ID}"):
                return second
            if query == {"page_size": 100}:
                return {
                    "supervisor_agents": [first],
                    "next_page_token": "second-page",
                }
            if query == {"page_size": 100, "page_token": "second-page"}:
                return {"supervisor_agents": [second]}
            raise AssertionError((path, query))

    assert supervisor_inventory.supervisor_inventory_direct(SimpleNamespace(api_client=_Api())) == (
        first,
        second,
    )
    assert calls == [
        ("/api/2.1/supervisor-agents", {"page_size": 100}),
        (f"/api/2.1/supervisor-agents/{NEW_ID}", None),
        (
            "/api/2.1/supervisor-agents",
            {"page_size": 100, "page_token": "second-page"},
        ),
        (f"/api/2.1/supervisor-agents/{OLD_ID}", None),
    ]


def test_retirement_supervisor_lookup_uses_direct_immutable_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _agents()[1]
    monkeypatch.setattr(
        cutover,
        "_retirement_supervisor_by_id",
        lambda workspace, supervisor_id: cutover._supervisor_by_id_direct(
            workspace,
            supervisor_id,
        ),
    )
    workspace = SimpleNamespace(
        api_client=SimpleNamespace(
            do=lambda method, path: (
                row
                if method == "GET" and path.endswith(f"/{OLD_ID}")
                else pytest.fail("unexpected direct Supervisor lookup")
            )
        )
    )

    assert cutover._retirement_supervisor_by_id(workspace, OLD_ID) == row


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "malformed"),
        ({"supervisor_agents": [{}]}, "missing identity"),
        (
            {"supervisor_agents": [], "next_page_token": " bad "},
            "page token",
        ),
    ],
)
def test_direct_supervisor_inventory_rejects_malformed_pages(
    payload: dict[str, object],
    message: str,
) -> None:
    api = SimpleNamespace(do=lambda *_args, **_kwargs: payload)

    with pytest.raises(RuntimeError, match=message):
        supervisor_inventory.supervisor_inventory_direct(SimpleNamespace(api_client=api))


def test_direct_supervisor_inventory_rejects_duplicate_identity_across_pages() -> None:
    row = _agents()[0]

    class _Api:
        def do(
            self,
            _method: str,
            path: str,
            *,
            query: object | None = None,
        ) -> object:
            if path.endswith(f"/{NEW_ID}"):
                return row
            if query == {"page_size": 100}:
                return {"supervisor_agents": [row], "next_page_token": "second"}
            return {"supervisor_agents": [row]}

    with pytest.raises(RuntimeError, match="duplicate identity"):
        supervisor_inventory.supervisor_inventory_direct(SimpleNamespace(api_client=_Api()))


def test_direct_supervisor_inventory_enforces_hard_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(supervisor_inventory, "MAX_CUTOVER_SUPERVISORS", 1)
    api = SimpleNamespace(
        do=lambda _method, path, **_kwargs: (
            {"supervisor_agents": _agents()}
            if path == "/api/2.1/supervisor-agents"
            else next(row for row in _agents() if path.endswith(f"/{row['supervisor_agent_id']}"))
        )
    )

    with pytest.raises(RuntimeError, match="exceeds the reviewed bound"):
        supervisor_inventory.supervisor_inventory_direct(SimpleNamespace(api_client=api))


def test_cutover_journal_classifies_same_contract_retry_predecessor_as_stale() -> None:
    gateway, supervisor = _signed_blue_pins()

    assert (
        classify_cutover_journal_against_signed_blue(
            journal_gateway_pin=None,
            journal_supervisor_pin={
                "supervisor_id": OLD_ID,
                "endpoint": OLD_ENDPOINT,
                "endpoint_id": OLD_ENDPOINT_ID,
                "creator": "skyler@entrada.ai",
            },
            signed_blue_gateway_pin=gateway,
            signed_blue_supervisor_pin=supervisor,
        )
        == "stale"
    )


def test_cutover_journal_classifies_pre_capture_signed_blue_as_current() -> None:
    gateway, supervisor = _signed_blue_pins()

    assert (
        classify_cutover_journal_against_signed_blue(
            journal_gateway_pin=None,
            journal_supervisor_pin=supervisor,
            signed_blue_gateway_pin=gateway,
            signed_blue_supervisor_pin=supervisor,
        )
        == "current"
    )


def test_new_contract_roll_forward_classifies_preceding_release_journal_as_stale() -> None:
    gateway, supervisor = _signed_blue_pins()

    assert (
        classify_cutover_journal_against_signed_blue(
            journal_gateway_pin={
                "name": OLD_GATEWAY,
                "endpoint_id": OLD_GATEWAY_ID,
                "creator": RUNTIME_ID,
            },
            journal_supervisor_pin={
                "supervisor_id": OLD_ID,
                "endpoint": OLD_ENDPOINT,
                "endpoint_id": OLD_ENDPOINT_ID,
                "creator": "skyler@entrada.ai",
            },
            signed_blue_gateway_pin=gateway,
            signed_blue_supervisor_pin=supervisor,
        )
        == "stale"
    )


@pytest.mark.parametrize(
    "journal_pin",
    [
        {
            "supervisor_id": NEW_ID,
            "endpoint": "reused-name",
            "endpoint_id": "reused-id",
            "creator": RUNTIME_ID,
        },
        {
            "supervisor_id": "other-supervisor",
            "endpoint": NEW_ENDPOINT,
            "endpoint_id": "other-endpoint-id",
            "creator": RUNTIME_ID,
        },
        {
            "supervisor_id": "other-supervisor",
            "endpoint": "other-endpoint",
            "endpoint_id": NEW_ENDPOINT_ID,
            "creator": RUNTIME_ID,
        },
    ],
)
def test_cutover_journal_rejects_signed_blue_name_or_id_reuse(
    journal_pin: dict[str, str],
) -> None:
    gateway, supervisor = _signed_blue_pins()

    with pytest.raises(RuntimeError, match="reuses|collides"):
        classify_cutover_journal_against_signed_blue(
            journal_gateway_pin=None,
            journal_supervisor_pin=journal_pin,
            signed_blue_gateway_pin=gateway,
            signed_blue_supervisor_pin=supervisor,
        )


def test_fresh_process_clears_stale_journal_only_after_exact_old_resources_are_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _stale_journal_workspace()
    monkeypatch.setattr(cutover, "_supervisor_agents", lambda: [_agents()[0]])
    _install_signed_blue_pin_env(monkeypatch)

    _clear_journal(workspace)

    assert journal_path(RUNTIME_ID) not in workspace.workspace.data


def test_fresh_process_preserves_stale_journal_while_old_endpoint_is_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _stale_journal_workspace(old_endpoint_name=OLD_ENDPOINT)
    monkeypatch.setattr(cutover, "_supervisor_agents", lambda: [_agents()[0]])
    _install_signed_blue_pin_env(monkeypatch)

    with pytest.raises(RuntimeError, match="endpoint is not retired"):
        _clear_journal(workspace)

    assert journal_path(RUNTIME_ID) in workspace.workspace.data


def test_fresh_process_resumes_partial_stale_retirement_before_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _stale_journal_workspace(old_endpoint_name=OLD_ENDPOINT)
    _install_signed_blue_pin_env(monkeypatch)
    retired_group_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(cutover, "_supervisor_agents", lambda: [_agents()[0]])

    def retire_groups(
        _workspace: object,
        *,
        endpoint_name: str,
        endpoint_id: str,
        **_kwargs: object,
    ) -> None:
        retired_group_calls.append((endpoint_name, endpoint_id))

    monkeypatch.setattr(stale_recovery, "retire_endpoint_query_groups", retire_groups)
    monkeypatch.setattr(
        stale_recovery,
        "revoke_managed_app_access",
        lambda *_args, **kwargs: kwargs["assert_before_mutation"]() or "managed",
    )
    monkeypatch.setattr(
        retired_groups,
        "inspect_gateway_query_access_mode",
        lambda *_args, **_kwargs: "none",
    )

    stale_recovery.resume_stale_journal_retirement(
        workspace,
        runtime_application_id=RUNTIME_ID,
        app_name="mip-app",
        app_application_id="app-client",
        verifier_application_id="verifier-client",
        verifier_scim_id="verifier-scim-id",
        proxy_application_id="proxy-client",
        timeout_s=1,
        assert_single_writer=_assert_lease,
    )
    _clear_journal(workspace)

    assert retired_group_calls == [(OLD_ENDPOINT, OLD_ENDPOINT_ID)]
    assert journal_path(RUNTIME_ID) not in workspace.workspace.data


def test_stale_retirement_recovery_proves_signed_blue_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _stale_journal_workspace(old_endpoint_name=OLD_ENDPOINT)
    _install_signed_blue_pin_env(monkeypatch)
    workspace.serving_endpoints.get(GATEWAY).id = "drifted-blue-endpoint-id"
    mutations: list[str] = []
    monkeypatch.setattr(
        stale_recovery,
        "delete_pinned_gateway",
        lambda *_args, **_kwargs: mutations.append("gateway"),
    )
    monkeypatch.setattr(
        stale_recovery,
        "retire_pinned_supervisor",
        lambda *_args, **_kwargs: mutations.append("supervisor"),
    )

    with pytest.raises(RuntimeError, match="signed-blue Gateway endpoint identity drifted"):
        stale_recovery.resume_stale_journal_retirement(
            workspace,
            runtime_application_id=RUNTIME_ID,
            app_name="mip-app",
            app_application_id="app-client",
            verifier_application_id="verifier-client",
            verifier_scim_id="verifier-scim-id",
            proxy_application_id="proxy-client",
            timeout_s=1,
            assert_single_writer=_assert_lease,
        )

    assert mutations == []


def test_stale_retirement_recovery_rejects_verifier_scim_drift_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _stale_journal_workspace(old_endpoint_name=OLD_ENDPOINT)
    _install_signed_blue_pin_env(monkeypatch)
    workspace.service_principals.list = lambda **_kwargs: [
        SimpleNamespace(
            application_id="verifier-client",
            id="replacement-verifier-scim-id",
        ),
        SimpleNamespace(application_id="proxy-client", id="proxy-scim-id"),
    ]
    mutations: list[str] = []
    monkeypatch.setattr(
        stale_recovery,
        "delete_pinned_gateway",
        lambda *_args, **_kwargs: mutations.append("gateway"),
    )
    monkeypatch.setattr(
        stale_recovery,
        "retire_pinned_supervisor",
        lambda *_args, **_kwargs: mutations.append("supervisor"),
    )

    with pytest.raises(RuntimeError, match="verifier service-principal SCIM ID drifted"):
        stale_recovery.resume_stale_journal_retirement(
            workspace,
            runtime_application_id=RUNTIME_ID,
            app_name="mip-app",
            app_application_id="app-client",
            verifier_application_id="verifier-client",
            verifier_scim_id="verifier-scim-id",
            proxy_application_id="proxy-client",
            timeout_s=1,
            assert_single_writer=_assert_lease,
        )

    assert mutations == []


def test_stale_clear_rejects_old_endpoint_omitted_from_list_but_live_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _stale_journal_workspace(
        old_endpoint_name=OLD_ENDPOINT,
        endpoint_list=[
            SimpleNamespace(name=GATEWAY),
            SimpleNamespace(name=NEW_ENDPOINT),
        ],
    )
    _install_signed_blue_pin_env(monkeypatch)

    with pytest.raises(RuntimeError, match="endpoint is not retired"):
        _clear_journal(workspace)

    assert journal_path(RUNTIME_ID) in workspace.workspace.data


def test_stale_clear_rejects_signed_blue_omitted_from_complete_supervisor_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _stale_journal_workspace(
        supervisors=(),
        direct_supervisors=(_agents()[0],),
    )
    _install_signed_blue_pin_env(monkeypatch)

    with pytest.raises(RuntimeError, match="omitted or drifted"):
        _clear_journal(workspace)

    assert journal_path(RUNTIME_ID) in workspace.workspace.data


def test_stale_clear_rejects_old_supervisor_omitted_from_list_but_live_by_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _stale_journal_workspace(
        supervisors=(_agents()[0],),
        direct_supervisors=tuple(_agents()),
    )
    _install_signed_blue_pin_env(monkeypatch)

    with pytest.raises(RuntimeError, match="Supervisor is not retired"):
        _clear_journal(workspace)

    assert journal_path(RUNTIME_ID) in workspace.workspace.data


@pytest.mark.parametrize(
    "endpoint_list",
    [
        [SimpleNamespace(name="")],
        [SimpleNamespace(name=GATEWAY), SimpleNamespace(name=GATEWAY)],
    ],
    ids=["blank", "duplicate"],
)
def test_stale_clear_rejects_malformed_complete_endpoint_inventory(
    monkeypatch: pytest.MonkeyPatch,
    endpoint_list: list[object],
) -> None:
    workspace = _stale_journal_workspace(endpoint_list=endpoint_list)
    _install_signed_blue_pin_env(monkeypatch)

    with pytest.raises(RuntimeError, match="duplicate or missing name"):
        _clear_journal(workspace)

    assert journal_path(RUNTIME_ID) in workspace.workspace.data


def test_cutover_inventory_excludes_valid_platform_foundation_endpoint() -> None:
    foundation = SimpleNamespace(
        id=None,
        creator=None,
        config=SimpleNamespace(
            served_entities=[
                SimpleNamespace(
                    foundation_model=SimpleNamespace(name="system.ai.meta_llama_v3_3_70b_instruct")
                )
            ]
        ),
    )
    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(
            list=lambda: [SimpleNamespace(name="databricks-llama")],
            get=lambda _name: foundation,
        )
    )

    assert retirement_inventory.validated_cutover_endpoint_inventory(workspace) == ()


def test_stale_clear_enforces_complete_endpoint_inventory_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _stale_journal_workspace()
    monkeypatch.setattr(retirement_inventory, "MAX_CUTOVER_ENDPOINT_INVENTORY", 1)
    _install_signed_blue_pin_env(monkeypatch)

    with pytest.raises(RuntimeError, match="exceeds the reviewed bound"):
        _clear_journal(workspace)

    assert journal_path(RUNTIME_ID) in workspace.workspace.data


def test_stale_clear_preserves_journal_while_endpoint_bound_group_is_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _stale_journal_workspace(
        groups=[
            _managed_group(
                endpoint_id=OLD_ENDPOINT_ID,
                application_id="proxy-client",
                principal_id="proxy-scim-id",
            )
        ]
    )
    _install_signed_blue_pin_env(monkeypatch)

    with pytest.raises(RuntimeError, match="Supervisor proxy query group is not retired"):
        _clear_journal(workspace)

    assert journal_path(RUNTIME_ID) in workspace.workspace.data


def test_clear_accepts_exact_nondeletable_gateway_only_after_access_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = SimpleNamespace(
        name=OLD_GATEWAY,
        id=OLD_GATEWAY_ID,
        creator="legacy-owner",
    )
    groups = [
        _managed_group(
            endpoint_id=OLD_GATEWAY_ID,
            application_id=application_id,
            principal_id=None,
        )
        for application_id in ("app-client", "verifier-client")
    ]
    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(
            list=lambda: [endpoint],
            get=lambda name: (
                endpoint
                if name == OLD_GATEWAY
                else (_ for _ in ()).throw(ResourceDoesNotExist("missing"))
            ),
        ),
        groups=SimpleNamespace(
            list=lambda **_kwargs: groups,
            get=lambda group_id: next(group for group in groups if group.id == group_id),
        ),
    )
    inspected: list[tuple[str, str]] = []
    monkeypatch.setattr(
        gateway_access,
        "inspect_gateway_query_access_mode",
        lambda *_args, **kwargs: (
            inspected.append((kwargs["application_id"], kwargs["scim_id"])),
            "none",
        )[1],
    )

    gateway_access.assert_cutover_journal_retired(
        workspace,
        journal={
            "canonical_name": "Mortgage Growth Agent",
            "old_gateway_endpoint": OLD_GATEWAY,
            "old_gateway_endpoint_id": OLD_GATEWAY_ID,
            "old_gateway_creator": "legacy-owner",
            "old_gateway_delete_allowed": "0",
        },
        supervisor_by_id=lambda _supervisor_id: None,
        supervisor_inventory=lambda: (),
        app_application_id="app-client",
        app_scim_id="app-scim-id",
        verifier_application_id="verifier-client",
        verifier_scim_id="verifier-scim-id",
        proxy_application_id="proxy-client",
    )

    assert inspected == [
        ("app-client", "app-scim-id"),
        ("verifier-client", "verifier-scim-id"),
    ]


def test_clear_rejects_nondeletable_gateway_with_remaining_query_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = SimpleNamespace(
        name=OLD_GATEWAY,
        id=OLD_GATEWAY_ID,
        creator="legacy-owner",
    )
    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(
            list=lambda: [endpoint],
            get=lambda _name: endpoint,
        ),
        groups=SimpleNamespace(list=lambda **_kwargs: []),
    )
    monkeypatch.setattr(
        gateway_access,
        "inspect_gateway_query_access_mode",
        lambda *_args, **kwargs: ("managed" if kwargs["identity_label"] == "App" else "none"),
    )

    with pytest.raises(RuntimeError, match="still authorizes App query access"):
        gateway_access.assert_cutover_journal_retired(
            workspace,
            journal={
                "canonical_name": "Mortgage Growth Agent",
                "old_gateway_endpoint": OLD_GATEWAY,
                "old_gateway_endpoint_id": OLD_GATEWAY_ID,
                "old_gateway_creator": "legacy-owner",
                "old_gateway_delete_allowed": "0",
            },
            supervisor_by_id=lambda _supervisor_id: None,
            supervisor_inventory=lambda: (),
            app_application_id="app-client",
            app_scim_id="app-scim-id",
            verifier_application_id="verifier-client",
            verifier_scim_id="verifier-scim-id",
            proxy_application_id="proxy-client",
        )


def test_clear_rejects_orphan_group_for_absent_nondeletable_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = _managed_group(
        endpoint_id=OLD_GATEWAY_ID,
        application_id="app-client",
        principal_id=None,
    )
    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(
            list=lambda: [],
            get=lambda _name: (_ for _ in ()).throw(ResourceDoesNotExist("missing")),
        ),
        groups=SimpleNamespace(
            list=lambda **_kwargs: [group],
            get=lambda _group_id: group,
        ),
    )
    monkeypatch.setattr(
        gateway_access,
        "inspect_gateway_query_access_mode",
        lambda *_args, **_kwargs: pytest.fail("absent Gateway must not be inspected"),
    )

    with pytest.raises(RuntimeError, match="Gateway App query group is not retired"):
        gateway_access.assert_cutover_journal_retired(
            workspace,
            journal={
                "canonical_name": "Mortgage Growth Agent",
                "old_gateway_endpoint": OLD_GATEWAY,
                "old_gateway_endpoint_id": OLD_GATEWAY_ID,
                "old_gateway_creator": "legacy-owner",
                "old_gateway_delete_allowed": "0",
            },
            supervisor_by_id=lambda _supervisor_id: None,
            supervisor_inventory=lambda: (),
            app_application_id="app-client",
            app_scim_id="app-scim-id",
            verifier_application_id="verifier-client",
            verifier_scim_id="verifier-scim-id",
            proxy_application_id="proxy-client",
        )


@pytest.mark.parametrize(
    ("endpoint_name", "endpoint_id"),
    [
        (OLD_ENDPOINT, "reused-endpoint-id"),
        ("renamed-endpoint", OLD_ENDPOINT_ID),
    ],
)
def test_fresh_process_rejects_old_endpoint_name_or_id_reuse_before_journal_clear(
    monkeypatch: pytest.MonkeyPatch,
    endpoint_name: str,
    endpoint_id: str,
) -> None:
    workspace = _stale_journal_workspace(
        old_endpoint_name=endpoint_name,
        old_endpoint_id=endpoint_id,
    )
    monkeypatch.setattr(cutover, "_supervisor_agents", lambda: [_agents()[0]])
    _install_signed_blue_pin_env(monkeypatch)

    with pytest.raises(RuntimeError, match="name or immutable ID was reused"):
        _clear_journal(workspace)

    assert journal_path(RUNTIME_ID) in workspace.workspace.data


def test_fresh_process_refuses_to_clear_journal_that_still_protects_signed_blue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _stale_journal_workspace()
    gateway, supervisor = _signed_blue_pins()
    payload = sign_cutover_journal(
        {
            "version": 3,
            "canonical_name": "Mortgage Growth Agent",
            "old_id": supervisor["supervisor_id"],
            "old_endpoint": supervisor["endpoint"],
            "old_endpoint_id": supervisor["endpoint_id"],
            "old_creator": supervisor["creator"],
            "old_create_time": "new-time",
        }
    )
    workspace.workspace.data[journal_path(RUNTIME_ID)] = json.dumps(payload).encode()
    monkeypatch.setattr(cutover, "_supervisor_agents", lambda: [_agents()[0]])
    _install_signed_blue_pin_env(monkeypatch)

    with pytest.raises(RuntimeError, match="still protects the signed-blue runtime"):
        _clear_journal(workspace)

    assert journal_path(RUNTIME_ID) in workspace.workspace.data


@pytest.mark.parametrize(
    "reused_agent",
    [
        {
            "supervisor_agent_id": OLD_ID,
            "display_name": "Reused Supervisor",
            "endpoint_name": "renamed-old-endpoint",
            "creator": RUNTIME_ID,
            "create_time": "reused-time",
        },
        {
            "supervisor_agent_id": "replacement-old-id",
            "display_name": "Reused Supervisor",
            "endpoint_name": OLD_ENDPOINT,
            "creator": RUNTIME_ID,
            "create_time": "reused-time",
        },
    ],
)
def test_fresh_process_rejects_old_supervisor_id_or_name_reuse_before_clear(
    monkeypatch: pytest.MonkeyPatch,
    reused_agent: dict[str, str],
) -> None:
    workspace = _stale_journal_workspace(
        supervisors=(_agents()[0], reused_agent),
    )
    _install_signed_blue_pin_env(monkeypatch)

    with pytest.raises(RuntimeError, match="immutable ID or endpoint name was reused"):
        _clear_journal(workspace)

    assert journal_path(RUNTIME_ID) in workspace.workspace.data


def test_stale_journal_clear_rejects_non_runtime_command_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _stale_journal_workspace()
    workspace.current_user.me = lambda: SimpleNamespace(
        user_name="deployer-client",
        display_name="workspace-admin",
    )
    monkeypatch.setattr(cutover, "_supervisor_agents", lambda: [_agents()[0]])
    _install_signed_blue_pin_env(monkeypatch)

    with pytest.raises(RuntimeError, match="not the configured agent-runtime"):
        _clear_journal(workspace)

    assert journal_path(RUNTIME_ID) in workspace.workspace.data


def test_cutover_journal_exports_immutable_endpoint_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = _journal_workspace()
    monkeypatch.setattr(cutover, "_supervisor_agents", _agents)

    cutover.pin_journal(
        workspace,
        assert_single_writer=_assert_lease,
        runtime_application_id=RUNTIME_ID,
        canonical_name="Mortgage Growth Agent",
        old_id=OLD_ID,
        old_endpoint=OLD_ENDPOINT,
        old_creator="skyler@entrada.ai",
        old_create_time="old-time",
    )
    out_env = tmp_path / "cutover.env"
    cutover.export_journal(
        workspace,
        runtime_application_id=RUNTIME_ID,
        out_env=out_env,
    )

    exported = out_env.read_text(encoding="utf-8")
    assert f"MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT_ID={OLD_ENDPOINT_ID}\n" in exported
    assert f"MIP_REPLACED_AGENT_SUPERVISOR_ID={OLD_ID}\n" in exported
    values = dict(line.split("=", 1) for line in exported.splitlines())
    assert shlex.split(values["MIP_REPLACED_AGENT_SUPERVISOR_PIN_JSON"]) == [
        (
            '{"creator":"skyler@entrada.ai","endpoint":"mas-old-endpoint",'
            '"endpoint_id":"old-endpoint-id","supervisor_id":"old-supervisor"}'
        )
    ]


def test_clear_journal_rejects_silent_delete_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _journal_workspace()
    path = _pin_test_journal(workspace, monkeypatch)
    workspace.workspace.delete = lambda _path: None

    with pytest.raises(RuntimeError, match="remained after exact deletion"):
        _clear_journal(workspace)

    assert path in workspace.workspace.data


def test_clear_journal_preserves_record_while_normal_cutover_group_is_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _journal_workspace()
    path = _pin_test_journal(workspace, monkeypatch)
    group = _managed_group(
        endpoint_id=OLD_ENDPOINT_ID,
        application_id="app-client",
        principal_id="app-scim-id",
    )
    workspace.groups = SimpleNamespace(
        list=lambda **_kwargs: [group],
        get=lambda _group_id: group,
    )

    with pytest.raises(RuntimeError, match="Supervisor App query group is not retired"):
        _clear_journal(workspace)

    assert path in workspace.workspace.data


def test_clear_journal_accepts_delete_that_commits_then_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _journal_workspace()
    path = _pin_test_journal(workspace, monkeypatch)
    real_delete = workspace.workspace.delete
    calls = 0

    def commit_then_timeout(actual_path: str) -> None:
        nonlocal calls
        calls += 1
        real_delete(actual_path)
        raise TimeoutError("injected timeout after journal delete commit")

    workspace.workspace.delete = commit_then_timeout

    _clear_journal(workspace)

    assert calls == 1
    assert path not in workspace.workspace.data


def test_clear_journal_refuses_retry_when_exact_record_remains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _journal_workspace()
    path = _pin_test_journal(workspace, monkeypatch)
    calls = 0

    def timeout_before_commit(_path: str) -> None:
        nonlocal calls
        calls += 1
        raise TimeoutError("injected timeout before journal delete commit")

    workspace.workspace.delete = timeout_before_commit

    with pytest.raises(RuntimeError, match="remained after ambiguous deletion; refusing retry"):
        _clear_journal(workspace)

    assert calls == 1
    assert path in workspace.workspace.data


def test_clear_journal_rejects_change_immediately_before_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _journal_workspace()
    path = _pin_test_journal(workspace, monkeypatch)
    real_download = workspace.workspace.download
    downloads = 0
    deletes = 0

    def change_on_second_read(actual_path: str) -> io.BytesIO:
        nonlocal downloads
        downloads += 1
        if downloads == 2:
            _replace_signed_journal(workspace, path)
        return real_download(actual_path)

    def record_delete(_path: str) -> None:
        nonlocal deletes
        deletes += 1

    workspace.workspace.download = change_on_second_read
    workspace.workspace.delete = record_delete

    with pytest.raises(RuntimeError, match="changed before clearance proof"):
        _clear_journal(workspace)

    assert deletes == 0
    assert json.loads(workspace.workspace.data[path])["old_endpoint_id"] == (
        "replacement-endpoint-id"
    )


def test_clear_journal_never_retries_changed_record_after_ambiguous_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _journal_workspace()
    path = _pin_test_journal(workspace, monkeypatch)
    calls = 0

    def replace_then_timeout(_path: str) -> None:
        nonlocal calls
        calls += 1
        _replace_signed_journal(workspace, path)
        raise TimeoutError("injected timeout with changed journal")

    workspace.workspace.delete = replace_then_timeout

    with pytest.raises(RuntimeError, match="changed during ambiguous deletion; refusing retry"):
        _clear_journal(workspace)

    assert calls == 1
    assert json.loads(workspace.workspace.data[path])["old_endpoint_id"] == (
        "replacement-endpoint-id"
    )


def test_cutover_journal_rejects_runtime_home_tampering(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = _journal_workspace()
    monkeypatch.setattr(cutover, "_supervisor_agents", _agents)
    cutover.pin_journal(
        workspace,
        assert_single_writer=_assert_lease,
        runtime_application_id=RUNTIME_ID,
        canonical_name="Mortgage Growth Agent",
        old_id=OLD_ID,
        old_endpoint=OLD_ENDPOINT,
        old_creator="skyler@entrada.ai",
        old_create_time="old-time",
    )
    path = journal_path(RUNTIME_ID)
    payload = json.loads(workspace.workspace.data[path])
    payload["old_endpoint"] = "attacker-controlled-endpoint"
    workspace.workspace.data[path] = json.dumps(payload).encode()

    with pytest.raises(RuntimeError, match="journal signature is invalid"):
        cutover.export_journal(
            workspace,
            runtime_application_id=RUNTIME_ID,
            out_env=tmp_path / "cutover.env",
        )


def test_previous_key_journal_is_re_signed_under_current_deploy_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = _journal_workspace()
    monkeypatch.setattr(cutover, "_supervisor_agents", _agents)
    previous_verify = derive_gateway_proof_verify_key(PREVIOUS_SIGNING_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", PREVIOUS_SIGNING_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", previous_verify)
    cutover.pin_journal(
        workspace,
        assert_single_writer=_assert_lease,
        runtime_application_id=RUNTIME_ID,
        canonical_name="Mortgage Growth Agent",
        old_id=OLD_ID,
        old_endpoint=OLD_ENDPOINT,
        old_creator="skyler@entrada.ai",
        old_create_time="old-time",
    )
    assert workspace.workspace.upload_count == 1

    current_verify = derive_gateway_proof_verify_key(SIGNING_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", current_verify)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", previous_verify)
    cutover.refresh_cutover_journal_attestation(
        workspace,
        runtime_application_id=RUNTIME_ID,
        assert_single_writer=_assert_lease,
    )

    assert workspace.workspace.upload_count == 2
    refreshed = json.loads(workspace.workspace.data[journal_path(RUNTIME_ID)])
    assert refreshed["attestation_verify_key"] == current_verify
    monkeypatch.delenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY")
    cutover.export_journal(
        workspace,
        runtime_application_id=RUNTIME_ID,
        out_env=tmp_path / "rotated.env",
    )


def test_current_key_journal_refresh_does_not_rewrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _journal_workspace()
    monkeypatch.setattr(cutover, "_supervisor_agents", _agents)
    cutover.pin_journal(
        workspace,
        assert_single_writer=_assert_lease,
        runtime_application_id=RUNTIME_ID,
        canonical_name="Mortgage Growth Agent",
        old_id=OLD_ID,
        old_endpoint=OLD_ENDPOINT,
        old_creator="skyler@entrada.ai",
        old_create_time="old-time",
    )

    cutover.refresh_cutover_journal_attestation(
        workspace,
        runtime_application_id=RUNTIME_ID,
        assert_single_writer=_assert_lease,
    )

    assert workspace.workspace.upload_count == 1


def test_missing_journal_refresh_is_a_noop() -> None:
    workspace = _journal_workspace()

    cutover.refresh_cutover_journal_attestation(
        workspace,
        runtime_application_id=RUNTIME_ID,
        assert_single_writer=_assert_lease,
    )

    assert workspace.workspace.upload_count == 0


def test_existing_journal_refuses_same_name_endpoint_with_different_immutable_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _journal_workspace()
    monkeypatch.setattr(cutover, "_supervisor_agents", _agents)
    cutover.pin_journal(
        workspace,
        assert_single_writer=_assert_lease,
        runtime_application_id=RUNTIME_ID,
        canonical_name="Mortgage Growth Agent",
        old_id=OLD_ID,
        old_endpoint=OLD_ENDPOINT,
        old_creator="skyler@entrada.ai",
        old_create_time="old-time",
    )
    workspace.serving_endpoints = _journal_workspace(
        endpoint_id="replacement-endpoint-id"
    ).serving_endpoints

    with pytest.raises(RuntimeError, match="different immutable cutover tuple"):
        cutover.pin_journal(
            workspace,
            assert_single_writer=_assert_lease,
            runtime_application_id=RUNTIME_ID,
            canonical_name="Mortgage Growth Agent",
            old_id=OLD_ID,
            old_endpoint=OLD_ENDPOINT,
            old_creator="skyler@entrada.ai",
            old_create_time="old-time",
        )


def test_second_run_recovers_journal_for_exact_orphan_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = _journal_workspace()
    monkeypatch.setattr(cutover, "_supervisor_agents", _agents)
    cutover.pin_journal(
        workspace,
        assert_single_writer=_assert_lease,
        runtime_application_id=RUNTIME_ID,
        canonical_name="Mortgage Growth Agent",
        old_id=OLD_ID,
        old_endpoint=OLD_ENDPOINT,
        old_creator="skyler@entrada.ai",
        old_create_time="old-time",
    )

    # Simulate the prior run deleting the agent, then failing before orphan
    # endpoint cleanup and journal clear.
    monkeypatch.setattr(cutover, "_supervisor_agents", lambda: [_agents()[0]])
    out_env = tmp_path / "recovered.env"
    cutover.export_journal(
        workspace,
        runtime_application_id=RUNTIME_ID,
        out_env=out_env,
    )
    exported = dict(line.split("=", 1) for line in out_env.read_text(encoding="utf-8").splitlines())
    monkeypatch.setattr(cutover, "_assert_green_path", lambda *_a, **_kw: None)

    cutover.retire(
        workspace,
        app_name="mip-app",
        old_id=exported["MIP_REPLACED_AGENT_SUPERVISOR_ID"],
        old_endpoint=exported["MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT"],
        old_endpoint_id=exported["MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT_ID"],
        old_creator=exported["MIP_REPLACED_AGENT_SUPERVISOR_CREATOR"],
        old_create_time=exported["MIP_REPLACED_AGENT_SUPERVISOR_CREATE_TIME"],
        timeout_s=1,
        **_green_kwargs(),
    )

    pinned_path = journal_path(RUNTIME_ID)
    assert pinned_path in workspace.workspace.data
    _clear_journal(workspace)
    assert pinned_path not in workspace.workspace.data


def test_gateway_only_cutover_journal_recovers_and_deletes_exact_old_runtime_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    files = _WorkspaceFiles()
    deleted = False
    revoked: list[tuple[str, str]] = []

    class _Endpoints:
        def get(self, name: str) -> object:
            if name == OLD_GATEWAY and not deleted:
                return SimpleNamespace(id=OLD_GATEWAY_ID, creator=RUNTIME_ID)
            raise ResourceDoesNotExist("missing")

        def delete(self, name: str) -> None:
            nonlocal deleted
            assert name == OLD_GATEWAY
            deleted = True

    workspace = SimpleNamespace(
        workspace=files,
        serving_endpoints=_Endpoints(),
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(
                user_name=RUNTIME_ID,
                display_name="mip-agent-runtime-ci-sp",
            )
        ),
        apps=SimpleNamespace(
            get=lambda _name: {
                "service_principal_client_id": "app-client",
                "service_principal_id": "app-scim-id",
            }
        ),
    )
    cutover.pin_journal(
        workspace,
        assert_single_writer=_assert_lease,
        runtime_application_id=RUNTIME_ID,
        canonical_name="Mortgage Growth Agent",
        old_gateway_endpoint=OLD_GATEWAY,
    )
    out_env = tmp_path / "gateway-cutover.env"
    cutover.export_journal(
        workspace,
        runtime_application_id=RUNTIME_ID,
        out_env=out_env,
    )
    exported = dict(line.split("=", 1) for line in out_env.read_text(encoding="utf-8").splitlines())
    assert exported == {
        "MIP_REPLACED_AGENT_GATEWAY_ENDPOINT": OLD_GATEWAY,
        "MIP_REPLACED_AGENT_GATEWAY_ENDPOINT_ID": OLD_GATEWAY_ID,
        "MIP_REPLACED_AGENT_GATEWAY_CREATOR": RUNTIME_ID,
        "MIP_REPLACED_AGENT_GATEWAY_DELETE_ALLOWED": "1",
        "MIP_REPLACED_AGENT_GATEWAY_PIN_JSON": shlex.quote(
            '{"creator":"runtime-client","endpoint_id":"old-gateway-id",'
            '"name":"mip-growth-agent-gateway-old123456789"}'
        ),
    }
    monkeypatch.setattr(cutover, "_assert_green_path", lambda *_a, **_kw: None)

    def revoke(*_args: object, **kwargs: object) -> str:
        kwargs["assert_before_mutation"]()
        revoked.append((kwargs["endpoint_name"], kwargs["app_scim_id"]))
        return "managed"

    monkeypatch.setattr(
        cutover,
        "revoke_managed_app_access",
        revoke,
    )

    cutover.retire(
        workspace,
        app_name="mip-app",
        old_id=None,
        old_endpoint=None,
        old_endpoint_id=None,
        old_creator=None,
        old_create_time=None,
        old_gateway_endpoint=exported["MIP_REPLACED_AGENT_GATEWAY_ENDPOINT"],
        old_gateway_endpoint_id=exported["MIP_REPLACED_AGENT_GATEWAY_ENDPOINT_ID"],
        old_gateway_creator=exported["MIP_REPLACED_AGENT_GATEWAY_CREATOR"],
        old_gateway_delete_allowed=(exported["MIP_REPLACED_AGENT_GATEWAY_DELETE_ALLOWED"] == "1"),
        timeout_s=1,
        **_green_kwargs(),
    )

    assert deleted is True
    assert revoked == [(OLD_GATEWAY, "app-scim-id")]
    assert journal_path(RUNTIME_ID) in files.data


def test_legacy_old_gateway_skips_revoke_and_deletes_after_exact_reproof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted = False
    events: list[str] = []

    class _Endpoints:
        def get(self, name: str) -> object:
            assert name == OLD_GATEWAY
            if deleted:
                raise ResourceDoesNotExist("deleted")
            return SimpleNamespace(id=OLD_GATEWAY_ID, creator=RUNTIME_ID)

        def delete(self, name: str) -> None:
            nonlocal deleted
            assert name == OLD_GATEWAY
            events.append("delete")
            deleted = True

    monkeypatch.setattr(
        cutover,
        "revoke_managed_app_access",
        lambda *_a, **_kw: "mixed",
    )
    monkeypatch.setattr(
        retired_groups,
        "inspect_gateway_query_access_mode",
        lambda *_a, **_kw: "direct",
    )
    monkeypatch.setattr(
        cutover,
        "retire_endpoint_query_groups",
        lambda workspace, **kwargs: (
            workspace.serving_endpoints.get(kwargs["endpoint_name"]),
            pytest.fail("cleanup ran before endpoint deletion"),
        )
        if not deleted
        else (
            events.append("cleanup"),
            kwargs["principals"]
            == (
                ("app-client", "app-scim-id"),
                ("verifier-client", "verifier-scim-id"),
            )
            or pytest.fail("wrong Gateway cleanup identities"),
        ),
    )
    cutover._delete_pinned_gateway(
        SimpleNamespace(serving_endpoints=_Endpoints()),
        endpoint=OLD_GATEWAY,
        endpoint_id=OLD_GATEWAY_ID,
        creator=RUNTIME_ID,
        delete_allowed=True,
        green_endpoint=GATEWAY,
        runtime_application_id=RUNTIME_ID,
        app_principal="app-client",
        app_principal_id="app-scim-id",
        verifier_application_id="verifier-client",
        verifier_scim_id="verifier-scim-id",
        timeout_s=1,
        assert_single_writer=lambda: events.append("lease"),
    )

    assert events == ["lease", "delete", "cleanup"]


def test_legacy_old_gateway_identity_drift_blocks_delete_without_revoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads = 0
    mutations: list[str] = []

    class _Endpoints:
        def get(self, _name: str) -> object:
            nonlocal reads
            reads += 1
            return SimpleNamespace(
                id=OLD_GATEWAY_ID if reads == 1 else "replacement-id",
                creator=RUNTIME_ID,
            )

        def delete(self, _name: str) -> None:
            mutations.append("delete")

    monkeypatch.setattr(
        cutover,
        "revoke_managed_app_access",
        lambda *_a, **_kw: "legacy",
    )
    with pytest.raises(RuntimeError, match="changed before its pinned deletion"):
        cutover._delete_pinned_gateway(
            SimpleNamespace(serving_endpoints=_Endpoints()),
            endpoint=OLD_GATEWAY,
            endpoint_id=OLD_GATEWAY_ID,
            creator=RUNTIME_ID,
            delete_allowed=True,
            green_endpoint=GATEWAY,
            runtime_application_id=RUNTIME_ID,
            app_principal="app-client",
            app_principal_id="app-scim-id",
            timeout_s=1,
            assert_single_writer=lambda: None,
        )

    assert mutations == []


def test_already_absent_pinned_gateway_retry_is_a_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mutations: list[str] = []
    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(
            get=lambda _name: (_ for _ in ()).throw(ResourceDoesNotExist("deleted")),
            delete=lambda _name: mutations.append("delete"),
        )
    )
    monkeypatch.setattr(
        cutover,
        "revoke_managed_app_access",
        lambda *_a, **_kw: mutations.append("inspect") or "legacy",
    )
    cutover._delete_pinned_gateway(
        workspace,
        endpoint=OLD_GATEWAY,
        endpoint_id=OLD_GATEWAY_ID,
        creator=RUNTIME_ID,
        delete_allowed=True,
        green_endpoint=GATEWAY,
        runtime_application_id=RUNTIME_ID,
        app_principal="app-client",
        app_principal_id="app-scim-id",
        timeout_s=1,
        assert_single_writer=lambda: mutations.append("lease"),
    )

    assert mutations == []


def test_retired_endpoint_group_cleanup_is_exact_and_idempotent() -> None:
    applications = {
        "app-client": "app-scim-id",
        "verifier-client": "verifier-scim-id",
    }
    by_id: dict[str, object] = {}
    for application_id, scim_id in applications.items():
        group_id = f"group-{application_id}"
        by_id[group_id] = SimpleNamespace(
            id=group_id,
            display_name=managed_query_group_name(
                endpoint_id=OLD_GATEWAY_ID,
                application_id=application_id,
            ),
            external_id=managed_query_group_external_id(
                endpoint_id=OLD_GATEWAY_ID,
                application_id=application_id,
            ),
            members=([SimpleNamespace(value=scim_id)] if application_id == "app-client" else []),
            meta=SimpleNamespace(resource_type="WorkspaceGroup"),
        )
    deletes: list[str] = []
    events: list[str] = []

    class _Groups:
        def list(self, *, filter: str) -> list[object]:
            expected_name = filter.removeprefix("displayName eq '").removesuffix("'")
            return [group for group in by_id.values() if group.display_name == expected_name]

        def get(self, group_id: str) -> object:
            events.append(f"get:{group_id}")
            if group_id not in by_id:
                raise ResourceDoesNotExist("deleted")
            return by_id[group_id]

        def delete(self, group_id: str) -> None:
            assert events[-1] == "lease"
            events.append(f"delete:{group_id}")
            deletes.append(group_id)
            del by_id[group_id]

    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(
            get=lambda _name: (_ for _ in ()).throw(ResourceDoesNotExist("deleted"))
        ),
        groups=_Groups(),
    )
    kwargs = {
        "workspace": workspace,
        "endpoint_name": OLD_GATEWAY,
        "endpoint_id": OLD_GATEWAY_ID,
        "principals": tuple(applications.items()),
        "assert_single_writer": lambda: events.append("lease"),
        "sleep": lambda _seconds: None,
    }

    retired_groups.retire_endpoint_query_groups(**kwargs)
    first_pass_events = tuple(events)
    retired_groups.retire_endpoint_query_groups(**kwargs)

    assert deletes == ["group-app-client", "group-verifier-client"]
    for group_id in deletes:
        delete_index = first_pass_events.index(f"delete:{group_id}")
        assert first_pass_events[delete_index - 2 : delete_index + 1] == (
            f"get:{group_id}",
            "lease",
            f"delete:{group_id}",
        )
    assert not any(
        event.startswith(("get:", "delete:")) for event in events[len(first_pass_events) :]
    )
    assert by_id == {}


def test_retired_endpoint_group_cleanup_rejects_transient_endpoint_absence() -> None:
    application_id = "app-client"
    scim_id = "app-scim-id"
    group_id = f"group-{application_id}"
    by_id = {
        group_id: SimpleNamespace(
            id=group_id,
            display_name=managed_query_group_name(
                endpoint_id=OLD_GATEWAY_ID,
                application_id=application_id,
            ),
            external_id=managed_query_group_external_id(
                endpoint_id=OLD_GATEWAY_ID,
                application_id=application_id,
            ),
            members=[SimpleNamespace(value=scim_id)],
            meta=SimpleNamespace(resource_type="WorkspaceGroup"),
        )
    }
    deletes: list[str] = []

    class _Groups:
        def list(self, *, filter: str) -> list[object]:
            expected_name = filter.removeprefix("displayName eq '").removesuffix("'")
            return [
                group
                for group in by_id.values()
                if group.display_name == expected_name
            ]

        def get(self, target_group_id: str) -> object:
            return by_id[target_group_id]

        def delete(self, target_group_id: str) -> None:
            deletes.append(target_group_id)
            del by_id[target_group_id]

    endpoint_reads = 0

    def transient_endpoint_get(_name: str) -> object:
        nonlocal endpoint_reads
        endpoint_reads += 1
        if endpoint_reads == 1:
            raise ResourceDoesNotExist("transient false absence")
        return SimpleNamespace(id=OLD_GATEWAY_ID)

    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(get=transient_endpoint_get),
        groups=_Groups(),
    )

    with pytest.raises(RuntimeError, match="after it has reappeared"):
        retired_groups.retire_endpoint_query_groups(
            workspace,
            endpoint_name=OLD_GATEWAY,
            endpoint_id=OLD_GATEWAY_ID,
            principals=((application_id, scim_id),),
            assert_single_writer=lambda: None,
            sleep=lambda _seconds: None,
        )

    assert endpoint_reads == 2
    assert deletes == []
    assert group_id in by_id


def test_retired_endpoint_group_cleanup_rejects_unrelated_member() -> None:
    group = SimpleNamespace(
        id="group-app",
        display_name=managed_query_group_name(
            endpoint_id=OLD_GATEWAY_ID,
            application_id="app-client",
        ),
        external_id=managed_query_group_external_id(
            endpoint_id=OLD_GATEWAY_ID,
            application_id="app-client",
        ),
        members=[SimpleNamespace(value="unrelated-scim-id")],
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
    )
    deleted: list[str] = []
    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(
            get=lambda _name: (_ for _ in ()).throw(ResourceDoesNotExist("deleted"))
        ),
        groups=SimpleNamespace(
            list=lambda **_kw: [group],
            get=lambda _id: group,
            delete=lambda group_id: deleted.append(group_id),
        ),
    )

    with pytest.raises(RuntimeError, match="unrelated member"):
        retired_groups.retire_endpoint_query_groups(
            workspace,
            endpoint_name=OLD_GATEWAY,
            endpoint_id=OLD_GATEWAY_ID,
            principals=(("app-client", "app-scim-id"),),
            assert_single_writer=lambda: None,
        )

    assert deleted == []


def test_retired_endpoint_group_cleanup_refuses_live_endpoint() -> None:
    mutations: list[str] = []
    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(get=lambda _name: SimpleNamespace(id=OLD_GATEWAY_ID)),
        groups=SimpleNamespace(delete=lambda group_id: mutations.append(group_id)),
    )

    with pytest.raises(RuntimeError, match="before its endpoint is absent"):
        retired_groups.retire_endpoint_query_groups(
            workspace,
            endpoint_name=OLD_GATEWAY,
            endpoint_id=OLD_GATEWAY_ID,
            principals=(("app-client", "app-scim-id"),),
            assert_single_writer=lambda: mutations.append("lease"),
        )

    assert mutations == []


def test_legacy_old_supervisor_skips_revoke_before_pinned_deletion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _journal_workspace()
    _install_supervisor_journal(workspace)
    agent_deleted = False
    events: list[str] = []

    def agents() -> list[dict[str, str]]:
        return [_agents()[0]] if agent_deleted else _agents()

    def delete_agent(_args: list[str]) -> None:
        nonlocal agent_deleted
        events.append("delete-agent")
        agent_deleted = True

    real_endpoint_delete = workspace.serving_endpoints.delete

    def delete_endpoint(name: str) -> None:
        events.append("delete-endpoint")
        real_endpoint_delete(name)

    workspace.serving_endpoints.delete = delete_endpoint
    monkeypatch.setattr(cutover, "_supervisor_agents", agents)
    monkeypatch.setattr(cutover, "_assert_green_path", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        cutover,
        "revoke_managed_app_access",
        lambda *_a, **_kw: "legacy",
    )
    monkeypatch.setattr(
        cutover,
        "exact_service_principal_scim_id",
        lambda *_a, **_kw: "proxy-scim-id",
    )
    monkeypatch.setattr(
        retired_groups,
        "inspect_gateway_query_access_mode",
        lambda *_a, **_kw: "direct",
    )
    monkeypatch.setattr(
        cutover,
        "retire_endpoint_query_groups",
        lambda _workspace, **kwargs: (
            events.append("cleanup"),
            kwargs["principals"]
            == (
                ("app-client", "app-scim-id"),
                ("proxy-client", "proxy-scim-id"),
            )
            or pytest.fail("wrong Supervisor cleanup identities"),
        ),
    )
    monkeypatch.setattr(cutover, "_run_no_json", delete_agent)

    cutover.retire(
        workspace,
        app_name="mip-app",
        old_id=OLD_ID,
        old_endpoint=OLD_ENDPOINT,
        old_endpoint_id=OLD_ENDPOINT_ID,
        old_creator="skyler@entrada.ai",
        old_create_time="old-time",
        verifier_application_id="verifier-client",
        verifier_scim_id="verifier-scim-id",
        proxy_application_id="proxy-client",
        timeout_s=1,
        **_green_kwargs(),
    )

    assert events == ["delete-agent", "delete-endpoint", "cleanup"]


def test_prepare_lost_lease_blocks_app_acl_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    mutations: list[str] = []
    monkeypatch.setattr(cutover, "_assert_green_path", lambda *_a, **_kw: None)

    def converge(*_args: object, **kwargs: object) -> None:
        check = kwargs["assert_single_writer"]
        assert callable(check)
        check()
        mutations.append("grant")

    monkeypatch.setattr(cutover, "_converge_app_gateway_permissions", converge)
    green = _green_kwargs()
    green["assert_single_writer"] = lambda: (_ for _ in ()).throw(RuntimeError("lease lost"))

    with pytest.raises(RuntimeError, match="lease lost"):
        cutover.prepare(
            _workspace(),
            app_name="mip-app",
            verifier_application_id="verifier-client",
            verifier_scim_id="verifier-scim-id",
            **green,
        )

    assert mutations == []


def test_prepare_rejects_unauthorized_legacy_retirement_before_app_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        cutover,
        "_assert_green_path",
        lambda *_a, **_kw: events.append("green-proved"),
    )
    monkeypatch.setattr(
        cutover,
        "assert_pinned_access_retirement_authority",
        lambda *_a, **_kw: (
            events.append("retirement-preflight"),
            (_ for _ in ()).throw(RuntimeError("creator policy")),
        )[-1],
    )
    monkeypatch.setattr(
        cutover,
        "_converge_app_gateway_permissions",
        lambda *_a, **_kw: events.append("activate-green"),
    )

    with pytest.raises(RuntimeError, match="creator policy"):
        cutover.prepare(
            _workspace(),
            app_name="mip-app",
            verifier_application_id="verifier-client",
            verifier_scim_id="verifier-scim-id",
            **_green_kwargs(),
        )

    assert events == ["green-proved", "retirement-preflight"]


@pytest.mark.parametrize(("fail_on", "expected"), [(1, []), (2, ["revoke"])])
def test_old_gateway_lost_lease_blocks_each_mutation(
    monkeypatch: pytest.MonkeyPatch,
    fail_on: int,
    expected: list[str],
) -> None:
    mutations: list[str] = []
    checks = 0

    def check() -> None:
        nonlocal checks
        checks += 1
        if checks == fail_on:
            raise RuntimeError("lease lost")

    endpoints = SimpleNamespace(
        get=lambda _name: SimpleNamespace(id=OLD_GATEWAY_ID, creator=RUNTIME_ID),
        delete=lambda _name: mutations.append("delete"),
    )
    workspace = SimpleNamespace(serving_endpoints=endpoints)

    def revoke(*_args: object, **kwargs: object) -> str:
        kwargs["assert_before_mutation"]()
        mutations.append("revoke")
        return "managed"

    monkeypatch.setattr(
        cutover,
        "revoke_managed_app_access",
        revoke,
    )

    with pytest.raises(RuntimeError, match="lease lost"):
        cutover._delete_pinned_gateway(
            workspace,
            endpoint=OLD_GATEWAY,
            endpoint_id=OLD_GATEWAY_ID,
            creator=RUNTIME_ID,
            delete_allowed=True,
            green_endpoint=GATEWAY,
            runtime_application_id=RUNTIME_ID,
            app_principal="app-client",
            app_principal_id="app-scim-id",
            timeout_s=1,
            assert_single_writer=check,
        )

    assert mutations == expected


@pytest.mark.parametrize(("fail_on", "expected"), [(1, []), (2, ["revoke"])])
def test_old_supervisor_lost_lease_blocks_acl_and_agent_delete(
    monkeypatch: pytest.MonkeyPatch,
    fail_on: int,
    expected: list[str],
) -> None:
    mutations: list[str] = []
    checks = 0

    def check() -> None:
        nonlocal checks
        checks += 1
        if checks == fail_on:
            raise RuntimeError("lease lost")

    workspace = _journal_workspace()
    _install_supervisor_journal(workspace)
    monkeypatch.setattr(cutover, "_supervisor_agents", _agents)
    monkeypatch.setattr(cutover, "_assert_green_path", lambda *_a, **_kw: None)

    def revoke(*_args: object, **kwargs: object) -> str:
        kwargs["assert_before_mutation"]()
        mutations.append("revoke")
        return "managed"

    monkeypatch.setattr(
        cutover,
        "revoke_managed_app_access",
        revoke,
    )
    monkeypatch.setattr(cutover, "_run_no_json", lambda _args: mutations.append("delete-agent"))
    green = _green_kwargs()
    green["assert_single_writer"] = check

    with pytest.raises(RuntimeError, match="lease lost"):
        cutover.retire(
            workspace,
            app_name="mip-app",
            old_id=OLD_ID,
            old_endpoint=OLD_ENDPOINT,
            old_endpoint_id=OLD_ENDPOINT_ID,
            old_creator="skyler@entrada.ai",
            old_create_time="old-time",
            timeout_s=1,
            **green,
        )

    assert mutations == expected


def test_orphan_endpoint_lost_lease_blocks_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _journal_workspace()
    _install_supervisor_journal(workspace)
    mutations: list[str] = []
    checks = 0

    def check() -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise RuntimeError("lease lost")

    monkeypatch.setattr(cutover, "_supervisor_agents", lambda: [_agents()[0]])
    monkeypatch.setattr(cutover, "_assert_green_path", lambda *_a, **_kw: None)

    def revoke(*_args: object, **kwargs: object) -> str:
        kwargs["assert_before_mutation"]()
        mutations.append("revoke")
        return "managed"

    monkeypatch.setattr(
        cutover,
        "revoke_managed_app_access",
        revoke,
    )
    green = _green_kwargs()
    green["assert_single_writer"] = check

    with pytest.raises(RuntimeError, match="lease lost"):
        cutover.retire(
            workspace,
            app_name="mip-app",
            old_id=OLD_ID,
            old_endpoint=OLD_ENDPOINT,
            old_endpoint_id=OLD_ENDPOINT_ID,
            old_creator="skyler@entrada.ai",
            old_create_time="old-time",
            timeout_s=1,
            **green,
        )

    assert mutations == ["revoke"]
    assert cutover._endpoint_identity(workspace, OLD_ENDPOINT) == (
        OLD_ENDPOINT_ID,
        "skyler@entrada.ai",
    )


def test_finalize_lost_lease_blocks_rename(monkeypatch: pytest.MonkeyPatch) -> None:
    mutations: list[str] = []
    monkeypatch.setattr(cutover, "_supervisor_agents", lambda: [_agents()[0]])
    monkeypatch.setattr(cutover, "assert_current_runtime_identity", lambda *_a, **_kw: None)
    monkeypatch.setattr(cutover, "_run_no_json", lambda _args: mutations.append("rename"))

    with pytest.raises(RuntimeError, match="lease lost"):
        cutover.finalize(
            SimpleNamespace(),
            canonical_name="Mortgage Growth Agent",
            replacement_id=NEW_ID,
            replacement_endpoint=NEW_ENDPOINT,
            runtime_application_id=RUNTIME_ID,
            catalog="mip",
            genie_space_id="space-123",
            assert_single_writer=lambda: (_ for _ in ()).throw(RuntimeError("lease lost")),
        )

    assert mutations == []


def test_journal_persist_lost_lease_blocks_workspace_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _journal_workspace()
    monkeypatch.setattr(cutover, "_supervisor_agents", _agents)

    with pytest.raises(RuntimeError, match="lease lost"):
        cutover.pin_journal(
            workspace,
            runtime_application_id=RUNTIME_ID,
            canonical_name="Mortgage Growth Agent",
            old_id=OLD_ID,
            old_endpoint=OLD_ENDPOINT,
            old_creator="skyler@entrada.ai",
            old_create_time="old-time",
            assert_single_writer=lambda: (_ for _ in ()).throw(RuntimeError("lease lost")),
        )

    assert workspace.workspace.upload_count == 0


def test_journal_refresh_lost_lease_blocks_rewrite(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _journal_workspace()
    monkeypatch.setattr(cutover, "_supervisor_agents", _agents)
    cutover.pin_journal(
        workspace,
        runtime_application_id=RUNTIME_ID,
        canonical_name="Mortgage Growth Agent",
        old_id=OLD_ID,
        old_endpoint=OLD_ENDPOINT,
        old_creator="skyler@entrada.ai",
        old_create_time="old-time",
        assert_single_writer=_assert_lease,
    )
    old_verify = derive_gateway_proof_verify_key(SIGNING_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", PREVIOUS_SIGNING_KEY)
    monkeypatch.setenv(
        "MIP_AI_GATEWAY_PROOF_VERIFY_KEY",
        derive_gateway_proof_verify_key(PREVIOUS_SIGNING_KEY),
    )
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", old_verify)

    with pytest.raises(RuntimeError, match="lease lost"):
        cutover.refresh_cutover_journal_attestation(
            workspace,
            runtime_application_id=RUNTIME_ID,
            assert_single_writer=lambda: (_ for _ in ()).throw(RuntimeError("lease lost")),
        )

    assert workspace.workspace.upload_count == 1


def test_journal_clear_lost_lease_preserves_record(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _journal_workspace()
    path = _pin_test_journal(workspace, monkeypatch)

    with pytest.raises(RuntimeError, match="lease lost"):
        _clear_journal(
            workspace,
            assert_single_writer=lambda: (_ for _ in ()).throw(RuntimeError("lease lost")),
        )

    assert path in workspace.workspace.data

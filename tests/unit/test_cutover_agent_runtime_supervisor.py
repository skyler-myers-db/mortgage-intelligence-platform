from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from databricks.sdk.errors import ResourceDoesNotExist
from databricks.sdk.service.workspace import ImportFormat

from backend.agents.gateway_contract import (
    DEFAULT_GATEWAY_AGENT_EXPERIMENT,
    gateway_inference_table_family,
    gateway_model_family,
)
from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tools.databricks import cutover_agent_runtime_supervisor as cutover
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


@pytest.fixture(autouse=True)
def _attestation_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv(
        "MIP_AI_GATEWAY_PROOF_VERIFY_KEY",
        derive_gateway_proof_verify_key(SIGNING_KEY),
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
        apps=SimpleNamespace(get=lambda _name: {"service_principal_client_id": "app-client"}),
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
        "revoke_direct_permissions",
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
        "revoke_direct_permissions",
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
        "revoke_direct_permissions",
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
        "revoke_direct_permissions",
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
        "revoke_direct_permissions",
        lambda *_args, **_kwargs: events.append("revoke-old") or True,
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
        "revoke_direct_permissions",
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
            **_green_kwargs(),
        )

    assert events == []


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
        apps=SimpleNamespace(get=lambda _name: {"service_principal_client_id": "app-client"}),
    )
    _install_supervisor_journal(workspace)
    monkeypatch.setattr(cutover, "_supervisor_agents", lambda: [_agents()[0]])
    monkeypatch.setattr(cutover, "_assert_green_path", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cutover,
        "revoke_direct_permissions",
        lambda *_args, **kwargs: events.append((kwargs["endpoint_name"], kwargs["missing_ok"]))
        or False,
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

    return SimpleNamespace(
        workspace=files,
        serving_endpoints=_Endpoints(),
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(
                user_name=RUNTIME_ID,
                display_name="mip-agent-runtime-ci-sp",
            )
        ),
        apps=SimpleNamespace(get=lambda _name: {"service_principal_client_id": "app-client"}),
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


def test_clear_journal_rejects_silent_delete_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _journal_workspace()
    path = _pin_test_journal(workspace, monkeypatch)
    workspace.workspace.delete = lambda _path: None

    with pytest.raises(RuntimeError, match="remained after exact deletion"):
        cutover.clear_journal(
            workspace, runtime_application_id=RUNTIME_ID, assert_single_writer=_assert_lease
        )

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

    cutover.clear_journal(
        workspace, runtime_application_id=RUNTIME_ID, assert_single_writer=_assert_lease
    )

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
        cutover.clear_journal(
            workspace, runtime_application_id=RUNTIME_ID, assert_single_writer=_assert_lease
        )

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

    with pytest.raises(RuntimeError, match="changed before exact deletion"):
        cutover.clear_journal(
            workspace, runtime_application_id=RUNTIME_ID, assert_single_writer=_assert_lease
        )

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
        cutover.clear_journal(
            workspace, runtime_application_id=RUNTIME_ID, assert_single_writer=_assert_lease
        )

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
    monkeypatch.setattr(cutover, "revoke_direct_permissions", lambda *_a, **_kw: True)

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
    cutover.clear_journal(
        workspace, runtime_application_id=RUNTIME_ID, assert_single_writer=_assert_lease
    )
    assert pinned_path not in workspace.workspace.data


def test_gateway_only_cutover_journal_recovers_and_deletes_exact_old_runtime_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    files = _WorkspaceFiles()
    deleted = False
    revoked: list[str] = []

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
        apps=SimpleNamespace(get=lambda _name: {"service_principal_client_id": "app-client"}),
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
    }
    monkeypatch.setattr(cutover, "_assert_green_path", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        cutover,
        "revoke_direct_permissions",
        lambda _workspace, **kwargs: revoked.append(kwargs["endpoint_name"]) or True,
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
    assert revoked == [OLD_GATEWAY]
    assert journal_path(RUNTIME_ID) in files.data


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
        cutover.prepare(_workspace(), app_name="mip-app", **green)

    assert mutations == []


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
    monkeypatch.setattr(
        cutover,
        "revoke_direct_permissions",
        lambda *_a, **_kw: mutations.append("revoke") or True,
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
    monkeypatch.setattr(
        cutover,
        "revoke_direct_permissions",
        lambda *_a, **_kw: mutations.append("revoke") or True,
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
    monkeypatch.setattr(
        cutover,
        "revoke_direct_permissions",
        lambda *_a, **_kw: mutations.append("revoke") or False,
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
        cutover.clear_journal(
            workspace,
            runtime_application_id=RUNTIME_ID,
            assert_single_writer=lambda: (_ for _ in ()).throw(RuntimeError("lease lost")),
        )

    assert path in workspace.workspace.data

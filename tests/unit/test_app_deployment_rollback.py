from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import ResourceDoesNotExist

from backend.agents.gateway_contract import (
    GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
    gateway_exact_resource_digest,
    gateway_runtime_binding_hash,
)
from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tools.databricks import app_deployment_rollback as rollback
from tools.databricks.app_rollback_resource_contract import reviewed_app_resource_contract
from tools.databricks.supervisor_agent_contract import (
    canonical_supervisor_contract_json,
    supervisor_contract_hash,
)

APP_NAME = "mip-app"
GIT_SHA = "a" * 40
LEASE_ID = "11111111-1111-4111-8111-111111111111"
SOURCE = "/Workspace/Users/deployer/.bundle/mip/dev/files"
ARTIFACT = "/Workspace/Users/app-id/src/deployment-blue"
SIGNING_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
TREATMENT_ARGS = {
    "treatment_warehouse_id": "warehouse-id",
    "treatment_catalog": "mip",
}
GENIE_SPACE_ID = "genie-space-id"
RESOURCE_CONTRACT = {
    "proof_version": GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
    "catalog": "mip",
    "genie_space_id": GENIE_SPACE_ID,
    "runtime_application_id": "runtime-client",
    "supervisor_display_name": "Mortgage Growth Agent",
    "supervisor_canonical_name": "Mortgage Growth Agent",
    "supervisor_contract_json": canonical_supervisor_contract_json(
        genie_space_id=GENIE_SPACE_ID,
        catalog="mip",
    ),
    "supervisor_contract_sha256": supervisor_contract_hash(
        genie_space_id=GENIE_SPACE_ID,
        catalog="mip",
    ),
    "supervisor_id": "supervisor-id",
    "supervisor_endpoint": "supervisor-endpoint",
    "gateway_endpoint": "green-gateway",
    "gateway_model_name": "mip.audit.proxy",
    "gateway_model_version": "7",
    "gateway_model_source": "models:/mip.audit.proxy/7",
    "gateway_experiment_name": "/Users/runtime-client/proxy-deadbeef",
    "gateway_experiment_id": "experiment-7",
    "gateway_inference_table": "mip.audit.inference",
}
RESOURCE_DIGEST = gateway_exact_resource_digest(RESOURCE_CONTRACT)
APP_RESOURCES = [
    {
        "name": "genie_space",
        "genie_space": {
            "name": "mortgage_lead_intelligence",
            "space_id": GENIE_SPACE_ID,
            "permission": "CAN_RUN",
        },
    },
    {
        "name": "sql_warehouse",
        "sql_warehouse": {"id": "warehouse-id", "permission": "CAN_USE"},
    },
]
CAPTURE_ARGS = {
    "genie_space_id": GENIE_SPACE_ID,
    "expected_deployment_lease_id": LEASE_ID,
    "expected_app_resources": APP_RESOURCES,
    **TREATMENT_ARGS,
}


@pytest.fixture(autouse=True)
def _attestation_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv(
        "MIP_AI_GATEWAY_PROOF_VERIFY_KEY",
        derive_gateway_proof_verify_key(SIGNING_KEY),
    )
    monkeypatch.setattr(rollback, "grant_direct_can_query", lambda *_a, **_kw: None)
    monkeypatch.setattr(rollback, "assert_deployment_lease_held", lambda *_a, **_kw: {})
    monkeypatch.setattr(rollback, "revoke_direct_permissions", lambda *_a, **_kw: True)
    monkeypatch.setattr(
        rollback,
        "converge_campaign_treatment_access",
        lambda **_kwargs: True,
    )
    monkeypatch.setattr(
        rollback,
        "resolve_exact_resource_proof",
        lambda *_args, **_kwargs: SimpleNamespace(
            contract=RESOURCE_CONTRACT,
            digest=RESOURCE_DIGEST,
        ),
    )


def _payload(*, git_sha: str = GIT_SHA) -> dict[str, object]:
    values = {
        "APP_ENV": "sandbox",
        "MIP_DEFAULT_CATALOG": "mip",
        "MIP_GIT_SHA": git_sha,
        "MIP_APP_DEPLOYMENT_LEASE_ID": LEASE_ID,
        "MIP_AGENT_SERVING_ENDPOINT": "green-gateway",
        "MIP_AGENT_SUPERVISOR_ID": "supervisor-id",
        "MIP_AGENT_SUPERVISOR_ENDPOINT": "supervisor-endpoint",
        "MIP_AGENT_SUPERVISOR_NAME": "Mortgage Growth Agent",
        "MIP_AGENT_RUNTIME_CLIENT_ID": "runtime-client",
        "MIP_AI_GATEWAY_ENDPOINT": "green-gateway",
        "MIP_AI_GATEWAY_AGENT_MODEL": "mip.audit.proxy",
        "MIP_AI_GATEWAY_AGENT_MODEL_VERSION": "7",
        "MIP_AI_GATEWAY_AGENT_MODEL_SOURCE": "models:/mip.audit.proxy/7",
        "MIP_AI_GATEWAY_AGENT_MODEL_FAMILY": "customer_mip.audit.proxy_family",
        "MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE": "customer-gateway-proxy",
        "MIP_AI_GATEWAY_TABLE_PREFIX": "customer_gateway_inference",
        "MIP_AI_GATEWAY_EXPERIMENT_NAME": "/Users/runtime-client/proxy-deadbeef",
        "MIP_AI_GATEWAY_EXPERIMENT_ID": "experiment-7",
        "MIP_AI_GATEWAY_INFERENCE_TABLE": "mip.audit.inference",
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256": RESOURCE_DIGEST,
    }
    return {
        "source_code_path": SOURCE,
        "mode": "SNAPSHOT",
        "env_vars": [
            {"name": "DATABRICKS_WAREHOUSE_ID", "value_from": "sql_warehouse"},
            *({"name": name, "value": value} for name, value in values.items()),
        ],
    }


def _binding() -> str:
    return gateway_runtime_binding_hash(
        endpoint="green-gateway",
        supervisor_id="supervisor-id",
        upstream_endpoint="supervisor-endpoint",
        runtime_application_id="runtime-client",
        model_name="mip.audit.proxy",
        model_version=7,
        inference_table="mip.audit.inference",
    )


def _deployment(
    deployment_id: str = "deployment-blue",
    *,
    source: str = SOURCE,
    artifact: str = ARTIFACT,
    env_names: list[str] | None = None,
) -> object:
    return SimpleNamespace(
        deployment_id=deployment_id,
        source_code_path=source,
        deployment_artifacts=SimpleNamespace(source_code_path=artifact),
        env_vars=[SimpleNamespace(name=name) for name in (env_names or [])],
        status=SimpleNamespace(state="SUCCEEDED"),
        create_time="2026-07-16T00:00:00Z",
        update_time="2026-07-16T00:01:00Z",
    )


class _Secrets:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_secret(self, scope: str, key: str) -> object:
        try:
            raw = self.values[(scope, key)]
        except KeyError as exc:
            raise ResourceDoesNotExist("missing") from exc
        return SimpleNamespace(value=base64.b64encode(raw.encode()).decode())

    def put_secret(self, *, scope: str, key: str, string_value: str) -> None:
        self.values[(scope, key)] = string_value


class _Apps:
    def __init__(self, deployments: list[object]) -> None:
        self.deployments = deployments
        self.deployed_payload: dict[str, object] | None = None
        self.started = 0
        self.active_deployment = deployments[-1]
        self.pending_deployment: object | None = None
        self.service_principal_client_id = "app-client"
        self.service_principal_id = "app-scim-id"
        self.resources = json.loads(json.dumps(APP_RESOURCES))

    def list_deployments(self, _app_name: str) -> list[object]:
        return self.deployments

    def get(self, _app_name: str) -> object:
        return SimpleNamespace(
            compute_status=SimpleNamespace(state="RUNNING"),
            url="https://mip.example",
            service_principal_client_id=self.service_principal_client_id,
            service_principal_id=self.service_principal_id,
            active_deployment=self.active_deployment,
            pending_deployment=self.pending_deployment,
            resources=self.resources,
        )

    def start_and_wait(self, _app_name: str, *, timeout: object) -> None:
        self.started += 1

    def deploy_and_wait(self, _app_name: str, deployment: object, *, timeout: object) -> object:
        self.deployed_payload = deployment.as_dict()
        restored = _deployment(
            "deployment-restored",
            source=ARTIFACT,
            artifact="/Workspace/Users/app-id/src/deployment-restored",
        )
        restored.update_time = "2026-07-17T00:00:00Z"
        self.deployments.append(restored)
        self.active_deployment = restored
        return restored


def _workspace(deployments: list[object] | None = None) -> object:
    return SimpleNamespace(
        apps=_Apps(deployments or [_deployment()]),
        secrets=_Secrets(),
    )


def _record(workspace: Any) -> dict[str, Any]:
    raw = workspace.secrets.values[("mip", rollback._record_key(APP_NAME))]
    return json.loads(raw)


def test_capture_persists_exact_immutable_last_good_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(rollback, "_health", lambda *_a, **_kw: (GIT_SHA, _binding(), LEASE_ID))

    rollback.capture_current(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        payload=_payload(),
        base_url="https://mip.example",
        bearer_token="token",
        expected_git_sha=GIT_SHA,
        expected_gateway_binding=_binding(),
        **CAPTURE_ARGS,
    )

    record = _record(workspace)
    assert record["deployment_id"] == "deployment-blue"
    assert record["payload"]["source_code_path"] == ARTIFACT
    assert record["gateway_binding_sha256"] == _binding()
    assert record["gateway_resources"] == {
        **RESOURCE_CONTRACT,
        "resource_digest": RESOURCE_DIGEST,
    }
    assert record["app_resources"] == APP_RESOURCES
    assert record["payload_sha256"] == rollback._payload_digest(record["payload"])


def test_capture_rejects_payload_that_does_not_bind_observed_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(rollback, "_health", lambda *_a, **_kw: (GIT_SHA, "b" * 64, LEASE_ID))

    with pytest.raises(RuntimeError, match="health contract"):
        rollback.capture_current(
            workspace,
            app_name=APP_NAME,
            scope="mip",
            payload=_payload(),
            base_url="https://mip.example",
            bearer_token="token",
            expected_git_sha=GIT_SHA,
            expected_gateway_binding=_binding(),
            **CAPTURE_ARGS,
        )


def test_capture_rejects_unreviewed_app_resource_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    workspace.apps.resources.append(
        {
            "name": "unreviewed_secret",
            "secret": {"scope": "other", "key": "credential", "permission": "READ"},
        }
    )
    monkeypatch.setattr(rollback, "_health", lambda *_a, **_kw: (GIT_SHA, _binding(), LEASE_ID))

    with pytest.raises(RuntimeError, match="reviewed bundle manifest"):
        rollback.capture_current(
            workspace,
            app_name=APP_NAME,
            scope="mip",
            payload=_payload(),
            base_url="https://mip.example",
            bearer_token="token",
            expected_git_sha=GIT_SHA,
            expected_gateway_binding=_binding(),
            **CAPTURE_ARGS,
        )


def test_reviewed_app_resource_contract_resolves_bundle_resource_references() -> None:
    summary = {
        "resources": {
            "sql_warehouses": {"mip_serverless_sql": {"id": "warehouse-id"}},
            "apps": {
                "mip_app": {
                    "resources": [
                        {
                            "name": "sql_warehouse",
                            "description": "reviewed warehouse",
                            "sql_warehouse": {
                                "id": "${resources.sql_warehouses.mip_serverless_sql.id}",
                                "permission": "CAN_USE",
                            },
                        },
                        APP_RESOURCES[0],
                    ]
                }
            },
        }
    }

    assert reviewed_app_resource_contract(summary) == APP_RESOURCES


def test_reviewed_app_resource_contract_rejects_unresolved_reference() -> None:
    summary = {
        "resources": {
            "apps": {
                "mip_app": {
                    "resources": [
                        {
                            "name": "sql_warehouse",
                            "sql_warehouse": {
                                "id": "${resources.sql_warehouses.missing.id}",
                                "permission": "CAN_USE",
                            },
                        }
                    ]
                }
            }
        }
    }

    with pytest.raises(RuntimeError, match="did not resolve"):
        reviewed_app_resource_contract(summary)


def test_payload_resource_proof_preserves_custom_resource_families(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def _resolve(*_args: object, **kwargs: object) -> object:
        observed.update(kwargs)
        return SimpleNamespace(contract=RESOURCE_CONTRACT, digest=RESOURCE_DIGEST)

    monkeypatch.setattr(rollback, "resolve_exact_resource_proof", _resolve)

    rollback._payload_resource_proof(
        _workspace(),
        payload=_payload(),
        genie_space_id=GENIE_SPACE_ID,
    )

    assert observed["gateway_model_family_name"] == "customer_mip.audit.proxy_family"
    assert observed["gateway_experiment_base_name"] == "customer-gateway-proxy"
    assert observed["gateway_table_prefix"] == "customer_gateway_inference"
    assert observed["require_resource_binding"] is True


def test_capture_rejects_candidate_served_resource_binding_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(rollback, "_health", lambda *_a, **_kw: (GIT_SHA, _binding(), LEASE_ID))

    def _resolve(*_args: object, **kwargs: object) -> object:
        assert kwargs["require_resource_binding"] is True
        raise RuntimeError("live candidate served resource binding drifted")

    monkeypatch.setattr(rollback, "resolve_exact_resource_proof", _resolve)

    with pytest.raises(RuntimeError, match="candidate served resource binding drifted"):
        rollback.capture_current(
            workspace,
            app_name=APP_NAME,
            scope="mip",
            payload=_payload(),
            base_url="https://mip.example",
            bearer_token="token",
            expected_git_sha=GIT_SHA,
            expected_gateway_binding=_binding(),
            **CAPTURE_ARGS,
        )

    assert ("mip", rollback._record_key(APP_NAME)) not in workspace.secrets.values


def test_capture_requiesces_treatment_when_durable_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    modes: list[str] = []
    monkeypatch.setattr(rollback, "_health", lambda *_a, **_kw: (GIT_SHA, _binding(), LEASE_ID))
    monkeypatch.setattr(
        rollback,
        "converge_campaign_treatment_access",
        lambda **kwargs: modes.append(str(kwargs["mode"])) or True,
    )
    monkeypatch.setattr(
        rollback,
        "_save_record",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("secret write failed")),
    )

    with pytest.raises(RuntimeError, match="secret write failed"):
        rollback.capture_current(
            workspace,
            app_name=APP_NAME,
            scope="mip",
            payload=_payload(),
            base_url="https://mip.example",
            bearer_token="token",
            expected_git_sha=GIT_SHA,
            expected_gateway_binding=_binding(),
            **CAPTURE_ARGS,
        )

    assert modes == ["quiesce"]


def test_capture_persists_proof_before_treatment_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    events: list[str] = []
    save_record = rollback._save_record
    monkeypatch.setattr(rollback, "_health", lambda *_a, **_kw: (GIT_SHA, _binding(), LEASE_ID))
    monkeypatch.setattr(
        rollback,
        "converge_campaign_treatment_access",
        lambda **kwargs: events.append(str(kwargs["mode"])) or True,
    )

    def _save(*args: object, **kwargs: object) -> None:
        events.append("save")
        save_record(*args, **kwargs)

    monkeypatch.setattr(rollback, "_save_record", _save)

    rollback.capture_current(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        payload=_payload(),
        base_url="https://mip.example",
        bearer_token="token",
        expected_git_sha=GIT_SHA,
        expected_gateway_binding=_binding(),
        **CAPTURE_ARGS,
    )

    assert events == ["quiesce", "save", "runtime"]


def test_ensure_restores_stale_active_deployment_from_signed_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(rollback, "_health", lambda *_a, **_kw: (GIT_SHA, _binding(), LEASE_ID))
    rollback.capture_current(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        payload=_payload(),
        base_url="https://mip.example",
        bearer_token="token",
        expected_git_sha=GIT_SHA,
        expected_gateway_binding=_binding(),
        **CAPTURE_ARGS,
    )
    newer = _deployment("manual-newer")
    newer.update_time = "2026-07-16T00:02:00Z"
    workspace.apps.deployments.append(newer)
    workspace.apps.active_deployment = newer

    rollback.ensure_current(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        base_url="https://mip.example",
        bearer_token="token",
        **TREATMENT_ARGS,
    )

    assert workspace.apps.deployed_payload["source_code_path"] == ARTIFACT
    assert _record(workspace)["deployment_id"] == "deployment-restored"


def test_ensure_rejects_pending_deployment_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(rollback, "_health", lambda *_a, **_kw: (GIT_SHA, _binding(), LEASE_ID))
    rollback.capture_current(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        payload=_payload(),
        base_url="https://mip.example",
        bearer_token="token",
        expected_git_sha=GIT_SHA,
        expected_gateway_binding=_binding(),
        **CAPTURE_ARGS,
    )
    workspace.apps.pending_deployment = SimpleNamespace(
        deployment_id="manual-pending",
        status=SimpleNamespace(state="IN_PROGRESS"),
    )

    with pytest.raises(RuntimeError, match="pending deployment"):
        rollback.ensure_current(
            workspace,
            app_name=APP_NAME,
            scope="mip",
            base_url="https://mip.example",
            bearer_token="token",
            **TREATMENT_ARGS,
        )


def test_ensure_rejects_live_resource_drift_before_start_or_acl_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(rollback, "_health", lambda *_a, **_kw: (GIT_SHA, _binding(), LEASE_ID))
    rollback.capture_current(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        payload=_payload(),
        base_url="https://mip.example",
        bearer_token="token",
        expected_git_sha=GIT_SHA,
        expected_gateway_binding=_binding(),
        **CAPTURE_ARGS,
    )
    grants: list[str] = []
    monkeypatch.setattr(
        rollback,
        "grant_direct_can_query",
        lambda *_args, **_kwargs: grants.append("grant"),
    )
    monkeypatch.setattr(
        rollback,
        "resolve_exact_resource_proof",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("live Gateway resource proof does not match")
        ),
    )

    with pytest.raises(RuntimeError, match="live Gateway resource proof"):
        rollback.ensure_current(
            workspace,
            app_name=APP_NAME,
            scope="mip",
            base_url="https://mip.example",
            bearer_token="token",
            **TREATMENT_ARGS,
        )

    assert workspace.apps.started == 0
    assert grants == []


def test_ensure_rejects_app_resource_target_drift_before_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(rollback, "_health", lambda *_a, **_kw: (GIT_SHA, _binding(), LEASE_ID))
    rollback.capture_current(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        payload=_payload(),
        base_url="https://mip.example",
        bearer_token="token",
        expected_git_sha=GIT_SHA,
        expected_gateway_binding=_binding(),
        **CAPTURE_ARGS,
    )
    workspace.apps.resources = [
        *APP_RESOURCES[:-1],
        {
            "name": "sql_warehouse",
            "sql_warehouse": {"id": "attacker-warehouse", "permission": "CAN_USE"},
        },
    ]

    with pytest.raises(RuntimeError, match="App resource bindings drifted"):
        rollback.ensure_current(
            workspace,
            app_name=APP_NAME,
            scope="mip",
            base_url="https://mip.example",
            bearer_token="token",
            **TREATMENT_ARGS,
        )

    assert workspace.apps.started == 0


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("service_principal_client_id", "other-client"),
        ("service_principal_id", "other-scim-id"),
    ],
)
def test_ensure_rejects_recreated_same_name_app_identity(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    replacement: str,
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(rollback, "_health", lambda *_a, **_kw: (GIT_SHA, _binding(), LEASE_ID))
    rollback.capture_current(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        payload=_payload(),
        base_url="https://mip.example",
        bearer_token="token",
        expected_git_sha=GIT_SHA,
        expected_gateway_binding=_binding(),
        **CAPTURE_ARGS,
    )
    setattr(workspace.apps, field, replacement)

    with pytest.raises(RuntimeError, match="identity drifted"):
        rollback.ensure_current(
            workspace,
            app_name=APP_NAME,
            scope="mip",
            base_url="https://mip.example",
            bearer_token="token",
            **TREATMENT_ARGS,
        )


def test_restore_redeploys_exact_payload_and_advances_server_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(rollback, "_health", lambda *_a, **_kw: (GIT_SHA, _binding(), LEASE_ID))
    rollback.capture_current(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        payload=_payload(),
        base_url="https://mip.example",
        bearer_token="token",
        expected_git_sha=GIT_SHA,
        expected_gateway_binding=_binding(),
        **CAPTURE_ARGS,
    )

    rollback.restore_last_good(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        base_url="https://mip.example",
        bearer_token="token",
        **TREATMENT_ARGS,
    )

    assert workspace.apps.deployed_payload["source_code_path"] == ARTIFACT
    assert _record(workspace)["deployment_id"] == "deployment-restored"


def test_restore_keeps_treatment_quiesced_until_exact_blue_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(rollback, "_health", lambda *_a, **_kw: (GIT_SHA, _binding(), LEASE_ID))
    rollback.capture_current(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        payload=_payload(),
        base_url="https://mip.example",
        bearer_token="token",
        expected_git_sha=GIT_SHA,
        expected_gateway_binding=_binding(),
        **CAPTURE_ARGS,
    )
    events: list[str] = []
    monkeypatch.setattr(
        rollback,
        "converge_campaign_treatment_access",
        lambda **kwargs: events.append(str(kwargs["mode"])) or True,
    )
    monkeypatch.setattr(
        rollback,
        "_verify_health",
        lambda *_args, **_kwargs: events.append("health"),
    )

    rollback.restore_last_good(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        base_url="https://mip.example",
        bearer_token="token",
        **TREATMENT_ARGS,
    )

    assert events == ["quiesce", "health", "runtime"]


def test_restore_rejects_served_resource_binding_drift_after_valid_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(rollback, "_health", lambda *_a, **_kw: (GIT_SHA, _binding(), LEASE_ID))
    rollback.capture_current(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        payload=_payload(),
        base_url="https://mip.example",
        bearer_token="token",
        expected_git_sha=GIT_SHA,
        expected_gateway_binding=_binding(),
        **CAPTURE_ARGS,
    )
    proof_calls = 0

    def _resolve(*_args: object, **kwargs: object) -> object:
        nonlocal proof_calls
        assert kwargs["require_resource_binding"] is True
        proof_calls += 1
        if proof_calls == 2:
            raise RuntimeError("live Gateway served resource binding drifted")
        return SimpleNamespace(contract=RESOURCE_CONTRACT, digest=RESOURCE_DIGEST)

    monkeypatch.setattr(rollback, "resolve_exact_resource_proof", _resolve)

    with pytest.raises(RuntimeError, match="served resource binding drifted"):
        rollback.restore_last_good(
            workspace,
            app_name=APP_NAME,
            scope="mip",
            base_url="https://mip.example",
            bearer_token="token",
            **TREATMENT_ARGS,
        )

    assert workspace.apps.deployed_payload is not None
    assert proof_calls == 2


def test_capture_rejects_ambiguous_server_secret_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(rollback, "_health", lambda *_a, **_kw: (GIT_SHA, _binding(), LEASE_ID))
    original_get = workspace.secrets.get_secret
    reads = 0

    def inconsistent_get(scope: str, key: str) -> object:
        nonlocal reads
        reads += 1
        if reads == 1:
            return SimpleNamespace(value=base64.b64encode(b"different").decode())
        return original_get(scope, key)

    workspace.secrets.get_secret = inconsistent_get

    with pytest.raises(RuntimeError, match="write did not converge"):
        rollback.capture_current(
            workspace,
            app_name=APP_NAME,
            scope="mip",
            payload=_payload(),
            base_url="https://mip.example",
            bearer_token="token",
            expected_git_sha=GIT_SHA,
            expected_gateway_binding=_binding(),
            **CAPTURE_ARGS,
        )


def test_restore_regrants_blue_gateway_and_revokes_unverified_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(rollback, "_health", lambda *_a, **_kw: (GIT_SHA, _binding(), LEASE_ID))
    rollback.capture_current(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        payload=_payload(),
        base_url="https://mip.example",
        bearer_token="token",
        expected_git_sha=GIT_SHA,
        expected_gateway_binding=_binding(),
        **CAPTURE_ARGS,
    )
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(
        rollback,
        "grant_direct_can_query",
        lambda *_a, **kw: events.append(("grant", str(kw["endpoint_name"]))),
    )
    monkeypatch.setattr(
        rollback,
        "revoke_direct_permissions",
        lambda *_a, **kw: events.append(("revoke", str(kw["endpoint_name"]))) or True,
    )

    rollback.restore_last_good(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        base_url="https://mip.example",
        bearer_token="token",
        **TREATMENT_ARGS,
        revoke_endpoints=("unverified-green",),
    )

    assert ("grant", "green-gateway") in events
    assert ("revoke", "unverified-green") in events


def test_ensure_reconciles_every_reserved_versioned_gateway_after_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    workspace.serving_endpoints = SimpleNamespace(
        list=lambda: iter(
            [
                SimpleNamespace(name="green-gateway"),
                SimpleNamespace(name="mip-growth-agent-gateway-deadbeef1234"),
                SimpleNamespace(name="unrelated-endpoint"),
            ]
        )
    )
    monkeypatch.setattr(rollback, "_health", lambda *_a, **_kw: (GIT_SHA, _binding(), LEASE_ID))
    rollback.capture_current(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        payload=_payload(),
        base_url="https://mip.example",
        bearer_token="token",
        expected_git_sha=GIT_SHA,
        expected_gateway_binding=_binding(),
        **CAPTURE_ARGS,
    )
    revoked: list[str] = []
    monkeypatch.setattr(
        rollback,
        "revoke_direct_permissions",
        lambda *_a, **kw: revoked.append(str(kw["endpoint_name"])) or True,
    )

    rollback.ensure_current(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        base_url="https://mip.example",
        bearer_token="token",
        **TREATMENT_ARGS,
    )

    assert "mip-growth-agent-gateway-deadbeef1234" in revoked
    assert "unrelated-endpoint" not in revoked


def test_tampered_server_record_fails_signature_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(rollback, "_health", lambda *_a, **_kw: (GIT_SHA, _binding(), LEASE_ID))
    rollback.capture_current(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        payload=_payload(),
        base_url="https://mip.example",
        bearer_token="token",
        expected_git_sha=GIT_SHA,
        expected_gateway_binding=_binding(),
        **CAPTURE_ARGS,
    )
    record = _record(workspace)
    record["payload"]["env_vars"][0]["value_from"] = "attacker-resource"
    workspace.secrets.values[("mip", rollback._record_key(APP_NAME))] = json.dumps(record)

    with pytest.raises(RuntimeError, match="signature"):
        rollback.ensure_current(
            workspace,
            app_name=APP_NAME,
            scope="mip",
            base_url="https://mip.example",
            bearer_token="token",
            **TREATMENT_ARGS,
        )


def test_ensure_rekeys_previous_signed_record_during_bounded_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(rollback, "_health", lambda *_a, **_kw: (GIT_SHA, _binding(), LEASE_ID))
    rollback.capture_current(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        payload=_payload(),
        base_url="https://mip.example",
        bearer_token="token",
        expected_git_sha=GIT_SHA,
        expected_gateway_binding=_binding(),
        **CAPTURE_ARGS,
    )
    old_verify = derive_gateway_proof_verify_key(SIGNING_KEY)
    new_signing = base64.urlsafe_b64encode(bytes(reversed(range(32)))).decode().rstrip("=")
    new_verify = derive_gateway_proof_verify_key(new_signing)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", new_signing)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", new_verify)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", old_verify)

    rollback.ensure_current(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        base_url="https://mip.example",
        bearer_token="token",
        **TREATMENT_ARGS,
    )

    assert _record(workspace)["attestation_verify_key"] == new_verify

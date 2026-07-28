from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import ResourceDoesNotExist

from backend.agents.gateway_contract import (
    GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
    canonical_gateway_runtime_resource_contract,
    gateway_exact_resource_digest,
    gateway_runtime_binding_hash,
)
from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tools.databricks import app_deployment_health as deployment_health
from tools.databricks import app_deployment_rollback as rollback
from tools.databricks import app_deployment_rollback_cli as rollback_cli
from tools.databricks import app_deployment_rollback_inputs as rollback_inputs
from tools.databricks import app_rollback_record_contract as rollback_contract
from tools.databricks.app_health_contract import ActiveAppDeploymentPin
from tools.databricks.app_rollback_gateway_binding import payload_gateway_binding
from tools.databricks.app_rollback_resource_contract import reviewed_app_resource_contract
from tools.databricks.gateway_legacy_rollback import (
    LEGACY_GATEWAY_RESOURCE_FIELDS,
    PRIOR_GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
    legacy_gateway_resource_digest,
    prior_v2_gateway_resource_digest,
    validated_legacy_gateway_resources,
)
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
    "deployment_lease_id": LEASE_ID,
    "deployment_source_git_sha": GIT_SHA,
}
GENIE_SPACE_ID = "genie-space-id"
PROXY_CLIENT_ID = "proxy-client"
PROXY_CREDENTIAL_ID = "proxy-credential"
PROXY_SECRET_REFERENCE = "{{secrets/mip-agent-proxy/oauth-client-secret-proxy-credential}}"
RESOURCE_CONTRACT = {
    "proof_version": GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
    "catalog": "mip",
    "genie_space_id": GENIE_SPACE_ID,
    "runtime_application_id": "runtime-client",
    "workspace_host": "https://workspace.cloud.databricks.com",
    "proxy_caller_application_id": PROXY_CLIENT_ID,
    "proxy_caller_credential_id": PROXY_CREDENTIAL_ID,
    "proxy_caller_secret_reference": PROXY_SECRET_REFERENCE,
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
    "supervisor_creator": "runtime-client",
    "supervisor_endpoint": "supervisor-endpoint",
    "supervisor_endpoint_id": "supervisor-endpoint-id",
    "supervisor_endpoint_creator": "runtime-client",
    "gateway_endpoint": "green-gateway",
    "gateway_endpoint_id": "green-gateway-id",
    "gateway_endpoint_creator": "runtime-client",
    "gateway_endpoint_description": "test-reviewed-description",
    "gateway_endpoint_task": "agent/v1/responses",
    "gateway_endpoint_route_optimized": "false",
    "gateway_endpoint_budget_policy": "none",
    "gateway_endpoint_email_notifications": "none",
    "gateway_endpoint_deprecated_rate_limits": "[]",
    "gateway_source_hash": "1" * 64,
    "gateway_resource_hash": "2" * 64,
    "gateway_model_family": "mip.audit.proxy",
    "gateway_model_name": "mip.audit.proxy",
    "gateway_model_version": "7",
    "gateway_model_source": "models:/mip.audit.proxy/7",
    "gateway_model_owner": "runtime-client",
    "gateway_experiment_base": "proxy",
    "gateway_experiment_acl_json": '{"test":"acl"}',
    "gateway_experiment_acl_sha256": "3" * 64,
    "gateway_inference_table_family": "mip.audit.mip_agent_gateway_growth_agent",
    "gateway_experiment_name": "/Users/runtime-client/proxy-deadbeef",
    "gateway_experiment_id": "experiment-7",
    "gateway_experiment_owner": "runtime-client",
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
    "treatment_warehouse_id": TREATMENT_ARGS["treatment_warehouse_id"],
    "treatment_catalog": TREATMENT_ARGS["treatment_catalog"],
}


@pytest.fixture(autouse=True)
def _attestation_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv(
        "MIP_AI_GATEWAY_PROOF_VERIFY_KEY",
        derive_gateway_proof_verify_key(SIGNING_KEY),
    )
    monkeypatch.setenv("MIP_LAKEBASE_INSTANCE", "mip-app-state")
    monkeypatch.setenv("LAKEBASE_INSTANCE_NAME", "mip-app-state")
    monkeypatch.setattr(
        rollback,
        "preserve_blue_and_revoke_managed_candidates",
        lambda *_a, **_kw: "managed",
    )
    monkeypatch.setattr(rollback, "assert_deployment_lease_held", lambda *_a, **_kw: {})
    monkeypatch.setattr(
        rollback,
        "held_assertion",
        lambda *_args, **_kwargs: lambda: None,
    )
    monkeypatch.setattr(
        rollback_contract,
        "assert_owned_app_rollback_scope",
        lambda *_args, **_kwargs: None,
    )
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
    monkeypatch.setattr(
        rollback,
        "authenticated_reviewed_function_owner",
        lambda *_args, **_kwargs: "reviewed-owner",
    )
    monkeypatch.setattr(
        rollback,
        "assert_reviewed_function_set",
        lambda *_args, **_kwargs: None,
    )


def _payload(*, git_sha: str = GIT_SHA) -> dict[str, object]:
    values = {
        "APP_ENV": "sandbox",
        "MIP_LAKEBASE_INSTANCE": "mip-app-state",
        "LAKEBASE_INSTANCE_NAME": "mip-app-state",
        "MIP_DEFAULT_CATALOG": "mip",
        "MIP_GIT_SHA": git_sha,
        "MIP_APP_DEPLOYMENT_LEASE_ID": LEASE_ID,
        "MIP_AGENT_SERVING_ENDPOINT": "green-gateway",
        "MIP_AGENT_SUPERVISOR_ID": "supervisor-id",
        "MIP_AGENT_SUPERVISOR_ENDPOINT": "supervisor-endpoint",
        "MIP_AGENT_SUPERVISOR_NAME": "Mortgage Growth Agent",
        "MIP_AGENT_RUNTIME_CLIENT_ID": "runtime-client",
        "MIP_REVIEWED_FUNCTION_OWNER": "reviewed-owner",
        "MIP_AGENT_PROXY_CLIENT_ID": PROXY_CLIENT_ID,
        "MIP_AGENT_PROXY_CREDENTIAL_ID": PROXY_CREDENTIAL_ID,
        "MIP_AGENT_PROXY_SECRET_REFERENCE": PROXY_SECRET_REFERENCE,
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
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON": (
            canonical_gateway_runtime_resource_contract(RESOURCE_CONTRACT)
        ),
    }
    return {
        "source_code_path": SOURCE,
        "mode": "SNAPSHOT",
        "env_vars": [
            {"name": "DATABRICKS_WAREHOUSE_ID", "value_from": "sql_warehouse"},
            *({"name": name, "value": value} for name, value in values.items()),
        ],
    }


def _immutable_payload(*, git_sha: str = GIT_SHA) -> dict[str, object]:
    payload = _payload(git_sha=git_sha)
    payload["source_code_path"] = ARTIFACT
    return payload


def _binding() -> str:
    return gateway_runtime_binding_hash(
        endpoint="green-gateway",
        supervisor_id="supervisor-id",
        upstream_endpoint="supervisor-endpoint",
        runtime_application_id="runtime-client",
        workspace_host="https://workspace.cloud.databricks.com",
        model_name="mip.audit.proxy",
        model_version=7,
        inference_table="mip.audit.inference",
        proxy_caller_application_id=PROXY_CLIENT_ID,
        proxy_caller_credential_id=PROXY_CREDENTIAL_ID,
        proxy_caller_secret_reference=PROXY_SECRET_REFERENCE,
    )


def _prior_v2_proxy_resources() -> dict[str, str]:
    contract = {key: value for key, value in RESOURCE_CONTRACT.items() if key != "workspace_host"}
    contract["proof_version"] = PRIOR_GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION
    return {
        **contract,
        "resource_digest": prior_v2_gateway_resource_digest(contract),
    }


def _prior_v2_payload() -> dict[str, object]:
    payload = json.loads(json.dumps(_immutable_payload()))
    resources = _prior_v2_proxy_resources()
    contract = {key: value for key, value in resources.items() if key != "resource_digest"}
    replacements = {
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256": resources["resource_digest"],
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON": json.dumps(
            contract,
            sort_keys=True,
            separators=(",", ":"),
        ),
    }
    for item in payload["env_vars"]:
        if item["name"] in replacements:
            item["value"] = replacements[item["name"]]
    return payload


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

    def delete_secret(self, scope: str, key: str) -> None:
        self.values.pop((scope, key), None)


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
        self.resource_updates = 0

    def list_deployments(self, _app_name: str) -> list[object]:
        return self.deployments

    def get_deployment(self, _app_name: str, deployment_id: str) -> object:
        return SimpleNamespace(
            deployment_id=deployment_id,
            env_vars=[
                SimpleNamespace(
                    name="MIP_APP_DEPLOYMENT_LEASE_ID",
                    value=None,
                    value_from=None,
                )
            ],
        )

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

    def update(self, _app_name: str, app: object) -> object:
        self.resource_updates += 1
        self.resources = app.as_dict()["resources"]
        return self.get(_app_name)

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


def test_health_uses_bounded_authenticated_readiness_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def wait(*_args: object, **kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "git_sha": GIT_SHA,
            "agent_gateway_binding_sha256": _binding(),
            "deployment_lease_id": LEASE_ID,
        }

    monkeypatch.setattr(deployment_health, "wait_for_authenticated_app_health", wait)

    assert rollback._health(
        _workspace(),
        app_name=APP_NAME,
        base_url="https://mip.example",
        bearer_token="token",
        expected_pin=ActiveAppDeploymentPin(
            deployment_id="deployment-blue",
            lease_id=LEASE_ID,
        ),
    ) == (GIT_SHA, _binding(), LEASE_ID)
    assert captured["timeout_s"] == deployment_health.APP_HEALTH_READY_TIMEOUT_S
    assert captured["interval_s"] == deployment_health.APP_HEALTH_READY_INTERVAL_S
    assert captured["bearer_token"] == "token"
    assert callable(captured["assert_pinned"])


def test_health_rejects_active_deployment_drift_during_readiness_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    expected_pin = ActiveAppDeploymentPin(
        deployment_id="deployment-blue",
        lease_id=LEASE_ID,
    )

    def wait(*_args: object, **kwargs: object) -> dict[str, object]:
        assert_pinned = kwargs["assert_pinned"]
        assert callable(assert_pinned)
        assert_pinned()
        workspace.apps.active_deployment = _deployment("deployment-other")
        assert_pinned()
        raise AssertionError("pin drift must fail before returning health")

    monkeypatch.setattr(deployment_health, "wait_for_authenticated_app_health", wait)

    with pytest.raises(RuntimeError, match="changed during proof"):
        rollback._health(
            workspace,
            app_name=APP_NAME,
            base_url="https://mip.example",
            bearer_token="token",
            expected_pin=expected_pin,
        )


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
    env = rollback._env_map(record["payload"])
    assert env["MIP_LAKEBASE_INSTANCE"] == "mip-app-state"
    assert env["LAKEBASE_INSTANCE_NAME"] == "mip-app-state"
    assert record["gateway_binding_sha256"] == _binding()
    assert record["gateway_resources"] == {
        **RESOURCE_CONTRACT,
        "resource_digest": RESOURCE_DIGEST,
    }
    assert record["app_resources"] == APP_RESOURCES
    assert record["payload_sha256"] == rollback._payload_digest(record["payload"])


def test_verified_signed_last_good_contract_is_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(
        rollback,
        "_health",
        lambda *_a, **_kw: (GIT_SHA, _binding(), LEASE_ID),
    )
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
    before = dict(workspace.secrets.values)
    resource_updates = workspace.apps.resource_updates
    starts = workspace.apps.started

    contract = rollback.verified_signed_last_good_contract(
        workspace,
        app_name=APP_NAME,
        scope="mip",
    )

    assert contract.record_version == 6
    assert contract.proxy_rollback_mode == "exact-proxy"
    assert contract.deployment_id == "deployment-blue"
    assert contract.deployment_lease_id == LEASE_ID
    assert contract.git_sha == GIT_SHA
    assert contract.gateway_binding_sha256 == _binding()
    assert contract.gateway_endpoint == "green-gateway"
    assert contract.gateway_endpoint_id == "green-gateway-id"
    assert contract.gateway_endpoint_creator == "runtime-client"
    assert contract.gateway_inference_table_family == "mip.audit.mip_agent_gateway_growth_agent"
    assert contract.supervisor_id == "supervisor-id"
    assert contract.supervisor_creator == "runtime-client"
    assert contract.supervisor_endpoint == "supervisor-endpoint"
    assert contract.supervisor_endpoint_id == "supervisor-endpoint-id"
    assert contract.runtime_application_id == "runtime-client"
    assert contract.genie_space_id == GENIE_SPACE_ID
    assert contract.proxy_application_id == PROXY_CLIENT_ID
    assert contract.active_proxy_credential_id == PROXY_CREDENTIAL_ID
    assert contract.pending_proxy_credential_retirement_ids == ()
    assert workspace.secrets.values == before
    assert workspace.apps.resource_updates == resource_updates
    assert workspace.apps.started == starts


@pytest.mark.parametrize("missing_field", ["gateway_endpoint_id", "gateway_endpoint_creator"])
def test_signed_last_good_rejects_missing_immutable_gateway_identity(
    monkeypatch: pytest.MonkeyPatch,
    missing_field: str,
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(
        rollback,
        "_health",
        lambda *_a, **_kw: (GIT_SHA, _binding(), LEASE_ID),
    )
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
    resources = dict(record["gateway_resources"])
    resources.pop("resource_digest")
    resources.pop(missing_field)
    record["gateway_resources"] = {
        **resources,
        "resource_digest": gateway_exact_resource_digest(resources),
    }

    with pytest.raises(RuntimeError, match="lacks immutable Gateway identity"):
        rollback_contract._validated_record(
            record,
            app_name=APP_NAME,
            expected_lakebase_instance="mip-app-state",
        )


def test_ensure_cli_exports_exact_signed_blue_proxy_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    out_env = tmp_path / "blue.env"
    monkeypatch.setenv("MIP_TEST_TOKEN", "token")
    monkeypatch.setattr(rollback_cli, "WorkspaceClient", lambda: object())
    monkeypatch.setattr(rollback, "ensure_current", lambda **_kwargs: "blue-gateway")
    monkeypatch.setattr(
        rollback,
        "verified_signed_last_good_contract",
        lambda *_args, **_kwargs: rollback.SignedLastGoodAppContract(
            record_version=6,
            proxy_rollback_mode="exact-proxy",
            deployment_id="deployment-blue",
            deployment_lease_id=LEASE_ID,
            git_sha=GIT_SHA,
            gateway_binding_sha256=_binding(),
            gateway_endpoint="blue-gateway",
            gateway_endpoint_id="blue-gateway-id",
            gateway_endpoint_creator="runtime-client",
            gateway_inference_table_family=("mip.audit.mip_agent_gateway_growth_agent"),
            supervisor_id="supervisor-id",
            supervisor_creator="runtime-client",
            supervisor_endpoint="supervisor-endpoint",
            supervisor_endpoint_id="supervisor-endpoint-id",
            runtime_application_id="runtime-client",
            genie_space_id=GENIE_SPACE_ID,
            proxy_application_id=PROXY_CLIENT_ID,
            active_proxy_credential_id=PROXY_CREDENTIAL_ID,
            pending_proxy_credential_retirement_ids=("retired-credential",),
        ),
    )

    assert (
        rollback_cli.main(
            [
                "ensure",
                "--app-name",
                APP_NAME,
                "--scope",
                "mip",
                "--base-url",
                "https://mip.example",
                "--token-env",
                "MIP_TEST_TOKEN",
                "--treatment-warehouse-id",
                "warehouse-id",
                "--deployment-lease-id",
                LEASE_ID,
                "--deployment-source-git-sha",
                GIT_SHA,
                "--out-env",
                str(out_env),
            ]
        )
        == 0
    )

    values = dict(line.split("=", 1) for line in out_env.read_text(encoding="utf-8").splitlines())
    assert values == {
        "MIP_APP_ROLLBACK_RECORD_VERSION": "6",
        "MIP_APP_ROLLBACK_PROXY_MODE": "exact-proxy",
        "MIP_APP_ROLLBACK_DEPLOYMENT_ID": "deployment-blue",
        "MIP_APP_ROLLBACK_GATEWAY_ENDPOINT": "blue-gateway",
        "MIP_APP_ROLLBACK_GATEWAY_ENDPOINT_ID": "blue-gateway-id",
        "MIP_APP_ROLLBACK_GATEWAY_CREATOR": "runtime-client",
        "MIP_APP_ROLLBACK_GATEWAY_PIN_JSON": (
            """'{"creator":"runtime-client","endpoint_id":"blue-gateway-id","name":"""
            """"blue-gateway"}'"""
        ),
        "MIP_APP_ROLLBACK_GATEWAY_INFERENCE_TABLE_PREFIX": (
            "mip.audit.mip_agent_gateway_growth_agent"
        ),
        "MIP_APP_ROLLBACK_SUPERVISOR_ID": "supervisor-id",
        "MIP_APP_ROLLBACK_SUPERVISOR_CREATOR": "runtime-client",
        "MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT": "supervisor-endpoint",
        "MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT_ID": "supervisor-endpoint-id",
        "MIP_APP_ROLLBACK_SUPERVISOR_PIN_JSON": (
            """'{"creator":"runtime-client","endpoint":"supervisor-endpoint","endpoint_id":"""
            """"supervisor-endpoint-id","supervisor_id":"supervisor-id"}'"""
        ),
        "MIP_APP_ROLLBACK_RUNTIME_APPLICATION_ID": "runtime-client",
        "MIP_APP_ROLLBACK_GENIE_SPACE_ID": GENIE_SPACE_ID,
        "MIP_APP_ROLLBACK_PROXY_APPLICATION_ID": PROXY_CLIENT_ID,
        "MIP_APP_ROLLBACK_PROXY_CREDENTIAL_IDS": "retired-credential",
    }


def test_inspect_cli_exports_explicit_legacy_proxyless_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    out_env = tmp_path / "legacy.env"
    monkeypatch.setenv("MIP_TEST_TOKEN", "token")
    monkeypatch.setattr(rollback_cli, "WorkspaceClient", lambda: object())
    monkeypatch.setattr(
        rollback,
        "verified_signed_last_good_contract",
        lambda *_args, **_kwargs: rollback.SignedLastGoodAppContract(
            record_version=5,
            proxy_rollback_mode="legacy-proxyless",
            deployment_id="legacy-deployment",
            deployment_lease_id=LEASE_ID,
            git_sha=GIT_SHA,
            gateway_binding_sha256=_binding(),
            gateway_endpoint="legacy-gateway",
            gateway_endpoint_id="legacy-gateway-id",
            gateway_endpoint_creator="runtime-client",
            gateway_inference_table_family="legacy-gateway_inference_table_family",
            supervisor_id="legacy-supervisor",
            supervisor_creator="runtime-client",
            supervisor_endpoint="legacy-endpoint",
            supervisor_endpoint_id="legacy-endpoint-id",
            runtime_application_id="runtime-client",
            genie_space_id=GENIE_SPACE_ID,
            proxy_application_id=None,
            active_proxy_credential_id=None,
            pending_proxy_credential_retirement_ids=(),
        ),
    )

    assert (
        rollback_cli.main(
            [
                "inspect",
                "--app-name",
                APP_NAME,
                "--scope",
                "mip",
                "--base-url",
                "https://mip.example",
                "--token-env",
                "MIP_TEST_TOKEN",
                "--out-env",
                str(out_env),
            ]
        )
        == 0
    )

    values = dict(line.split("=", 1) for line in out_env.read_text(encoding="utf-8").splitlines())
    assert values["MIP_APP_ROLLBACK_RECORD_VERSION"] == "5"
    assert values["MIP_APP_ROLLBACK_PROXY_MODE"] == "legacy-proxyless"
    assert values["MIP_APP_ROLLBACK_DEPLOYMENT_ID"] == "legacy-deployment"
    assert (
        values["MIP_APP_ROLLBACK_GATEWAY_INFERENCE_TABLE_PREFIX"]
        == "legacy-gateway_inference_table_family"
    )
    assert values["MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT_ID"] == "legacy-endpoint-id"
    assert values["MIP_APP_ROLLBACK_PROXY_APPLICATION_ID"] == "''"


def test_proxy_retirement_derives_only_from_prior_signed_identity() -> None:
    previous = {
        "version": rollback.RECORD_VERSION,
        "gateway_resources": {
            "proxy_caller_application_id": PROXY_CLIENT_ID,
            "proxy_caller_credential_id": "blue",
        },
        "pending_proxy_credential_retirement_ids": ("older",),
    }
    candidate = {
        "proxy_caller_application_id": PROXY_CLIENT_ID,
        "proxy_caller_credential_id": "green",
    }

    assert rollback._capture_proxy_retirement_ids(
        previous,
        candidate_gateway_resources=candidate,
    ) == ("blue", "older")

    with pytest.raises(RuntimeError, match="proxy identity changed"):
        rollback._capture_proxy_retirement_ids(
            previous,
            candidate_gateway_resources={
                **candidate,
                "proxy_caller_application_id": "different-proxy",
            },
        )


def test_signed_proxy_retirement_journal_survives_process_loss_and_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    active_binding = _binding()
    monkeypatch.setattr(
        rollback,
        "_health",
        lambda *_a, **_kw: (GIT_SHA, active_binding, LEASE_ID),
    )
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

    green_credential_id = "green-credential"
    green_secret_reference = (
        "{{secrets/mip-agent-proxy/" f"oauth-client-secret-{green_credential_id}}}}}"
    )
    green_contract = {
        **RESOURCE_CONTRACT,
        "proxy_caller_credential_id": green_credential_id,
        "proxy_caller_secret_reference": green_secret_reference,
    }
    green_digest = gateway_exact_resource_digest(green_contract)
    active_binding = gateway_runtime_binding_hash(
        endpoint="green-gateway",
        supervisor_id="supervisor-id",
        upstream_endpoint="supervisor-endpoint",
        runtime_application_id="runtime-client",
        workspace_host="https://workspace.cloud.databricks.com",
        model_name="mip.audit.proxy",
        model_version=7,
        inference_table="mip.audit.inference",
        proxy_caller_application_id=PROXY_CLIENT_ID,
        proxy_caller_credential_id=green_credential_id,
        proxy_caller_secret_reference=green_secret_reference,
    )
    green_payload = json.loads(json.dumps(_payload()))
    green_values = {
        "MIP_AGENT_PROXY_CREDENTIAL_ID": green_credential_id,
        "MIP_AGENT_PROXY_SECRET_REFERENCE": green_secret_reference,
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256": green_digest,
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON": (
            canonical_gateway_runtime_resource_contract(green_contract)
        ),
    }
    for item in green_payload["env_vars"]:
        if item["name"] in green_values:
            item["value"] = green_values[item["name"]]
    green_deployment = _deployment(
        "deployment-green",
        artifact="/Workspace/Users/app-id/src/deployment-green",
    )
    green_deployment.update_time = "2026-07-17T00:01:00Z"
    workspace.apps.deployments.append(green_deployment)
    workspace.apps.active_deployment = green_deployment
    monkeypatch.setattr(
        rollback,
        "resolve_exact_resource_proof",
        lambda *_args, **_kwargs: SimpleNamespace(
            contract=green_contract,
            digest=green_digest,
        ),
    )
    rollback.capture_current(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        payload=green_payload,
        base_url="https://mip.example",
        bearer_token="token",
        expected_git_sha=GIT_SHA,
        expected_gateway_binding=active_binding,
        **CAPTURE_ARGS,
    )

    # Simulate a fresh process: recover authority only from the signed record.
    recovered = rollback.verified_signed_last_good_contract(
        workspace,
        app_name=APP_NAME,
        scope="mip",
    )
    assert recovered.active_proxy_credential_id == green_credential_id
    assert recovered.pending_proxy_credential_retirement_ids == (PROXY_CREDENTIAL_ID,)

    provider_checks: list[str] = []
    rollback.complete_proxy_credential_retirement(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        proxy_application_id=PROXY_CLIENT_ID,
        retained_credential_id=green_credential_id,
        retired_credential_ids=(PROXY_CREDENTIAL_ID,),
        assert_provider_cleanup=lambda: provider_checks.append("exact"),
    )

    completed = rollback.verified_signed_last_good_contract(
        workspace,
        app_name=APP_NAME,
        scope="mip",
    )
    assert provider_checks == ["exact"]
    assert completed.pending_proxy_credential_retirement_ids == ()


def test_proxy_retirement_rejects_wrong_application_before_provider_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(
        rollback,
        "_health",
        lambda *_a, **_kw: (GIT_SHA, _binding(), LEASE_ID),
    )
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
    provider_checked = False

    def provider_check() -> None:
        nonlocal provider_checked
        provider_checked = True

    with pytest.raises(RuntimeError, match="proxy application"):
        rollback.complete_proxy_credential_retirement(
            workspace,
            app_name=APP_NAME,
            scope="mip",
            proxy_application_id="wrong-proxy",
            retained_credential_id=PROXY_CREDENTIAL_ID,
            retired_credential_ids=(),
            assert_provider_cleanup=provider_check,
        )

    assert provider_checked is False


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


def test_capture_rejects_live_matching_owner_that_is_not_authenticated_deployer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(
        rollback,
        "_health",
        lambda *_args, **_kwargs: (GIT_SHA, _binding(), LEASE_ID),
    )
    monkeypatch.setattr(
        rollback,
        "authenticated_reviewed_function_owner",
        lambda *_args, **_kwargs: "different-authenticated-owner",
    )

    with pytest.raises(
        RuntimeError,
        match="candidate reviewed-function owner is not the authenticated deployer",
    ):
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


def test_reviewed_resources_file_accepts_exact_source_free_payload(tmp_path) -> None:
    payload = tmp_path / "app-resources.json"
    payload.write_text(
        json.dumps({"name": APP_NAME, "resources": APP_RESOURCES}),
        encoding="utf-8",
    )

    assert rollback_inputs.reviewed_resources_file(str(payload)) == APP_RESOURCES


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
    assert observed["proxy_caller_application_id"] == PROXY_CLIENT_ID
    assert observed["proxy_caller_credential_id"] == PROXY_CREDENTIAL_ID
    assert observed["proxy_caller_secret_reference"] == PROXY_SECRET_REFERENCE
    assert observed["reviewed_function_owner"] == "reviewed-owner"
    assert observed["require_resource_binding"] is True


@pytest.mark.parametrize(
    "missing",
    (
        "MIP_AGENT_PROXY_CLIENT_ID",
        "MIP_AGENT_PROXY_CREDENTIAL_ID",
        "MIP_AGENT_PROXY_SECRET_REFERENCE",
    ),
)
def test_payload_resource_proof_requires_complete_proxy_binding(missing: str) -> None:
    payload = _payload()
    payload["env_vars"] = [
        item
        for item in payload["env_vars"]
        if isinstance(item, dict) and item.get("name") != missing
    ]

    with pytest.raises(RuntimeError, match="lacks its exact Gateway resource contract"):
        rollback._payload_resource_proof(
            _workspace(),
            payload=payload,
            genie_space_id=GENIE_SPACE_ID,
        )


def test_stored_v6_proof_uses_signed_reviewed_function_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def _resolve(*_args: object, **kwargs: object) -> object:
        observed.update(kwargs)
        return SimpleNamespace(contract=RESOURCE_CONTRACT, digest=RESOURCE_DIGEST)

    monkeypatch.setattr(rollback, "resolve_exact_resource_proof", _resolve)
    monkeypatch.setattr(
        rollback,
        "authenticated_reviewed_function_owner",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("current signed records must not derive owner authority")
        ),
    )

    rollback._stored_resource_proof(
        _workspace(),
        record={
            "version": rollback.RECORD_VERSION,
            "payload": _immutable_payload(),
            "gateway_resources": {
                **RESOURCE_CONTRACT,
                "resource_digest": RESOURCE_DIGEST,
            },
        },
    )

    assert observed["reviewed_function_owner"] == "reviewed-owner"
    assert observed["allow_legacy_reviewed_function_contract"] is False


def test_stored_v6_proof_authenticates_bounded_pre_owner_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _immutable_payload()
    payload["env_vars"] = [
        item
        for item in payload["env_vars"]
        if isinstance(item, dict) and item.get("name") != "MIP_REVIEWED_FUNCTION_OWNER"
    ]
    authenticated: dict[str, object] = {}
    observed: dict[str, object] = {}

    def _owner(workspace: object, *, catalog: str) -> str:
        authenticated.update(workspace=workspace, catalog=catalog)
        return "reviewed-owner"

    def _resolve(*_args: object, **kwargs: object) -> object:
        observed.update(kwargs)
        return SimpleNamespace(contract=RESOURCE_CONTRACT, digest=RESOURCE_DIGEST)

    workspace = _workspace()
    monkeypatch.setattr(
        rollback,
        "authenticated_reviewed_function_owner",
        _owner,
    )
    monkeypatch.setattr(rollback, "resolve_exact_resource_proof", _resolve)

    rollback._stored_resource_proof(
        workspace,
        record={
            "version": rollback.RECORD_VERSION,
            "payload": payload,
            "gateway_resources": {
                **RESOURCE_CONTRACT,
                "resource_digest": RESOURCE_DIGEST,
            },
        },
    )

    assert authenticated == {"workspace": workspace, "catalog": "mip"}
    assert observed["reviewed_function_owner"] == "reviewed-owner"
    assert observed["expected"] == {
        **RESOURCE_CONTRACT,
        "resource_digest": RESOURCE_DIGEST,
    }
    assert observed["allow_legacy_reviewed_function_contract"] is True


def _legacy_gateway_resources() -> dict[str, str]:
    contract = {field: f"legacy-{field}" for field in LEGACY_GATEWAY_RESOURCE_FIELDS}
    contract["proof_version"] = GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION
    contract["workspace_host"] = RESOURCE_CONTRACT["workspace_host"]
    return {
        **contract,
        "resource_digest": legacy_gateway_resource_digest(contract),
    }


def _prior_v2_legacy_gateway_resources() -> dict[str, str]:
    contract = {
        field: f"prior-{field}"
        for field in LEGACY_GATEWAY_RESOURCE_FIELDS
        if field != "workspace_host"
    }
    contract["proof_version"] = PRIOR_GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION
    return {
        **contract,
        "resource_digest": prior_v2_gateway_resource_digest(contract),
    }


def test_prior_v2_payload_binding_preserves_old_hash_and_rejects_contradiction() -> None:
    payload = _prior_v2_payload()
    binding = payload_gateway_binding(payload)

    environment = {
        item["name"]: item["value"]
        for item in payload["env_vars"]
        if "value" in item
    }
    expected_binding = hashlib.sha256(
        "\0".join(
            environment[name]
            for name in (
                "MIP_AGENT_SERVING_ENDPOINT",
                "MIP_AGENT_SUPERVISOR_ID",
                "MIP_AGENT_SUPERVISOR_ENDPOINT",
                "MIP_AGENT_RUNTIME_CLIENT_ID",
                "MIP_AI_GATEWAY_AGENT_MODEL",
                "MIP_AI_GATEWAY_AGENT_MODEL_VERSION",
                "MIP_AI_GATEWAY_INFERENCE_TABLE",
                "MIP_AGENT_PROXY_CLIENT_ID",
                "MIP_AGENT_PROXY_CREDENTIAL_ID",
                "MIP_AGENT_PROXY_SECRET_REFERENCE",
            )
        ).encode()
    ).hexdigest()
    assert binding == expected_binding
    contract_item = next(
        item
        for item in payload["env_vars"]
        if item["name"] == "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON"
    )
    contract = json.loads(contract_item["value"])
    contract["gateway_endpoint"] = "different-gateway"
    contract_item["value"] = json.dumps(contract, sort_keys=True, separators=(",", ":"))
    with pytest.raises(RuntimeError, match="contradicts its signed proof"):
        payload_gateway_binding(payload)


def test_signed_prior_v6_record_loads_and_uses_transition_live_proof(
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
    payload = _prior_v2_payload()
    resources = _prior_v2_proxy_resources()
    record.update(
        payload=payload,
        payload_sha256=rollback._payload_digest(payload),
        gateway_binding_sha256=payload_gateway_binding(payload),
        gateway_resources=resources,
    )
    rollback._save_record(workspace, scope="mip", record=record)
    loaded = rollback._load_record(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        expected_lakebase_instance="mip-app-state",
    )
    observed: list[dict[str, str]] = []
    monkeypatch.setattr(
        rollback,
        "assert_live_legacy_gateway_resources",
        lambda _workspace, *, expected: observed.append(expected) or expected,
    )
    monkeypatch.setattr(
        rollback,
        "resolve_exact_resource_proof",
        lambda *_args, **_kwargs: pytest.fail("prior v2 must not enter current proof"),
    )

    proof = rollback._stored_resource_proof(workspace, record=loaded)

    assert proof.digest == resources["resource_digest"]
    assert observed == [resources]


def test_signed_prior_v5_record_loads_without_becoming_current(
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
    del workspace.secrets.values[("mip", rollback._record_key(APP_NAME))]
    record.update(version=5, gateway_resources=_prior_v2_legacy_gateway_resources())
    rollback._save_legacy_record(workspace, scope="mip", record=record)

    loaded = rollback._load_record(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        expected_lakebase_instance="mip-app-state",
    )

    assert loaded["version"] == 5
    assert loaded["gateway_resources"]["proof_version"] == (
        PRIOR_GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION
    )
    observed: list[dict[str, str]] = []
    monkeypatch.setattr(
        rollback,
        "assert_live_legacy_gateway_resources",
        lambda _workspace, *, expected: observed.append(expected) or expected,
    )
    monkeypatch.setattr(
        rollback,
        "authenticated_reviewed_function_owner",
        lambda *_args, **_kwargs: "reviewed-owner",
    )
    monkeypatch.setattr(rollback, "assert_reviewed_function_set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        rollback,
        "resolve_exact_resource_proof",
        lambda *_args, **_kwargs: pytest.fail("prior v5 must not enter current proof"),
    )

    proof = rollback._stored_resource_proof(workspace, record=loaded)

    assert proof.digest == loaded["gateway_resources"]["resource_digest"]
    assert observed == [loaded["gateway_resources"]]


def test_v5_stored_proof_requires_authenticated_reviewed_function_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    observed: dict[str, object] = {}

    def _owner(owner_workspace: object, *, catalog: str) -> str:
        observed.update(owner_workspace=owner_workspace, owner_catalog=catalog)
        return "reviewed-owner"

    def _assert_functions(function_workspace: object, **kwargs: object) -> None:
        observed.update(function_workspace=function_workspace, **kwargs)

    monkeypatch.setattr(rollback, "authenticated_reviewed_function_owner", _owner)
    monkeypatch.setattr(rollback, "assert_reviewed_function_set", _assert_functions)
    monkeypatch.setattr(
        rollback,
        "assert_live_legacy_gateway_resources",
        lambda *_args, **_kwargs: _legacy_gateway_resources(),
    )

    rollback._stored_resource_proof(
        workspace,
        record={
            "version": rollback.LEGACY_RECORD_VERSION,
            "gateway_resources": _legacy_gateway_resources(),
        },
    )

    assert observed == {
        "owner_workspace": workspace,
        "owner_catalog": "legacy-catalog",
        "function_workspace": workspace,
        "catalog": "legacy-catalog",
        "expected_owner": "reviewed-owner",
        "allow_legacy_segment_determinism": True,
    }


@pytest.mark.parametrize(
    ("failure_source", "message"),
    (
        ("owner", "not owned by authenticated deployer"),
        ("function", "reviewed UC function owner drifted"),
        ("function", "reviewed UC function body drifted"),
        ("function", "reviewed UC function execution metadata drifted"),
        ("function", "reviewed UC function determinism drifted"),
    ),
)
def test_v5_stored_proof_rejects_reviewed_function_governance_drift(
    monkeypatch: pytest.MonkeyPatch,
    failure_source: str,
    message: str,
) -> None:
    monkeypatch.setattr(
        rollback,
        "assert_live_legacy_gateway_resources",
        lambda *_args, **_kwargs: _legacy_gateway_resources(),
    )
    if failure_source == "owner":
        monkeypatch.setattr(
            rollback,
            "authenticated_reviewed_function_owner",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(message)),
        )
    else:
        monkeypatch.setattr(
            rollback,
            "assert_reviewed_function_set",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(message)),
        )

    with pytest.raises(RuntimeError, match=message):
        rollback._stored_resource_proof(
            _workspace(),
            record={
                "version": rollback.LEGACY_RECORD_VERSION,
                "gateway_resources": _legacy_gateway_resources(),
            },
        )


def test_load_falls_back_to_genuinely_signed_v5_record(
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
    del workspace.secrets.values[("mip", rollback._record_key(APP_NAME))]
    record["version"] = 5
    record["gateway_resources"] = _legacy_gateway_resources()
    rollback._save_legacy_record(workspace, scope="mip", record=record)

    loaded = rollback._load_record(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        expected_lakebase_instance="mip-app-state",
    )

    assert loaded["version"] == 5
    assert loaded["gateway_resources"] == _legacy_gateway_resources()
    monkeypatch.setattr(rollback, "_stored_resource_proof", lambda *_args, **_kwargs: None)
    signed = rollback.verified_signed_last_good_contract(
        workspace,
        app_name=APP_NAME,
        scope="mip",
    )
    assert signed.record_version == 5
    assert signed.proxy_rollback_mode == "legacy-proxyless"
    assert signed.proxy_application_id is None
    assert signed.supervisor_endpoint_id == "legacy-supervisor_endpoint_id"


def test_v6_capture_deletes_legacy_key_only_after_durable_readback(
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
    record["version"] = 5
    record["gateway_resources"] = _legacy_gateway_resources()
    rollback._save_legacy_record(workspace, scope="mip", record=record)
    legacy_key = ("mip", f"app-last-good-v5-{APP_NAME}")
    assert legacy_key in workspace.secrets.values

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

    assert legacy_key not in workspace.secrets.values
    assert ("mip", rollback._record_key(APP_NAME)) in workspace.secrets.values


def test_legacy_gateway_contract_rejects_current_proxy_fields() -> None:
    resources = _legacy_gateway_resources()
    resources["proxy_caller_application_id"] = PROXY_CLIENT_ID
    resources["resource_digest"] = "a" * 64

    with pytest.raises(RuntimeError, match="legacy App rollback Gateway resource"):
        validated_legacy_gateway_resources(resources)


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


def test_capture_requiesces_when_deployment_drifts_after_treatment_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    modes: list[str] = []
    health_calls = 0

    def health(*_args: object, **_kwargs: object) -> tuple[str, str, str]:
        nonlocal health_calls
        health_calls += 1
        if health_calls == 2:
            workspace.apps.active_deployment = _deployment("deployment-other")
        return GIT_SHA, _binding(), LEASE_ID

    monkeypatch.setattr(rollback, "_health", health)
    monkeypatch.setattr(
        rollback,
        "converge_campaign_treatment_access",
        lambda **kwargs: modes.append(str(kwargs["mode"])) or True,
    )

    with pytest.raises(RuntimeError, match="post-treatment proof"):
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

    assert modes == ["quiesce", "runtime", "quiesce"]


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
    restored_env = rollback._env_map(workspace.apps.deployed_payload)
    assert restored_env["MIP_LAKEBASE_INSTANCE"] == "mip-app-state"
    assert restored_env["LAKEBASE_INSTANCE_NAME"] == "mip-app-state"
    assert _record(workspace)["deployment_id"] == "deployment-restored"


def test_restore_rejects_signed_record_without_exact_lakebase_binding(
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
    record["payload"]["env_vars"] = [
        item
        for item in record["payload"]["env_vars"]
        if item["name"] not in {"MIP_LAKEBASE_INSTANCE", "LAKEBASE_INSTANCE_NAME"}
    ]
    record["payload_sha256"] = rollback._payload_digest(record["payload"])
    rollback._save_record(workspace, scope="mip", record=record)

    with pytest.raises(RuntimeError, match="Lakebase binding is invalid"):
        rollback.restore_last_good(
            workspace,
            app_name=APP_NAME,
            scope="mip",
            base_url="https://mip.example",
            bearer_token="token",
            **TREATMENT_ARGS,
        )

    assert workspace.apps.deployed_payload is None


def test_restore_rejects_contract_changed_after_proxy_identity_binding(
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

    with pytest.raises(RuntimeError, match="changed after identity binding"):
        rollback.restore_last_good(
            workspace,
            app_name=APP_NAME,
            scope="mip",
            base_url="https://mip.example",
            bearer_token="token",
            expected_rollback_deployment_id="different-deployment",
            **TREATMENT_ARGS,
        )

    assert workspace.apps.deployed_payload is None


def test_restore_ignores_pre_v5_rollback_key(
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
    v5_key = ("mip", rollback._record_key(APP_NAME))
    workspace.secrets.values[("mip", f"app-last-good-v4-{APP_NAME}")] = (
        workspace.secrets.values.pop(v5_key)
    )

    with pytest.raises(RuntimeError, match="no server-owned last-good"):
        rollback.restore_last_good(
            workspace,
            app_name=APP_NAME,
            scope="mip",
            base_url="https://mip.example",
            bearer_token="token",
            **TREATMENT_ARGS,
        )

    assert workspace.apps.deployed_payload is None


def test_restore_rejects_signed_record_for_another_lakebase_target(
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
    monkeypatch.setenv("MIP_LAKEBASE_INSTANCE", "other-target-state")
    monkeypatch.setenv("LAKEBASE_INSTANCE_NAME", "other-target-state")

    with pytest.raises(RuntimeError, match="does not match the deployment target"):
        rollback.restore_last_good(
            workspace,
            app_name=APP_NAME,
            scope="mip",
            base_url="https://mip.example",
            bearer_token="token",
            **TREATMENT_ARGS,
        )

    assert workspace.apps.deployed_payload is None


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
    workspace.apps.resources = [
        *APP_RESOURCES[:-1],
        {
            "name": "sql_warehouse",
            "sql_warehouse": {"id": "candidate-warehouse", "permission": "CAN_USE"},
        },
    ]

    with pytest.raises(RuntimeError, match="pending deployment"):
        rollback.ensure_current(
            workspace,
            app_name=APP_NAME,
            scope="mip",
            base_url="https://mip.example",
            bearer_token="token",
            **TREATMENT_ARGS,
        )

    assert workspace.apps.resource_updates == 0
    assert workspace.apps.started == 0


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
    acl_reads: list[str] = []
    monkeypatch.setattr(
        rollback,
        "preserve_blue_and_revoke_managed_candidates",
        lambda *_args, **_kwargs: acl_reads.append("inspect") or "managed",
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
    assert acl_reads == []


def test_ensure_restores_signed_resources_after_interrupted_candidate_update(
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
    candidate_resources = [
        *APP_RESOURCES[:-1],
        {
            "name": "sql_warehouse",
            "sql_warehouse": {"id": "candidate-warehouse", "permission": "CAN_USE"},
        },
    ]
    workspace.apps.update(
        APP_NAME,
        SimpleNamespace(as_dict=lambda: {"resources": candidate_resources}),
    )
    health_calls = 0

    def _signed_blue_health(*_args: object, **_kwargs: object) -> tuple[str, str, str]:
        nonlocal health_calls
        health_calls += 1
        assert workspace.apps.resources == APP_RESOURCES
        return GIT_SHA, _binding(), LEASE_ID

    monkeypatch.setattr(rollback, "_health", _signed_blue_health)

    endpoint = rollback.ensure_current(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        base_url="https://mip.example",
        bearer_token="token",
        **TREATMENT_ARGS,
    )

    assert endpoint == "green-gateway"
    assert workspace.apps.resource_updates == 2
    assert workspace.apps.resources == APP_RESOURCES
    assert workspace.apps.active_deployment.deployment_id == "deployment-blue"
    assert workspace.apps.deployed_payload is None
    assert workspace.apps.started == 0
    assert health_calls == 1


def test_ensure_fails_closed_when_signed_resource_restore_fails(
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
        *APP_RESOURCES,
        {
            "name": "candidate_secret",
            "secret": {
                "scope": "candidate",
                "key": "headers",
                "permission": "READ",
            },
        },
    ]
    modes: list[str] = []
    monkeypatch.setattr(
        rollback,
        "converge_campaign_treatment_access",
        lambda **kwargs: modes.append(str(kwargs["mode"])) or True,
    )
    monkeypatch.setattr(
        rollback,
        "restore_signed_app_resource_contract",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("resource restore failed")),
    )

    with pytest.raises(RuntimeError, match="resource restore failed"):
        rollback.ensure_current(
            workspace,
            app_name=APP_NAME,
            scope="mip",
            base_url="https://mip.example",
            bearer_token="token",
            **TREATMENT_ARGS,
        )

    assert modes == ["quiesce", "quiesce"]
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
    workspace.apps.resources = [
        *APP_RESOURCES[:-1],
        {
            "name": "sql_warehouse",
            "sql_warehouse": {"id": "candidate-warehouse", "permission": "CAN_USE"},
        },
    ]
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

    assert workspace.apps.resource_updates == 0


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


def test_inspect_allows_resource_drift_then_restore_reconciles_blue_resources(
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
    workspace.apps.resources.append(
        {
            "name": "otel_headers",
            "secret": {
                "scope": "candidate-observability",
                "key": "headers",
                "permission": "READ",
            },
        }
    )

    signed = rollback.verified_signed_last_good_contract(
        workspace,
        app_name=APP_NAME,
        scope="mip",
    )

    assert signed.deployment_id == "deployment-blue"
    assert workspace.apps.resource_updates == 0
    assert workspace.apps.resources != APP_RESOURCES

    rollback.restore_last_good(
        workspace,
        app_name=APP_NAME,
        scope="mip",
        base_url="https://mip.example",
        bearer_token="token",
        expected_rollback_deployment_id=signed.deployment_id,
        **TREATMENT_ARGS,
    )

    assert workspace.apps.resource_updates == 1
    assert workspace.apps.resources == APP_RESOURCES
    assert workspace.apps.deployed_payload["source_code_path"] == ARTIFACT


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
        if reads == 3:
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


def test_restore_preserves_legacy_blue_and_revokes_only_managed_candidate(
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
    calls: list[dict[str, object]] = []
    lease_authorities: list[dict[str, object]] = []
    monkeypatch.setattr(
        rollback,
        "held_assertion",
        lambda _workspace, **kwargs: lease_authorities.append(kwargs)
        or (lambda: None),
    )
    monkeypatch.setattr(
        rollback,
        "preserve_blue_and_revoke_managed_candidates",
        lambda *_a, **kw: calls.append(kw) or "legacy",
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

    assert len(calls) == 1
    assert lease_authorities == [
        {
            "app_name": APP_NAME,
            "lease_id": LEASE_ID,
            "source_git_sha": GIT_SHA,
            "operation": "signed App rollback Gateway ACL mutation",
        }
    ]
    assert callable(calls[0].pop("assert_before_mutation"))
    assert calls == [
        {
            "app_name": APP_NAME,
            "blue_endpoint": "green-gateway",
            "app_client_id": "app-client",
            "app_scim_id": "app-scim-id",
            "candidate_endpoints": ("unverified-green",),
        }
    ]


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

    assert workspace.apps.resource_updates == 0


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

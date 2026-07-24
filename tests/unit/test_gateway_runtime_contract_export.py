from __future__ import annotations

import base64
import subprocess
from types import SimpleNamespace

import pytest

from backend.agents.gateway_contract import (
    DEFAULT_GATEWAY_AGENT_EXPERIMENT,
    DEFAULT_GATEWAY_AGENT_MODEL,
    DEFAULT_GATEWAY_ENDPOINT,
    GATEWAY_PROXY_SOURCE_HASH_TAG,
    GATEWAY_UPSTREAM_TAG,
    gateway_exact_resource_digest,
    gateway_proxy_source_hash,
    gateway_runtime_binding_hash,
    gateway_runtime_resource_environment,
    sign_gateway_runtime_resource_contract,
    verified_gateway_runtime_resource_environment,
)
from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tools import verify_deployed_app_contract as deployed_contract
from tools.databricks import export_gateway_runtime_contract as export_contract
from tools.databricks import gateway_model_attestation as attestation
from tools.databricks.provision_gateway_responses_agent import (
    _ENDPOINT_DESCRIPTION,
    _STATIC_ENV,
    gateway_agent_model_name,
    gateway_experiment_name,
    gateway_inference_table_prefix,
    gateway_resource_hash,
)

_SUPERVISOR_ID = "supervisor-123"
_SUPERVISOR_ENDPOINT_ID = "supervisor-endpoint-id"
_UPSTREAM = "mas-supervisor-endpoint"
_PROXY_CLIENT_ID = "proxy-client"
_PROXY_CREDENTIAL_ID = "proxy-credential"
_PROXY_SECRET_REFERENCE = "{{secrets/mip-agent-proxy/oauth-client-secret-proxy-credential}}"
_MODEL_VERIFY_KEY = derive_gateway_proof_verify_key(
    base64.urlsafe_b64encode(b"e" * 32).decode("ascii").rstrip("=")
)


def _experiment_acl(
    *extra: dict[str, object],
    include_runtime: bool = True,
    runtime_inherited: bool = True,
    admins_inherited: bool = True,
) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    if include_runtime:
        entries.append(
            {
                "service_principal_name": "runtime-client",
                "all_permissions": [
                    {
                        "permission_level": "CAN_MANAGE",
                        "inherited": runtime_inherited,
                        "inherited_from_object": (
                            ["/directories/runtime-home-id"] if runtime_inherited else []
                        ),
                    }
                ],
            }
        )
    entries.append(
        {
            "group_name": "admins",
            "all_permissions": [
                {
                    "permission_level": "CAN_MANAGE",
                    "inherited": admins_inherited,
                    "inherited_from_object": ["/directories/"] if admins_inherited else [],
                }
            ],
        }
    )
    entries.extend(extra)
    return {"access_control_list": entries}


def _source_hash() -> str:
    return gateway_proxy_source_hash(
        upstream_endpoint=_UPSTREAM,
        catalog="mip",
        genie_space_id="space-123",
    )


def _resource_hash() -> str:
    return gateway_resource_hash(
        source_hash=_source_hash(),
        supervisor_id=_SUPERVISOR_ID,
        supervisor_endpoint_id=_SUPERVISOR_ENDPOINT_ID,
        runtime_application_id="runtime-client",
        model_name=DEFAULT_GATEWAY_AGENT_MODEL,
        experiment_name=DEFAULT_GATEWAY_AGENT_EXPERIMENT,
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
        attestation_verify_key=_MODEL_VERIFY_KEY,
        proxy_caller_application_id=_PROXY_CLIENT_ID,
        proxy_caller_credential_id=_PROXY_CREDENTIAL_ID,
        proxy_caller_secret_reference=_PROXY_SECRET_REFERENCE,
    )


def _model_name() -> str:
    return gateway_agent_model_name(
        base_model_name=DEFAULT_GATEWAY_AGENT_MODEL,
        contract_hash=_resource_hash(),
    )


def _inference_table() -> str:
    return "mip.audit." + gateway_inference_table_prefix(
        base_prefix="mip_agent_gateway_growth_agent",
        contract_hash=_resource_hash(),
    )


def _experiment_name() -> str:
    return gateway_experiment_name(
        base_experiment_name=DEFAULT_GATEWAY_AGENT_EXPERIMENT,
        contract_hash=_resource_hash(),
        runtime_application_id="runtime-client",
    )


def _resolve_contract(client: object, **kwargs: object) -> dict[str, str]:
    return export_contract.resolve_contract(
        client,
        proxy_caller_application_id=_PROXY_CLIENT_ID,
        proxy_caller_credential_id=_PROXY_CREDENTIAL_ID,
        proxy_caller_secret_reference=_PROXY_SECRET_REFERENCE,
        **kwargs,
    )


def _resolve_exact_resource_proof(client: object, **kwargs: object):
    return export_contract.resolve_exact_resource_proof(
        client,
        proxy_caller_application_id=_PROXY_CLIENT_ID,
        proxy_caller_credential_id=_PROXY_CREDENTIAL_ID,
        proxy_caller_secret_reference=_PROXY_SECRET_REFERENCE,
        **kwargs,
    )


@pytest.fixture(autouse=True)
def _exact_supervisor_contract_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    signing_key = base64.urlsafe_b64encode(b"e" * 32).decode("ascii").rstrip("=")
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", signing_key)
    monkeypatch.setenv(
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY",
        derive_gateway_proof_verify_key(signing_key),
    )
    monkeypatch.setenv("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING", "1")
    monkeypatch.setattr(
        export_contract,
        "assert_exact_supervisor_contract",
        lambda *_args, **_kwargs: None,
    )


def _endpoint_details(
    *,
    pending: object | None = None,
    inference_prefix: str | None = None,
    upstream: str = _UPSTREAM,
) -> object:
    source_hash = _source_hash()
    if inference_prefix is None:
        inference_prefix = gateway_inference_table_prefix(
            base_prefix="mip_agent_gateway_growth_agent",
            contract_hash=_resource_hash(),
        )
    return SimpleNamespace(
        id="gateway-endpoint-id",
        creator="runtime-client",
        description=_ENDPOINT_DESCRIPTION,
        route_optimized=False,
        pending_config=pending,
        state=SimpleNamespace(ready="READY"),
        task="agent/v1/responses",
        config=SimpleNamespace(
            served_entities=[
                SimpleNamespace(
                    burst_scaling_enabled=False,
                    entity_name=_model_name(),
                    entity_version="7",
                    name="mip-growth-supervisor-proxy-7",
                    environment_vars={
                        **_STATIC_ENV,
                        "MIP_UPSTREAM_SUPERVISOR_ID": _SUPERVISOR_ID,
                        "MIP_UPSTREAM_SUPERVISOR_ENDPOINT": upstream,
                        "MIP_UPSTREAM_SUPERVISOR_CREATOR": "runtime-client",
                        "MIP_UPSTREAM_PROXY_CLIENT_ID": _PROXY_CLIENT_ID,
                        "MIP_UPSTREAM_PROXY_CREDENTIAL_ID": _PROXY_CREDENTIAL_ID,
                        "MIP_UPSTREAM_PROXY_CLIENT_SECRET": _PROXY_SECRET_REFERENCE,
                        "MIP_SUPERVISOR_CATALOG": "mip",
                        "MIP_SUPERVISOR_GENIE_SPACE_ID": "space-123",
                        "MIP_SUPERVISOR_CONTRACT_SHA256": export_contract.supervisor_contract_hash(
                            genie_space_id="space-123",
                            catalog="mip",
                        ),
                        "MLFLOW_EXPERIMENT_ID": "experiment-7",
                    },
                    workload_size="Small",
                    workload_type="CPU",
                    scale_to_zero_enabled=True,
                )
            ],
            traffic_config=SimpleNamespace(
                routes=[
                    SimpleNamespace(
                        served_entity_name="mip-growth-supervisor-proxy-7",
                        traffic_percentage=100,
                    )
                ]
            ),
        ),
        tags=[
            SimpleNamespace(key=GATEWAY_PROXY_SOURCE_HASH_TAG, value=source_hash),
            SimpleNamespace(key=GATEWAY_UPSTREAM_TAG, value=_UPSTREAM),
        ],
        ai_gateway=SimpleNamespace(
            inference_table_config=SimpleNamespace(
                enabled=True,
                catalog_name="mip",
                schema_name="audit",
                table_name_prefix=inference_prefix,
            )
        ),
    )


class _ApiClient:
    def __init__(
        self,
        supervisors: list[dict[str, str]],
        experiment_acl: dict[str, object],
    ) -> None:
        self.supervisors = supervisors
        self.experiment_acl = experiment_acl

    def do(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, object] | None = None,
    ) -> object:
        assert method == "GET"
        if path == "/api/2.1/supervisor-agents":
            assert query == {"page_size": 100}
            return {"supervisor_agents": self.supervisors}
        if path.startswith("/api/2.1/supervisor-agents/"):
            assert query is None
            supervisor_id = path.rsplit("/", 1)[-1]
            matches = [
                row
                for row in self.supervisors
                if row.get("supervisor_agent_id") == supervisor_id
            ]
            return matches[0] if len(matches) == 1 else {}
        assert query is None
        assert path == "/api/2.0/permissions/experiments/experiment-7"
        return self.experiment_acl


def _workspace(
    *,
    supervisors: list[dict[str, str]] | None = None,
    details: object | dict[str, object] | None = None,
    experiment_acl: dict[str, object] | None = None,
) -> object:
    rows = supervisors
    if rows is None:
        rows = [
            {
                "display_name": "Mortgage Growth Agent",
                "supervisor_agent_id": _SUPERVISOR_ID,
                "endpoint_name": _UPSTREAM,
                "creator": "runtime-client",
            }
        ]

    class _ServingEndpoints:
        def list(self) -> list[object]:
            if isinstance(details, dict):
                return [SimpleNamespace(name=name) for name in details]
            return [SimpleNamespace(name=DEFAULT_GATEWAY_ENDPOINT)]

        def get(self, endpoint: str) -> object:
            if endpoint == _UPSTREAM:
                return SimpleNamespace(id="supervisor-endpoint-id", creator="runtime-client")
            if isinstance(details, dict):
                return details[endpoint]
            assert endpoint == DEFAULT_GATEWAY_ENDPOINT
            return details or _endpoint_details()

        def put(self, _endpoint: str, *, rate_limits: list[object]) -> object:
            assert rate_limits == []
            return SimpleNamespace(rate_limits=[])

    return SimpleNamespace(
        api_client=_ApiClient(rows, experiment_acl or _experiment_acl()),
        workspace=SimpleNamespace(
            get_status=lambda path: SimpleNamespace(
                path=path,
                object_type="DIRECTORY",
                object_id="runtime-home-id",
            )
        ),
        serving_endpoints=_ServingEndpoints(),
        registered_models=SimpleNamespace(
            get=lambda _name: SimpleNamespace(owner="runtime-client")
        ),
    )


def _model_registry(*, source_hash: str | None = None, upstream: str = _UPSTREAM) -> object:
    reviewed_hash = source_hash or gateway_proxy_source_hash(
        upstream_endpoint=_UPSTREAM,
        catalog="mip",
        genie_space_id="space-123",
    )
    contract = {
        "full_name": _model_name(),
        "model_source": "models:/m-reviewed-proxy",
        "source_hash": reviewed_hash,
        "supervisor_id": _SUPERVISOR_ID,
        "supervisor_endpoint_id": "supervisor-endpoint-id",
        "upstream_endpoint": upstream,
        "runtime_application_id": "runtime-client",
        "model_family": DEFAULT_GATEWAY_AGENT_MODEL,
        "experiment_base": DEFAULT_GATEWAY_AGENT_EXPERIMENT,
        "catalog": "mip",
        "genie_space_id": "space-123",
        "inference_schema": "audit",
        "inference_table_prefix": "mip_agent_gateway_growth_agent",
    }
    tags = attestation.sign_gateway_model_contract(**contract)
    return SimpleNamespace(
        get_model_version=lambda name, version: SimpleNamespace(
            name=name,
            version=version,
            source="models:/m-reviewed-proxy",
            status="READY",
            tags=tags,
        )
    )


def _tracking_client(
    *,
    name: str | None = None,
    owner: str = "runtime-client",
    lifecycle_stage: str = "active",
) -> object:
    experiment = SimpleNamespace(
        experiment_id="experiment-7",
        name=name or _experiment_name(),
        lifecycle_stage=lifecycle_stage,
        tags={"mlflow.ownerEmail": owner},
    )
    return SimpleNamespace(
        get_experiment=lambda _id: experiment,
        get_experiment_by_name=lambda _name: experiment,
    )


def _bound_workspace(
    workspace: object | None = None,
    *,
    model_registry: object | None = None,
    tracking_client: object | None = None,
) -> object:
    target = workspace or _workspace(details=_endpoint_details())
    registry = model_registry or _model_registry()
    experiments = tracking_client or _tracking_client()
    proof = _resolve_exact_resource_proof(
        target,
        supervisor_name="Mortgage Growth Agent",
        catalog="mip",
        genie_space_id="space-123",
        runtime_application_id="runtime-client",
        model_registry=registry,
        tracking_client=experiments,
        require_resource_binding=False,
    )
    signature = sign_gateway_runtime_resource_contract(proof.contract)
    binding = gateway_runtime_resource_environment(
        proof.contract,
        signature=signature,
        current_verify_key=derive_gateway_proof_verify_key(
            base64.urlsafe_b64encode(b"e" * 32).decode("ascii").rstrip("=")
        ),
    )
    details = target.serving_endpoints.get(proof.contract["gateway_endpoint"])
    details.config.served_entities[0].environment_vars.update(binding)
    return target


def test_resolve_contract_exports_exact_source_bound_runtime() -> None:
    contract = _resolve_contract(
        _bound_workspace(),
        supervisor_name="Mortgage Growth Agent",
        catalog="mip",
        genie_space_id="space-123",
        runtime_application_id="runtime-client",
        model_registry=_model_registry(),
        tracking_client=_tracking_client(),
    )

    expected_binding = gateway_runtime_binding_hash(
        endpoint=DEFAULT_GATEWAY_ENDPOINT,
        supervisor_id=_SUPERVISOR_ID,
        upstream_endpoint=_UPSTREAM,
        runtime_application_id="runtime-client",
        model_name=_model_name(),
        model_version=7,
        inference_table=_inference_table(),
        proxy_caller_application_id=_PROXY_CLIENT_ID,
        proxy_caller_credential_id=_PROXY_CREDENTIAL_ID,
        proxy_caller_secret_reference=_PROXY_SECRET_REFERENCE,
    )
    expected = {
        "MIP_AGENT_SERVING_ENDPOINT": DEFAULT_GATEWAY_ENDPOINT,
        "MIP_AGENT_SUPERVISOR_ENDPOINT": _UPSTREAM,
        "MIP_AGENT_SUPERVISOR_ID": _SUPERVISOR_ID,
        "MIP_AGENT_RUNTIME_CLIENT_ID": "runtime-client",
        "MIP_AGENT_PROXY_CLIENT_ID": _PROXY_CLIENT_ID,
        "MIP_AGENT_PROXY_CREDENTIAL_ID": _PROXY_CREDENTIAL_ID,
        "MIP_AGENT_PROXY_SECRET_REFERENCE": _PROXY_SECRET_REFERENCE,
        "MIP_AI_GATEWAY_ENDPOINT": DEFAULT_GATEWAY_ENDPOINT,
        "MIP_AI_GATEWAY_INFERENCE_TABLE": _inference_table(),
        "MIP_AI_GATEWAY_AGENT_MODEL": _model_name(),
        "MIP_AI_GATEWAY_AGENT_MODEL_VERSION": "7",
        "MIP_AI_GATEWAY_AGENT_MODEL_SOURCE": "models:/m-reviewed-proxy",
        "MIP_AI_GATEWAY_EXPERIMENT_NAME": _experiment_name(),
        "MIP_AI_GATEWAY_EXPERIMENT_ID": "experiment-7",
        "MIP_EXPECTED_AGENT_GATEWAY_BINDING_SHA256": expected_binding,
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256": contract[
            "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256"
        ],
    }
    assert expected.items() <= contract.items()
    verified = verified_gateway_runtime_resource_environment(contract)
    assert (
        gateway_exact_resource_digest(verified)
        == contract["MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256"]
    )


def test_exact_resource_proof_binds_experiment_model_endpoint_and_stored_digest() -> None:
    proof = _resolve_exact_resource_proof(
        _workspace(),
        supervisor_name="Mortgage Growth Agent",
        catalog="mip",
        genie_space_id="space-123",
        runtime_application_id="runtime-client",
        model_registry=_model_registry(),
        tracking_client=_tracking_client(),
    )

    assert proof.contract["gateway_endpoint_id"] == "gateway-endpoint-id"
    assert proof.contract["supervisor_endpoint_id"] == "supervisor-endpoint-id"
    assert proof.contract["gateway_model_source"] == "models:/m-reviewed-proxy"
    assert proof.contract["gateway_experiment_name"] == _experiment_name()
    assert proof.contract["gateway_experiment_id"] == "experiment-7"
    assert proof.contract["gateway_experiment_acl_json"].startswith('{"access_control_list":')
    assert len(proof.contract["gateway_experiment_acl_sha256"]) == 64
    assert proof.digest == gateway_exact_resource_digest(proof.contract)
    assert (
        _resolve_exact_resource_proof(
            _workspace(),
            supervisor_name="Mortgage Growth Agent",
            catalog="mip",
            genie_space_id="space-123",
            runtime_application_id="runtime-client",
            expected={**proof.contract, "resource_digest": proof.digest},
            model_registry=_model_registry(),
            tracking_client=_tracking_client(),
        ).digest
        == proof.digest
    )


def test_stored_proof_survives_only_the_reviewed_replacement_to_canonical_rename() -> None:
    replacement_name = "Mortgage Growth Agent [mip-agent-runtime-deadbeef1234]"
    replacement = {
        "display_name": replacement_name,
        "supervisor_agent_id": _SUPERVISOR_ID,
        "endpoint_name": _UPSTREAM,
        "creator": "runtime-client",
    }
    proof = _resolve_exact_resource_proof(
        _workspace(supervisors=[replacement]),
        supervisor_name="Mortgage Growth Agent",
        supervisor_id=_SUPERVISOR_ID,
        catalog="mip",
        genie_space_id="space-123",
        runtime_application_id="runtime-client",
        model_registry=_model_registry(),
        tracking_client=_tracking_client(),
    )
    expected = {**proof.contract, "resource_digest": proof.digest}
    canonical = {**replacement, "display_name": "Mortgage Growth Agent"}

    restored = _resolve_exact_resource_proof(
        _workspace(supervisors=[canonical]),
        supervisor_name="Mortgage Growth Agent",
        catalog="mip",
        genie_space_id="space-123",
        runtime_application_id="runtime-client",
        expected=expected,
        model_registry=_model_registry(),
        tracking_client=_tracking_client(),
    )
    assert restored.digest == proof.digest

    rogue = {**replacement, "display_name": "Unreviewed renamed Supervisor"}
    with pytest.raises(RuntimeError, match="stored Supervisor immutable identity drifted"):
        _resolve_exact_resource_proof(
            _workspace(supervisors=[rogue]),
            supervisor_name="Mortgage Growth Agent",
            catalog="mip",
            genie_space_id="space-123",
            runtime_application_id="runtime-client",
            expected=expected,
            model_registry=_model_registry(),
            tracking_client=_tracking_client(),
        )


def test_exact_resource_proof_rejects_stored_experiment_id_drift() -> None:
    proof = _resolve_exact_resource_proof(
        _workspace(),
        supervisor_name="Mortgage Growth Agent",
        catalog="mip",
        genie_space_id="space-123",
        runtime_application_id="runtime-client",
        model_registry=_model_registry(),
        tracking_client=_tracking_client(),
    )
    drifted = {**proof.contract, "gateway_experiment_id": "different-experiment"}
    with pytest.raises(RuntimeError, match="stored rollback contract"):
        _resolve_exact_resource_proof(
            _workspace(),
            supervisor_name="Mortgage Growth Agent",
            catalog="mip",
            genie_space_id="space-123",
            runtime_application_id="runtime-client",
            expected={
                **drifted,
                "resource_digest": gateway_exact_resource_digest(drifted),
            },
            model_registry=_model_registry(),
            tracking_client=_tracking_client(),
        )


def test_exact_resource_proof_requires_runtime_experiment_grant() -> None:
    with pytest.raises(RuntimeError, match="exact runtime CAN_MANAGE"):
        _resolve_exact_resource_proof(
            _workspace(experiment_acl=_experiment_acl(include_runtime=False)),
            supervisor_name="Mortgage Growth Agent",
            catalog="mip",
            genie_space_id="space-123",
            runtime_application_id="runtime-client",
            model_registry=_model_registry(),
            tracking_client=_tracking_client(),
        )


def test_exact_resource_proof_rejects_direct_runtime_experiment_grant() -> None:
    with pytest.raises(RuntimeError, match="home directory"):
        _resolve_exact_resource_proof(
            _workspace(experiment_acl=_experiment_acl(runtime_inherited=False)),
            supervisor_name="Mortgage Growth Agent",
            catalog="mip",
            genie_space_id="space-123",
            runtime_application_id="runtime-client",
            model_registry=_model_registry(),
            tracking_client=_tracking_client(),
        )


def test_exact_resource_proof_rejects_wrong_runtime_home_inheritance() -> None:
    acl = _experiment_acl()
    runtime_entry = acl["access_control_list"][0]  # type: ignore[index]
    runtime_entry["all_permissions"][0]["inherited_from_object"] = [  # type: ignore[index]
        "/directories/attacker-home"
    ]

    with pytest.raises(RuntimeError, match="home directory"):
        _resolve_exact_resource_proof(
            _workspace(experiment_acl=acl),
            supervisor_name="Mortgage Growth Agent",
            catalog="mip",
            genie_space_id="space-123",
            runtime_application_id="runtime-client",
            model_registry=_model_registry(),
            tracking_client=_tracking_client(),
        )


def test_exact_resource_proof_rejects_runtime_home_identity_drift() -> None:
    workspace = _workspace()
    workspace.workspace.get_status = lambda _path: SimpleNamespace(
        path="/Users/attacker-client",
        object_type="DIRECTORY",
        object_id="runtime-home-id",
    )

    with pytest.raises(RuntimeError, match="home directory identity"):
        _resolve_exact_resource_proof(
            workspace,
            supervisor_name="Mortgage Growth Agent",
            catalog="mip",
            genie_space_id="space-123",
            runtime_application_id="runtime-client",
            model_registry=_model_registry(),
            tracking_client=_tracking_client(),
        )


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (
            SimpleNamespace(
                path="/Users/runtime-client",
                object_type="FILE",
                object_id="runtime-home-id",
            ),
            "home directory identity",
        ),
        (
            SimpleNamespace(
                path="/Users/runtime-client",
                object_type="DIRECTORY",
                object_id=" ",
            ),
            "home directory identity",
        ),
    ],
    ids=["wrong-object-type", "blank-object-id"],
)
def test_exact_resource_proof_rejects_invalid_runtime_home_status(
    status: object,
    message: str,
) -> None:
    workspace = _workspace()
    workspace.workspace.get_status = lambda _path: status

    with pytest.raises(RuntimeError, match=message):
        _resolve_exact_resource_proof(
            workspace,
            supervisor_name="Mortgage Growth Agent",
            catalog="mip",
            genie_space_id="space-123",
            runtime_application_id="runtime-client",
            model_registry=_model_registry(),
            tracking_client=_tracking_client(),
        )


def test_exact_resource_proof_rejects_missing_runtime_home_status_api() -> None:
    workspace = _workspace()
    del workspace.workspace

    with pytest.raises(RuntimeError, match="could not resolve the Gateway runtime home"):
        _resolve_exact_resource_proof(
            workspace,
            supervisor_name="Mortgage Growth Agent",
            catalog="mip",
            genie_space_id="space-123",
            runtime_application_id="runtime-client",
            model_registry=_model_registry(),
            tracking_client=_tracking_client(),
        )


@pytest.mark.parametrize(
    "unexpected",
    [
        {
            "service_principal_name": "rogue-client",
            "all_permissions": [{"permission_level": "CAN_MANAGE", "inherited": False}],
        },
        {
            "user_name": "rogue@example.com",
            "all_permissions": [{"permission_level": "CAN_MANAGE", "inherited": False}],
        },
        {
            "group_name": "users",
            "all_permissions": [{"permission_level": "CAN_MANAGE", "inherited": True}],
        },
    ],
    ids=["service-principal", "user", "non-admin-group"],
)
def test_exact_resource_proof_rejects_other_experiment_principal(
    unexpected: dict[str, object],
) -> None:
    with pytest.raises(RuntimeError, match="unexpected principal"):
        _resolve_exact_resource_proof(
            _workspace(experiment_acl=_experiment_acl(unexpected)),
            supervisor_name="Mortgage Growth Agent",
            catalog="mip",
            genie_space_id="space-123",
            runtime_application_id="runtime-client",
            model_registry=_model_registry(),
            tracking_client=_tracking_client(),
        )


def test_exact_resource_proof_rejects_stored_experiment_acl_drift() -> None:
    proof = _resolve_exact_resource_proof(
        _workspace(),
        supervisor_name="Mortgage Growth Agent",
        catalog="mip",
        genie_space_id="space-123",
        runtime_application_id="runtime-client",
        model_registry=_model_registry(),
        tracking_client=_tracking_client(),
    )

    with pytest.raises(RuntimeError, match="stored rollback contract"):
        _resolve_exact_resource_proof(
            _workspace(experiment_acl=_experiment_acl(admins_inherited=False)),
            supervisor_name="Mortgage Growth Agent",
            catalog="mip",
            genie_space_id="space-123",
            runtime_application_id="runtime-client",
            expected={**proof.contract, "resource_digest": proof.digest},
            model_registry=_model_registry(),
            tracking_client=_tracking_client(),
        )


def test_stored_proof_revalidates_historical_resource_after_local_source_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proof = _resolve_exact_resource_proof(
        _workspace(),
        supervisor_name="Mortgage Growth Agent",
        catalog="mip",
        genie_space_id="space-123",
        runtime_application_id="runtime-client",
        model_registry=_model_registry(),
        tracking_client=_tracking_client(),
    )
    monkeypatch.setattr(export_contract, "gateway_proxy_source_hash", lambda **_kwargs: "b" * 64)

    restored = _resolve_exact_resource_proof(
        _workspace(),
        supervisor_name="Mortgage Growth Agent",
        catalog="mip",
        genie_space_id="space-123",
        runtime_application_id="runtime-client",
        expected={**proof.contract, "resource_digest": proof.digest},
        model_registry=_model_registry(),
        tracking_client=_tracking_client(),
    )

    assert restored.digest == proof.digest


def test_export_rejects_experiment_name_id_aliasing() -> None:
    with pytest.raises(RuntimeError, match="experiment name/ID binding drifted"):
        _resolve_contract(
            _workspace(),
            supervisor_name="Mortgage Growth Agent",
            catalog="mip",
            genie_space_id="space-123",
            runtime_application_id="runtime-client",
            model_registry=_model_registry(),
            tracking_client=_tracking_client(name="/Users/runtime-client/rogue-experiment"),
        )


def test_export_rejects_deleted_experiment() -> None:
    with pytest.raises(RuntimeError, match="experiment is not active"):
        _resolve_contract(
            _workspace(),
            supervisor_name="Mortgage Growth Agent",
            catalog="mip",
            genie_space_id="space-123",
            runtime_application_id="runtime-client",
            model_registry=_model_registry(),
            tracking_client=_tracking_client(lifecycle_stage="deleted"),
        )


def test_export_filters_human_canonical_after_config_before_runtime_green() -> None:
    human = _endpoint_details()
    human.creator = "human@example.com"
    human.config.served_entities[0].entity_name = "mip.audit.legacy_human_model"
    green = _endpoint_details()
    green.id = "green-endpoint-id"
    green_name = f"{DEFAULT_GATEWAY_ENDPOINT}-{_resource_hash()[:12]}"

    contract = _resolve_contract(
        _bound_workspace(_workspace(details={DEFAULT_GATEWAY_ENDPOINT: human, green_name: green})),
        supervisor_name="Mortgage Growth Agent",
        catalog="mip",
        genie_space_id="space-123",
        runtime_application_id="runtime-client",
        model_registry=_model_registry(),
        tracking_client=_tracking_client(),
    )

    assert contract["MIP_AI_GATEWAY_ENDPOINT"] == green_name


def test_resolve_contract_rejects_rogue_served_model_version_before_export() -> None:
    with pytest.raises(RuntimeError, match="Model version tags do not bind"):
        _resolve_contract(
            _workspace(),
            supervisor_name="Mortgage Growth Agent",
            catalog="mip",
            genie_space_id="space-123",
            runtime_application_id="runtime-client",
            model_registry=_model_registry(source_hash="b" * 64),
            tracking_client=_tracking_client(),
        )


@pytest.mark.parametrize("count", [0, 2], ids=["missing", "duplicate"])
def test_resolve_contract_requires_exactly_one_named_supervisor(count: int) -> None:
    supervisors = [
        {
            "display_name": "Mortgage Growth Agent",
            "supervisor_agent_id": f"supervisor-{index}",
            "endpoint_name": f"endpoint-{index}",
        }
        for index in range(count)
    ]

    with pytest.raises(RuntimeError, match=f"found {count}"):
        _resolve_contract(
            _workspace(supervisors=supervisors),
            supervisor_name="Mortgage Growth Agent",
            catalog="mip",
            genie_space_id="space-123",
            runtime_application_id="runtime-client",
            tracking_client=_tracking_client(),
        )


def test_named_supervisor_discovery_follows_all_pages() -> None:
    workspace = _workspace()
    inventory_calls: list[dict[str, object]] = []
    fallback = workspace.api_client

    class _PagedApi:
        def do(
            self,
            method: str,
            path: str,
            *,
            query: dict[str, object] | None = None,
        ) -> object:
            if path != "/api/2.1/supervisor-agents":
                return fallback.do(method, path, query=query)
            assert query is not None
            inventory_calls.append(query)
            if "page_token" not in query:
                return {
                    "supervisor_agents": [
                        {
                            "display_name": "Other Agent",
                            "supervisor_agent_id": "other-supervisor",
                            "endpoint_name": "other-endpoint",
                            "creator": "runtime-client",
                        }
                    ],
                    "next_page_token": "page-2",
                }
            assert query["page_token"] == "page-2"
            return {
                "supervisor_agents": [
                    {
                        "display_name": "Mortgage Growth Agent",
                        "supervisor_agent_id": _SUPERVISOR_ID,
                        "endpoint_name": _UPSTREAM,
                        "creator": "runtime-client",
                    }
                ]
            }

    workspace.api_client = _PagedApi()
    proof = _resolve_exact_resource_proof(
        workspace,
        supervisor_name="Mortgage Growth Agent",
        catalog="mip",
        genie_space_id="space-123",
        runtime_application_id="runtime-client",
        model_registry=_model_registry(),
        tracking_client=_tracking_client(),
    )

    assert proof.contract["supervisor_id"] == _SUPERVISOR_ID
    assert inventory_calls == [
        {"page_size": 100},
        {"page_size": 100, "page_token": "page-2"},
    ]


@pytest.mark.parametrize("failure", ["cycle", "duplicate"])
def test_supervisor_inventory_rejects_unsafe_pagination(failure: str) -> None:
    class _UnsafeApi:
        def do(
            self,
            _method: str,
            _path: str,
            *,
            query: dict[str, object],
        ) -> object:
            if "page_token" not in query:
                return {
                    "supervisor_agents": [
                        {"supervisor_agent_id": "supervisor-1"}
                    ],
                    "next_page_token": "page-2",
                }
            return {
                "supervisor_agents": [
                    {
                        "supervisor_agent_id": (
                            "supervisor-1" if failure == "duplicate" else "supervisor-2"
                        )
                    }
                ],
                "next_page_token": "page-2" if failure == "cycle" else "",
            }

    with pytest.raises(RuntimeError, match="duplicate|cycled"):
        export_contract._supervisors(SimpleNamespace(api_client=_UnsafeApi()))


@pytest.mark.parametrize(
    ("details", "message"),
    [
        (_endpoint_details(pending=SimpleNamespace()), "pending config update"),
        (_endpoint_details(inference_prefix="wrong_prefix"), "found 0"),
        (_endpoint_details(upstream="wrong-upstream"), "found 0"),
    ],
    ids=["pending", "wrong-inference-table", "wrong-upstream"],
)
def test_resolve_contract_rejects_gateway_binding_drift(
    details: object,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        _resolve_contract(
            _workspace(details=details),
            supervisor_name="Mortgage Growth Agent",
            catalog="mip",
            genie_space_id="space-123",
            runtime_application_id="runtime-client",
            model_registry=_model_registry(),
            tracking_client=_tracking_client(),
        )


def test_export_main_appends_exact_contract_to_github_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    github_env = tmp_path / "github-env"
    monkeypatch.setattr(export_contract, "WorkspaceClient", lambda: _bound_workspace())
    monkeypatch.setattr(
        export_contract,
        "MlflowClient",
        lambda **kwargs: (_model_registry() if kwargs.get("registry_uri") else _tracking_client()),
    )
    monkeypatch.setenv("GENIE_SPACE_ID", "space-123")
    monkeypatch.setenv("DATABRICKS_AGENT_RUNTIME_CLIENT_ID", "runtime-client")
    monkeypatch.setenv("DATABRICKS_AGENT_PROXY_CLIENT_ID", _PROXY_CLIENT_ID)
    monkeypatch.setenv("DATABRICKS_AGENT_PROXY_CREDENTIAL_ID", _PROXY_CREDENTIAL_ID)
    monkeypatch.setenv("MIP_AGENT_PROXY_SECRET_REFERENCE", _PROXY_SECRET_REFERENCE)

    assert export_contract.main(["--github-env", str(github_env)]) == 0

    rows = github_env.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 20
    assert f"MIP_AGENT_SERVING_ENDPOINT={DEFAULT_GATEWAY_ENDPOINT}" in rows
    assert f"MIP_AGENT_SUPERVISOR_ID={_SUPERVISOR_ID}" in rows
    assert f"MIP_AGENT_SUPERVISOR_ENDPOINT_ID={_SUPERVISOR_ENDPOINT_ID}" in rows
    assert "MIP_AI_GATEWAY_AGENT_MODEL_VERSION=7" in rows
    assert not any("CLIENT_SECRET=" in row or "TOKEN=" in row for row in rows)


def test_export_main_shell_env_survives_source_and_signature_verification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    shell_env = tmp_path / "gateway-runtime.env"
    shell_env.write_text(
        "MIP_AGENT_SERVING_ENDPOINT=stale-endpoint\nUNRELATED_VALUE=preserved\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(export_contract, "WorkspaceClient", lambda: _bound_workspace())
    monkeypatch.setattr(
        export_contract,
        "MlflowClient",
        lambda **kwargs: (_model_registry() if kwargs.get("registry_uri") else _tracking_client()),
    )
    monkeypatch.setenv("GENIE_SPACE_ID", "space-123")
    monkeypatch.setenv("DATABRICKS_AGENT_RUNTIME_CLIENT_ID", "runtime-client")
    monkeypatch.setenv("DATABRICKS_AGENT_PROXY_CLIENT_ID", _PROXY_CLIENT_ID)
    monkeypatch.setenv("DATABRICKS_AGENT_PROXY_CREDENTIAL_ID", _PROXY_CREDENTIAL_ID)
    monkeypatch.setenv("MIP_AGENT_PROXY_SECRET_REFERENCE", _PROXY_SECRET_REFERENCE)

    assert export_contract.main(["--shell-env", str(shell_env)]) == 0

    completed = subprocess.run(
        [
            "bash",
            "-euo",
            "pipefail",
            "-c",
            'set -a; . "$1"; set +a; env -0',
            "bash",
            str(shell_env),
        ],
        check=True,
        capture_output=True,
    )
    sourced = {
        key.decode("utf-8"): value.decode("utf-8")
        for row in completed.stdout.split(b"\0")
        if row and b"=" in row
        for key, value in [row.split(b"=", 1)]
    }
    contract = verified_gateway_runtime_resource_environment(sourced)
    assert contract["gateway_endpoint"] == DEFAULT_GATEWAY_ENDPOINT
    assert contract["supervisor_id"] == _SUPERVISOR_ID
    rows = shell_env.read_text(encoding="utf-8").splitlines()
    keys = [row.split("=", 1)[0] for row in rows]
    assert len(keys) == len(set(keys))
    assert "UNRELATED_VALUE=preserved" in rows


_DEPLOYMENT_LEASE_ID = "11111111-1111-4111-8111-111111111111"
_OTHER_DEPLOYMENT_LEASE_ID = "22222222-2222-4222-8222-222222222222"


def _health_response(
    *,
    git_sha: str = "abc123",
    binding: str = "binding-123",
    lease_id: str = _DEPLOYMENT_LEASE_ID,
) -> object:
    return SimpleNamespace(
        status_code=200,
        json=lambda: {
            "git_sha": git_sha,
            "agent_gateway_binding_sha256": binding,
            "deployment_lease_id": lease_id,
        },
    )


def _deployed_app_workspace(*lease_ids: str) -> object:
    deployment_id = "deployment-green"
    deployment = SimpleNamespace(
        deployment_id=deployment_id,
        env_vars=[
            SimpleNamespace(
                name="MIP_APP_DEPLOYMENT_LEASE_ID",
                value=lease_id,
                value_from=None,
            )
            for lease_id in lease_ids
        ],
    )

    class _Apps:
        def get(self, app_name: str) -> object:
            assert app_name == "mip-app"
            return SimpleNamespace(
                url="https://mip-app.example",
                active_deployment=SimpleNamespace(deployment_id=deployment_id),
            )

        def get_deployment(self, app_name: str, actual_deployment_id: str) -> object:
            assert app_name == "mip-app"
            assert actual_deployment_id == deployment_id
            return deployment

    return SimpleNamespace(apps=_Apps())


def _redacted_deployed_app_workspace() -> object:
    deployment_id = "deployment-green"
    deployment = SimpleNamespace(
        deployment_id=deployment_id,
        env_vars=[
            SimpleNamespace(
                name="MIP_APP_DEPLOYMENT_LEASE_ID",
                value=None,
                value_from=None,
            )
        ],
    )

    class _Apps:
        def get(self, app_name: str) -> object:
            assert app_name == "mip-app"
            return SimpleNamespace(
                url="https://mip-app.example",
                active_deployment=SimpleNamespace(deployment_id=deployment_id),
            )

        def get_deployment(self, app_name: str, actual_deployment_id: str) -> object:
            assert app_name == "mip-app"
            assert actual_deployment_id == deployment_id
            return deployment

    return SimpleNamespace(apps=_Apps())


def test_verify_deployed_contract_uses_authenticated_health() -> None:
    captured: dict[str, object] = {}

    client = SimpleNamespace(
        get=lambda url, *, headers: captured.update(url=url, headers=headers) or _health_response()
    )

    deployed_contract.verify(
        workspace=_deployed_app_workspace(_DEPLOYMENT_LEASE_ID),
        app_name="mip-app",
        base_url="https://mip-app.example/",
        bearer_token="short-lived-bearer",
        git_sha="abc123",
        gateway_binding_sha256="binding-123",
        expected_deployment_lease_id=_DEPLOYMENT_LEASE_ID,
        client=client,
    )

    assert captured["url"] == "https://mip-app.example/api/health"
    assert captured["headers"]["Authorization"] == "Bearer short-lived-bearer"


def test_verify_deployed_contract_validates_health_uuid_when_control_plane_redacts() -> None:
    deployed_contract.verify(
        workspace=_redacted_deployed_app_workspace(),
        app_name="mip-app",
        base_url="https://mip-app.example",
        bearer_token="short-lived-bearer",
        git_sha="abc123",
        gateway_binding_sha256="binding-123",
        client=SimpleNamespace(
            get=lambda *_args, **_kwargs: _health_response(lease_id=_DEPLOYMENT_LEASE_ID)
        ),
    )

    with pytest.raises(RuntimeError, match="valid UUID"):
        deployed_contract.verify(
            workspace=_redacted_deployed_app_workspace(),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="short-lived-bearer",
            git_sha="abc123",
            gateway_binding_sha256="binding-123",
            client=SimpleNamespace(
                get=lambda *_args, **_kwargs: _health_response(lease_id="attacker")
            ),
        )


def test_verify_deployed_contract_retries_transient_health_on_same_deployment(
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses = [
        SimpleNamespace(status_code=502),
        _health_response(),
    ]
    calls = 0
    now = [0.0]

    def get(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return responses.pop(0)

    deployed_contract.verify(
        workspace=_deployed_app_workspace(_DEPLOYMENT_LEASE_ID),
        app_name="mip-app",
        base_url="https://mip-app.example",
        bearer_token="short-lived-bearer",
        git_sha="abc123",
        gateway_binding_sha256="binding-123",
        expected_deployment_lease_id=_DEPLOYMENT_LEASE_ID,
        client=SimpleNamespace(get=get),
        health_timeout_s=10,
        health_interval_s=1,
        sleep=lambda delay: now.__setitem__(0, now[0] + delay),
        monotonic=lambda: now[0],
    )

    assert calls == 2
    assert now == [1.0]
    stderr = capsys.readouterr().err
    assert "HTTP 502" in stderr
    assert "short-lived-bearer" not in stderr


def test_verify_deployed_contract_rejects_deployment_drift_before_retry() -> None:
    lease_id = _DEPLOYMENT_LEASE_ID

    class _Apps:
        active_id = "deployment-green"

        def get(self, _app_name: str) -> object:
            return SimpleNamespace(
                url="https://mip-app.example",
                active_deployment=SimpleNamespace(deployment_id=self.active_id),
            )

        def get_deployment(self, _app_name: str, deployment_id: str) -> object:
            return SimpleNamespace(
                deployment_id=deployment_id,
                env_vars=[
                    SimpleNamespace(
                        name="MIP_APP_DEPLOYMENT_LEASE_ID",
                        value=lease_id,
                        value_from=None,
                    )
                ],
            )

    apps = _Apps()
    workspace = SimpleNamespace(apps=apps)
    sleeps: list[float] = []

    def get(*_args: object, **_kwargs: object) -> object:
        apps.active_id = "deployment-other"
        return SimpleNamespace(status_code=502)

    with pytest.raises(RuntimeError, match="changed during proof"):
        deployed_contract.verify(
            workspace=workspace,
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="short-lived-bearer",
            git_sha="abc123",
            gateway_binding_sha256="binding-123",
            expected_deployment_lease_id=lease_id,
            client=SimpleNamespace(get=get),
            health_timeout_s=10,
            health_interval_s=1,
            sleep=sleeps.append,
        )

    assert sleeps == []


def test_verify_deployed_contract_rejects_signed_deployment_mismatch_before_http() -> None:
    calls = 0

    def get(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return _health_response()

    with pytest.raises(RuntimeError, match="signed deployment contract"):
        deployed_contract.verify(
            workspace=_deployed_app_workspace(_DEPLOYMENT_LEASE_ID),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="short-lived-bearer",
            git_sha="abc123",
            gateway_binding_sha256="binding-123",
            expected_deployment_lease_id=_DEPLOYMENT_LEASE_ID,
            expected_deployment_id="deployment-signed-other",
            client=SimpleNamespace(get=get),
        )

    assert calls == 0


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (_health_response(git_sha="wrong"), "git SHA"),
        (_health_response(binding="wrong"), "Gateway binding"),
    ],
    ids=["wrong-sha", "wrong-binding"],
)
def test_verify_deployed_contract_rejects_health_mismatch(
    payload: object,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        deployed_contract.verify(
            workspace=_deployed_app_workspace(_DEPLOYMENT_LEASE_ID),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="short-lived-bearer",
            git_sha="abc123",
            gateway_binding_sha256="binding-123",
            client=SimpleNamespace(get=lambda *_args, **_kwargs: payload),
        )


def test_verify_deployed_contract_does_not_reflect_untrusted_health_fields() -> None:
    reflected_secret = "runtime-bearer-reflected-by-compromised-app"
    with pytest.raises(RuntimeError) as exc:
        deployed_contract.verify(
            workspace=_deployed_app_workspace(_DEPLOYMENT_LEASE_ID),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token=reflected_secret,
            git_sha="abc123",
            gateway_binding_sha256="binding-123",
            client=SimpleNamespace(
                get=lambda *_args, **_kwargs: _health_response(git_sha=reflected_secret)
            ),
        )

    assert reflected_secret not in str(exc.value)


def test_verify_deployed_contract_rejects_stale_health_from_same_sha_and_binding() -> None:
    with pytest.raises(RuntimeError, match="active Databricks App deployment"):
        deployed_contract.verify(
            workspace=_deployed_app_workspace(_DEPLOYMENT_LEASE_ID),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="short-lived-bearer",
            git_sha="abc123",
            gateway_binding_sha256="binding-123",
            expected_deployment_lease_id=_DEPLOYMENT_LEASE_ID,
            client=SimpleNamespace(
                get=lambda *_args, **_kwargs: _health_response(lease_id=_OTHER_DEPLOYMENT_LEASE_ID)
            ),
        )


@pytest.mark.parametrize(
    "active_lease_ids",
    [(), (_DEPLOYMENT_LEASE_ID, _DEPLOYMENT_LEASE_ID)],
    ids=["missing", "duplicate"],
)
def test_verify_deployed_contract_rejects_non_unique_active_lease_env(
    active_lease_ids: tuple[str, ...],
) -> None:
    with pytest.raises(RuntimeError, match="exactly one MIP_APP_DEPLOYMENT_LEASE_ID"):
        deployed_contract.verify(
            workspace=_deployed_app_workspace(*active_lease_ids),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="short-lived-bearer",
            git_sha="abc123",
            gateway_binding_sha256="binding-123",
            client=SimpleNamespace(get=lambda *_args, **_kwargs: _health_response()),
        )


def test_verify_deployed_contract_rejects_explicit_expected_lease_mismatch() -> None:
    calls = 0

    def get(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return _health_response()

    with pytest.raises(RuntimeError, match="expected deployment lease"):
        deployed_contract.verify(
            workspace=_deployed_app_workspace(_DEPLOYMENT_LEASE_ID),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="short-lived-bearer",
            git_sha="abc123",
            gateway_binding_sha256="binding-123",
            expected_deployment_lease_id=_OTHER_DEPLOYMENT_LEASE_ID,
            client=SimpleNamespace(get=get),
        )
    assert calls == 0


def test_verify_deployed_contract_cli_requires_token_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MIP_TEST_BEARER", raising=False)

    with pytest.raises(SystemExit) as exc:
        deployed_contract.main(
            [
                "--base-url",
                "https://mip-app.example",
                "--token-env",
                "MIP_TEST_BEARER",
                "--git-sha",
                "abc123",
                "--gateway-binding-sha256",
                "binding-123",
            ]
        )

    assert exc.value.code == 2


def test_verify_deployed_contract_cli_binds_signed_last_good(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = object()
    captured: dict[str, object] = {}
    git_sha = "a" * 40
    binding = "b" * 64
    monkeypatch.setenv("MIP_TEST_BEARER", "short-lived-bearer")
    monkeypatch.setattr(deployed_contract, "WorkspaceClient", lambda: workspace)
    monkeypatch.setattr(
        deployed_contract,
        "verified_signed_last_good_contract",
        lambda *_args, **_kwargs: SimpleNamespace(
            deployment_id="deployment-signed",
            deployment_lease_id=_DEPLOYMENT_LEASE_ID,
            git_sha=git_sha,
            gateway_binding_sha256=binding,
        ),
    )
    monkeypatch.setattr(
        deployed_contract,
        "verify",
        lambda **kwargs: captured.update(kwargs),
    )

    assert (
        deployed_contract.main(
            [
                "--base-url",
                "https://mip-app.example",
                "--token-env",
                "MIP_TEST_BEARER",
                "--git-sha",
                git_sha,
                "--gateway-binding-sha256",
                binding,
                "--rollback-scope",
                "mip-app-rollback",
            ]
        )
        == 0
    )
    assert captured["workspace"] is workspace
    assert captured["expected_deployment_id"] == "deployment-signed"
    assert captured["expected_deployment_lease_id"] == _DEPLOYMENT_LEASE_ID

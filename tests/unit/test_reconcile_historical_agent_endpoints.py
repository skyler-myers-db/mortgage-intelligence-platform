from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import NotFound

from backend.agents.gateway_contract import (
    DEFAULT_GATEWAY_ENDPOINT,
    GATEWAY_ENDPOINT_DESCRIPTION,
)
from backend.agents.supervisor_contract import (
    canonical_supervisor_contract_json,
    supervisor_contract_hash,
)
from tests.fixtures.gateway_runtime_resources import (
    TEST_GATEWAY_VERIFY_KEY,
    gateway_runtime_contract_for_scope,
    signed_gateway_model_tags,
    signed_gateway_runtime_environment,
)
from tools.databricks import historical_agent_endpoint_cleanup as cleanup
from tools.databricks import historical_gateway_attestation as legacy_attestation
from tools.databricks import historical_supervisor_creation_retirement as creation_retirement
from tools.databricks import reconcile_historical_agent_endpoints as inventory
from tools.databricks.serving_query_group_access import (
    managed_query_group_external_id,
    managed_query_group_name,
)
from tools.databricks.supervisor_agent_contract import supervisor_replacement_name

_RUNTIME = "runtime-client"
_CATALOG = "mip"
_GENIE = "genie-space"
_SUPERVISOR_NAME = "Mortgage Growth Agent"


@pytest.fixture(autouse=True)
def _trusted_model_attestation_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY",
        TEST_GATEWAY_VERIFY_KEY,
    )


def _supervisor(
    *,
    supervisor_id: str = "supervisor-1",
    display_name: str = _SUPERVISOR_NAME,
    endpoint: str = "supervisor-endpoint-1",
    creator: str = _RUNTIME,
) -> dict[str, str]:
    return {
        "supervisor_agent_id": supervisor_id,
        "display_name": display_name,
        "endpoint_name": endpoint,
        "creator": creator,
        "create_time": "2026-07-24T00:00:00Z",
    }


def _contract(
    *,
    gateway: str,
    gateway_id: str,
    supervisor: dict[str, str],
    supervisor_endpoint_id: str,
) -> dict[str, str]:
    contract = gateway_runtime_contract_for_scope(
        catalog=_CATALOG,
        genie_space_id=_GENIE,
        runtime_application_id=_RUNTIME,
        supervisor_id=supervisor["supervisor_agent_id"],
        supervisor_endpoint=supervisor["endpoint_name"],
        gateway_endpoint=gateway,
        gateway_model_name=f"{_CATALOG}.audit.gateway_model_deadbeef0000",
        gateway_model_version="7",
        gateway_model_source="models:/gateway-model-source",
        gateway_experiment_name="/Users/runtime-client/gateway-deadbeef0000",
        gateway_experiment_id="experiment-7",
        gateway_inference_table=f"{_CATALOG}.audit.gateway_table_deadbeef0000",
    )
    supervisor_json = canonical_supervisor_contract_json(
        genie_space_id=_GENIE,
        catalog=_CATALOG,
    )
    return {
        **contract,
        "gateway_endpoint_id": gateway_id,
        "gateway_endpoint_description": GATEWAY_ENDPOINT_DESCRIPTION,
        "gateway_inference_table_family": f"{_CATALOG}.audit.gateway_table",
        "gateway_model_family": f"{_CATALOG}.audit.gateway_model",
        "supervisor_canonical_name": _SUPERVISOR_NAME,
        "supervisor_display_name": supervisor["display_name"],
        "supervisor_endpoint_id": supervisor_endpoint_id,
        "supervisor_creator": supervisor["creator"],
        "supervisor_endpoint_creator": supervisor["creator"],
        "supervisor_contract_json": supervisor_json,
        "supervisor_contract_sha256": __import__("hashlib")
        .sha256(supervisor_json.encode())
        .hexdigest(),
    }


def _gateway_details(contract: dict[str, str]) -> SimpleNamespace:
    environment = signed_gateway_runtime_environment(contract)
    return SimpleNamespace(
        id=contract["gateway_endpoint_id"],
        creator=contract["gateway_endpoint_creator"],
        pending_config=None,
        config=SimpleNamespace(served_entities=[SimpleNamespace(environment_vars=environment)]),
    )


class _ServingEndpoints:
    def __init__(self, details: dict[str, Any]) -> None:
        self.details = details
        self.deleted: list[str] = []
        self.permissions: dict[str, Any] = {}

    def list(self) -> list[dict[str, str]]:
        return [{"name": name} for name in self.details]

    def get(self, name: str) -> Any:
        if name not in self.details:
            raise NotFound("missing")
        return self.details[name]

    def delete(self, name: str) -> None:
        self.deleted.append(name)
        del self.details[name]

    def get_permissions(self, endpoint_id: str) -> Any:
        return self.permissions.get(
            endpoint_id,
            SimpleNamespace(access_control_list=[]),
        )


class _ApiClient:
    def __init__(self, supervisors: list[dict[str, str]], serving: _ServingEndpoints) -> None:
        self.supervisors = {row["supervisor_agent_id"]: dict(row) for row in supervisors}
        self.tools = {row["supervisor_agent_id"]: [] for row in supervisors}
        self.serving = serving
        self.deleted: list[str] = []
        self.delete_endpoint_with_agent = True

    def do(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
    ) -> Any:
        del query
        if method == "GET" and path == "/api/2.1/supervisor-agents":
            return {"supervisor_agents": list(self.supervisors.values())}
        if method == "GET" and path.endswith("/tools"):
            return {"tools": list(self.tools[path.split("/")[-2]])}
        if method == "GET" and path.endswith("/examples"):
            return {"examples": []}
        supervisor_id = path.rsplit("/", 1)[-1]
        if method == "GET":
            if supervisor_id not in self.supervisors:
                raise NotFound("missing")
            return dict(self.supervisors[supervisor_id])
        assert method == "DELETE"
        self.deleted.append(supervisor_id)
        endpoint = self.supervisors.pop(supervisor_id)["endpoint_name"]
        if self.delete_endpoint_with_agent:
            self.serving.details.pop(endpoint)
        return {}


class _Groups:
    def __init__(self) -> None:
        self.details: dict[str, Any] = {}
        self.deleted: list[str] = []

    def list(self, *, filter: str | None = None) -> list[Any]:
        values = list(self.details.values())
        if filter is None:
            return values
        expected = filter.partition("'")[2].rpartition("'")[0]
        return [
            group for group in values if str(getattr(group, "display_name", "") or "") == expected
        ]

    def get(self, group_id: str) -> Any:
        if group_id not in self.details:
            raise NotFound("missing")
        return self.details[group_id]

    def delete(self, group_id: str) -> None:
        self.deleted.append(group_id)
        del self.details[group_id]


class _Client:
    def __init__(self, details: dict[str, Any], supervisors: list[dict[str, str]]) -> None:
        self.serving_endpoints = _ServingEndpoints(details)
        self.api_client = _ApiClient(supervisors, self.serving_endpoints)
        self.groups = _Groups()


class _MemoryCleanupJournal:
    def __init__(self) -> None:
        self.pending: inventory.SupervisorCleanupProof | None = None
        self.cleared: list[inventory.SupervisorCleanupProof] = []

    def read(self) -> inventory.SupervisorCleanupProof | None:
        return self.pending

    def proof_for(
        self,
        supervisor: inventory.ReviewedSupervisor,
        *,
        runtime_application_id: str,
    ) -> inventory.SupervisorCleanupProof:
        return inventory.SupervisorCleanupProof(
            app_name="mip-app",
            lease_id="lease-id",
            source_git_sha="a" * 40,
            runtime_application_id=runtime_application_id,
            supervisor_id=supervisor.supervisor_id,
            endpoint=supervisor.endpoint,
            endpoint_id=supervisor.endpoint_id,
            creator=supervisor.creator,
        )

    def stage(self, proof: inventory.SupervisorCleanupProof) -> None:
        if self.pending not in (None, proof):
            raise RuntimeError("journal conflict")
        self.pending = proof

    def clear(
        self,
        proof: inventory.SupervisorCleanupProof,
        *,
        assert_resources_absent: Callable[[], None],
    ) -> None:
        assert_resources_absent()
        assert self.pending == proof
        self.pending = None
        self.cleared.append(proof)


def _cleanup_proof(
    supervisor: dict[str, str],
    *,
    endpoint_id: str,
    lease_id: str = "original-lease",
    source_git_sha: str = "a" * 40,
) -> inventory.SupervisorCleanupProof:
    return inventory.SupervisorCleanupProof(
        app_name="mip-app",
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        runtime_application_id=_RUNTIME,
        supervisor_id=supervisor["supervisor_agent_id"],
        endpoint=supervisor["endpoint_name"],
        endpoint_id=endpoint_id,
        creator=supervisor["creator"],
    )


def _inventory(
    client: _Client,
    *,
    gateway_pins: tuple[inventory.GatewayPin, ...] = (),
    supervisor_pins: tuple[inventory.SupervisorPin, ...] = (),
    pending_cleanup: inventory.SupervisorCleanupProof | None = None,
    pending_creation: dict[str, Any] | None = None,
    contracts: list[dict[str, Any]] | None = None,
    supervisor_name: str = _SUPERVISOR_NAME,
) -> inventory.RuntimeEndpointInventory:
    return inventory.inventory_runtime_endpoints(
        client,
        runtime_application_id=_RUNTIME,
        gateway_prefixes=(DEFAULT_GATEWAY_ENDPOINT,),
        supervisor_name=supervisor_name,
        catalog=_CATALOG,
        genie_space_id=_GENIE,
        gateway_pins=gateway_pins,
        supervisor_pins=supervisor_pins,
        pending_supervisor_cleanup=pending_cleanup,
        pending_supervisor_creation=pending_creation,
        assert_single_writer=lambda: None,
        assert_supervisor_contract=lambda _supervisor_id, **kwargs: (
            contracts.append(kwargs["expected_contract"]) if contracts is not None else None
        ),
    )


def test_inventory_includes_configured_supervisor_replacement_family() -> None:
    supervisor_name = "Configured Mortgage Growth Agent"
    supervisor = _supervisor(
        display_name=supervisor_replacement_name(
            supervisor_name,
            genie_space_id=_GENIE,
            catalog=_CATALOG,
        )
    )
    endpoint_id = "configured-supervisor-endpoint-id"
    client = _Client(
        {
            supervisor["endpoint_name"]: SimpleNamespace(
                id=endpoint_id,
                creator=_RUNTIME,
            )
        },
        [supervisor],
    )

    result = _inventory(client, supervisor_name=supervisor_name)

    assert [(row.supervisor_id, row.endpoint) for row in result.supervisors] == [
        (supervisor["supervisor_agent_id"], supervisor["endpoint_name"])
    ]


def test_cleanup_postflight_allows_only_claimed_pending_creation() -> None:
    empty = inventory.RuntimeEndpointInventory(
        version=1,
        runtime_application_id=_RUNTIME,
        gateways=(),
        supervisors=(),
        pending_supervisor_cleanup=None,
        pending_supervisor_creation=None,
    )
    assert inventory.cleanup_postflight_is_complete(empty)
    assert inventory.cleanup_postflight_is_complete(
        inventory.RuntimeEndpointInventory(
            **{
                **empty.__dict__,
                "pending_supervisor_creation": {
                    "supervisor_id": "claimed-supervisor",
                },
            }
        )
    )
    assert not inventory.cleanup_postflight_is_complete(
        inventory.RuntimeEndpointInventory(
            **{
                **empty.__dict__,
                "pending_supervisor_creation": {
                    "supervisor_id": "",
                },
            }
        )
    )


def test_inventory_routes_retire_only_partial_creation_to_exact_cleanup() -> None:
    contract_json = canonical_supervisor_contract_json(
        genie_space_id=_GENIE,
        catalog=_CATALOG,
    )
    contract = json.loads(contract_json)
    marker = "[mip-supervisor-create:11111111-1111-4111-8111-111111111111]"
    temporary_name = f"Historical Agent {marker}"
    supervisor = {
        **_supervisor(
            supervisor_id="retire-only-supervisor",
            display_name=temporary_name,
            endpoint="retire-only-endpoint",
        ),
        "description": contract["description"],
        "instructions": f"{contract['instructions']} {marker}",
    }
    endpoint_id = "retire-only-endpoint-id"
    pending = {
        "disposition": "retire_only",
        "canonical_name": "Historical Agent",
        "target_name": "Historical Agent",
        "temporary_name": temporary_name,
        "temporary_instructions": supervisor["instructions"],
        "genie_space_id": _GENIE,
        "catalog": _CATALOG,
        "contract_json": contract_json,
        "contract_sha256": __import__("hashlib").sha256(contract_json.encode()).hexdigest(),
        "runtime_application_id": _RUNTIME,
        "supervisor_id": supervisor["supervisor_agent_id"],
        "endpoint": supervisor["endpoint_name"],
        "endpoint_id": endpoint_id,
        "creator": _RUNTIME,
        "create_time": supervisor["create_time"],
    }
    client = _Client(
        {
            supervisor["endpoint_name"]: SimpleNamespace(
                id=endpoint_id,
                creator=_RUNTIME,
            )
        },
        [supervisor],
    )

    result = _inventory(client, pending_creation=pending)

    assert [(item.supervisor_id, item.preserved) for item in result.supervisors] == [
        ("retire-only-supervisor", False)
    ]
    assert result.pending_supervisor_creation == pending
    assert not inventory.cleanup_postflight_is_complete(result)

    client.api_client.supervisors["retire-only-supervisor"].update(
        display_name=pending["target_name"],
        instructions=contract["instructions"],
    )
    with pytest.raises(RuntimeError, match="incomplete contract"):
        _inventory(client, pending_creation=pending)


def test_inventory_emits_every_attested_historical_gateway_and_supervisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = _supervisor()
    supervisor_endpoint_id = "supervisor-endpoint-id"
    blue_name = DEFAULT_GATEWAY_ENDPOINT
    old_name = f"{DEFAULT_GATEWAY_ENDPOINT}-deadbeef0000-mq1"
    blue = _contract(
        gateway=blue_name,
        gateway_id="gateway-blue-id",
        supervisor=supervisor,
        supervisor_endpoint_id=supervisor_endpoint_id,
    )
    old = _contract(
        gateway=old_name,
        gateway_id="gateway-old-id",
        supervisor=supervisor,
        supervisor_endpoint_id=supervisor_endpoint_id,
    )
    client = _Client(
        {
            blue_name: _gateway_details(blue),
            old_name: _gateway_details(old),
            supervisor["endpoint_name"]: SimpleNamespace(
                id=supervisor_endpoint_id,
                creator=_RUNTIME,
            ),
        },
        [supervisor],
    )
    monkeypatch.setattr(
        inventory,
        "gateway_endpoint_configuration_matches",
        lambda _details, _deployment: True,
    )
    monkeypatch.setattr(
        inventory,
        "verify_gateway_responses_agent",
        lambda *_args, **_kwargs: None,
    )
    gateway_pin = inventory.GatewayPin(blue_name, "gateway-blue-id", _RUNTIME)
    supervisor_pin = inventory.SupervisorPin(
        supervisor_id=supervisor["supervisor_agent_id"],
        endpoint=supervisor["endpoint_name"],
        endpoint_id=supervisor_endpoint_id,
        creator=_RUNTIME,
    )
    verified_contracts: list[dict[str, Any]] = []

    result = _inventory(
        client,
        gateway_pins=(gateway_pin,),
        supervisor_pins=(supervisor_pin,),
        contracts=verified_contracts,
    )

    assert [(row.name, row.preserved) for row in result.gateways] == [
        (blue_name, True),
        (old_name, False),
    ]
    assert [(row.endpoint, row.preserved) for row in result.supervisors] == [
        (supervisor["endpoint_name"], True)
    ]
    assert verified_contracts == [
        json.loads(
            canonical_supervisor_contract_json(
                genie_space_id=_GENIE,
                catalog=_CATALOG,
            )
        )
    ]
    assert {row["name"] for row in result.document()["reviewed_serving_endpoints"]} == {
        blue_name,
        old_name,
        supervisor["endpoint_name"],
    }


def test_inventory_ignores_human_owned_family_names_but_rejects_unsigned_runtime_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = f"{DEFAULT_GATEWAY_ENDPOINT}-deadbeef0000"
    client = _Client(
        {
            DEFAULT_GATEWAY_ENDPOINT: SimpleNamespace(
                id="human-gateway-id",
                creator="human@example.com",
            ),
            candidate: SimpleNamespace(
                id="runtime-gateway-id",
                creator=_RUNTIME,
                config=SimpleNamespace(served_entities=[SimpleNamespace(environment_vars={})]),
                pending_config=None,
            ),
        },
        [],
    )

    with pytest.raises(RuntimeError, match="legacy Gateway immutable endpoint contract"):
        _inventory(client)

    del client.serving_endpoints.details[candidate]
    result = _inventory(client)
    assert result.gateways == ()
    assert result.supervisors == ()
    assert result.document()["reviewed_serving_endpoints"] == []


def test_partial_modern_resource_envelope_never_falls_back_to_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = f"{DEFAULT_GATEWAY_ENDPOINT}-deadbeef0000"
    details = SimpleNamespace(
        id="runtime-gateway-id",
        creator=_RUNTIME,
        config=SimpleNamespace(
            served_entities=[
                SimpleNamespace(
                    environment_vars={
                        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON": "{}",
                        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY": (TEST_GATEWAY_VERIFY_KEY),
                    }
                )
            ]
        ),
        pending_config=None,
    )
    legacy_calls: list[str] = []
    monkeypatch.setattr(
        inventory,
        "attest_legacy_gateway",
        lambda *_args, **_kwargs: legacy_calls.append("legacy"),
    )

    with pytest.raises(RuntimeError, match="invalid runtime-resource proof"):
        inventory._live_gateway_contract(
            _Client({candidate: details}, []),
            details,
            name=candidate,
            gateway_prefixes=(DEFAULT_GATEWAY_ENDPOINT,),
            runtime_application_id=_RUNTIME,
            supervisor_name=_SUPERVISOR_NAME,
            catalog=_CATALOG,
            genie_space_id=_GENIE,
            assert_single_writer=lambda: None,
        )

    assert legacy_calls == []


def test_legacy_attestation_requires_hash_derived_name_and_signed_model_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource_hash = "14609944fa02" + "0" * 52
    endpoint = f"{DEFAULT_GATEWAY_ENDPOINT}-{resource_hash[:12]}"
    supervisor_endpoint = "historical-supervisor-endpoint"
    supervisor_endpoint_id = "historical-supervisor-endpoint-id"
    model_family = f"{_CATALOG}.audit.gateway_model"
    model_name = f"{model_family}_{resource_hash[:12]}"
    model_source = "models:/m-signed-historical-source"
    model_contract = {
        "full_name": model_name,
        "model_source": model_source,
        "source_hash": "3" * 64,
        "supervisor_id": "historical-supervisor",
        "supervisor_endpoint_id": supervisor_endpoint_id,
        "upstream_endpoint": supervisor_endpoint,
        "runtime_application_id": _RUNTIME,
        "model_family": model_family,
        "experiment_base": "gateway-experiment",
        "catalog": _CATALOG,
        "genie_space_id": _GENIE,
        "inference_schema": "audit",
        "inference_table_prefix": "gateway_table",
    }
    tags = signed_gateway_model_tags(model_contract)
    environment = {
        "MIP_UPSTREAM_SUPERVISOR_ID": "historical-supervisor",
        "MIP_UPSTREAM_SUPERVISOR_ENDPOINT": supervisor_endpoint,
        "MIP_UPSTREAM_SUPERVISOR_CREATOR": _RUNTIME,
        "MIP_UPSTREAM_PROXY_CLIENT_ID": "proxy-client",
        "MIP_UPSTREAM_PROXY_CREDENTIAL_ID": "proxy-credential",
        "MIP_UPSTREAM_PROXY_CLIENT_SECRET": (
            "{{secrets/mip-agent-proxy/oauth-client-secret-proxy-credential}}"
        ),
        "MIP_SUPERVISOR_CATALOG": _CATALOG,
        "MIP_SUPERVISOR_GENIE_SPACE_ID": _GENIE,
        "MIP_SUPERVISOR_CONTRACT_SHA256": supervisor_contract_hash(
            genie_space_id=_GENIE,
            catalog=_CATALOG,
        ),
        "MLFLOW_EXPERIMENT_ID": "experiment-id",
    }
    details = SimpleNamespace(
        id="historical-gateway-id",
        creator=_RUNTIME,
        pending_config=None,
        description=GATEWAY_ENDPOINT_DESCRIPTION,
        task="agent/v1/responses",
        config=SimpleNamespace(
            served_entities=[
                SimpleNamespace(
                    entity_name=model_name,
                    entity_version="7",
                    environment_vars=environment,
                )
            ]
        ),
    )
    workspace = SimpleNamespace(
        api_client=SimpleNamespace(
            do=lambda _method, _path: {
                "supervisor_agent_id": "historical-supervisor",
                "display_name": _SUPERVISOR_NAME,
                "endpoint_name": supervisor_endpoint,
                "creator": _RUNTIME,
            }
        ),
        serving_endpoints=SimpleNamespace(
            get=lambda name: (
                SimpleNamespace(id=supervisor_endpoint_id, creator=_RUNTIME)
                if name == supervisor_endpoint
                else details
            )
        ),
        registered_models=SimpleNamespace(get=lambda _name: SimpleNamespace(owner=_RUNTIME)),
    )
    model_registry = SimpleNamespace(
        get_model_version=lambda _name, _version: SimpleNamespace(
            name=model_name,
            version="7",
            source=model_source,
            tags=tags,
        )
    )
    tracking = SimpleNamespace(
        get_experiment=lambda _experiment_id: SimpleNamespace(tags={"mlflow.ownerEmail": _RUNTIME})
    )
    monkeypatch.setattr(
        legacy_attestation,
        "gateway_resource_hash",
        lambda **_kwargs: resource_hash,
    )
    monkeypatch.setattr(
        legacy_attestation,
        "verify_gateway_responses_agent",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        legacy_attestation,
        "resolve_exact_experiment_acl",
        lambda *_args, **_kwargs: SimpleNamespace(
            canonical_json='{"runtime":"CAN_MANAGE"}',
            sha256="a" * 64,
        ),
    )

    result = legacy_attestation.attest_legacy_gateway(
        workspace,
        details,
        endpoint_name=endpoint,
        endpoint_prefixes=(DEFAULT_GATEWAY_ENDPOINT,),
        runtime_application_id=_RUNTIME,
        supervisor_name=_SUPERVISOR_NAME,
        catalog=_CATALOG,
        genie_space_id=_GENIE,
        assert_single_writer=lambda: None,
        model_registry=model_registry,
        tracking_client=tracking,
    )

    assert result["gateway_endpoint"] == endpoint
    assert result["gateway_resource_hash"] == resource_hash
    with pytest.raises(RuntimeError, match="deterministic resource name"):
        legacy_attestation.attest_legacy_gateway(
            workspace,
            details,
            endpoint_name=f"{DEFAULT_GATEWAY_ENDPOINT}-ffffffffffff",
            endpoint_prefixes=(DEFAULT_GATEWAY_ENDPOINT,),
            runtime_application_id=_RUNTIME,
            supervisor_name=_SUPERVISOR_NAME,
            catalog=_CATALOG,
            genie_space_id=_GENIE,
            assert_single_writer=lambda: None,
            model_registry=model_registry,
            tracking_client=tracking,
        )


def test_inventory_rejects_preserved_tuple_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = _supervisor()
    contract = _contract(
        gateway=DEFAULT_GATEWAY_ENDPOINT,
        gateway_id="actual-id",
        supervisor=supervisor,
        supervisor_endpoint_id="supervisor-endpoint-id",
    )
    client = _Client(
        {
            DEFAULT_GATEWAY_ENDPOINT: _gateway_details(contract),
            supervisor["endpoint_name"]: SimpleNamespace(
                id="supervisor-endpoint-id",
                creator=_RUNTIME,
            ),
        },
        [supervisor],
    )
    monkeypatch.setattr(
        inventory,
        "gateway_endpoint_configuration_matches",
        lambda _details, _deployment: True,
    )
    monkeypatch.setattr(
        inventory,
        "verify_gateway_responses_agent",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="preserved Gateway tuple"):
        _inventory(
            client,
            gateway_pins=(
                inventory.GatewayPin(
                    DEFAULT_GATEWAY_ENDPOINT,
                    "different-id",
                    _RUNTIME,
                ),
            ),
        )


def test_inventory_tolerates_absent_exact_pins_after_partial_retirement() -> None:
    client = _Client({}, [])

    result = _inventory(
        client,
        gateway_pins=(
            inventory.GatewayPin(
                DEFAULT_GATEWAY_ENDPOINT,
                "retired-gateway-id",
                _RUNTIME,
            ),
        ),
        supervisor_pins=(
            inventory.SupervisorPin(
                "retired-supervisor-id",
                "retired-supervisor-endpoint",
                "retired-supervisor-endpoint-id",
                _RUNTIME,
            ),
        ),
    )

    assert result.gateways == ()
    assert result.supervisors == ()


def test_inventory_preserves_live_supervisor_after_gateway_retirement() -> None:
    supervisor = _supervisor()
    supervisor_endpoint_id = "retained-supervisor-endpoint-id"
    supervisor_pin = inventory.SupervisorPin(
        supervisor["supervisor_agent_id"],
        supervisor["endpoint_name"],
        supervisor_endpoint_id,
        _RUNTIME,
    )
    client = _Client(
        {
            supervisor["endpoint_name"]: SimpleNamespace(
                id=supervisor_endpoint_id,
                creator=_RUNTIME,
            ),
        },
        [supervisor],
    )

    result = _inventory(
        client,
        gateway_pins=(
            inventory.GatewayPin(
                DEFAULT_GATEWAY_ENDPOINT,
                "retired-gateway-id",
                _RUNTIME,
            ),
        ),
        supervisor_pins=(supervisor_pin,),
    )

    assert result.gateways == ()
    assert tuple(
        (row.supervisor_id, row.endpoint, row.endpoint_id, row.creator, row.preserved)
        for row in result.supervisors
    ) == (
        (
            supervisor_pin.supervisor_id,
            supervisor_pin.endpoint,
            supervisor_pin.endpoint_id,
            supervisor_pin.creator,
            True,
        ),
    )


def test_pending_cleanup_proof_cannot_override_preserved_supervisor_pin() -> None:
    supervisor = _supervisor()
    endpoint_id = "preserved-supervisor-endpoint-id"
    pin = inventory.SupervisorPin(
        supervisor["supervisor_agent_id"],
        supervisor["endpoint_name"],
        endpoint_id,
        _RUNTIME,
    )
    client = _Client(
        {
            supervisor["endpoint_name"]: SimpleNamespace(
                id=endpoint_id,
                creator=_RUNTIME,
            ),
        },
        [supervisor],
    )

    with pytest.raises(RuntimeError, match="conflicts with a preserved tuple"):
        _inventory(
            client,
            supervisor_pins=(pin,),
            pending_cleanup=_cleanup_proof(supervisor, endpoint_id=endpoint_id),
        )

    assert client.api_client.deleted == []
    assert client.serving_endpoints.deleted == []


def test_inventory_rejects_preserved_gateway_immutable_id_reuse() -> None:
    client = _Client(
        {
            "renamed-serving-endpoint": SimpleNamespace(
                id="signed-gateway-id",
                creator=_RUNTIME,
            ),
        },
        [],
    )

    with pytest.raises(RuntimeError, match="preserved Gateway tuple"):
        _inventory(
            client,
            gateway_pins=(
                inventory.GatewayPin(
                    DEFAULT_GATEWAY_ENDPOINT,
                    "signed-gateway-id",
                    _RUNTIME,
                ),
            ),
        )


def test_inventory_rejects_foreign_reuse_of_preserved_gateway_name() -> None:
    client = _Client(
        {
            DEFAULT_GATEWAY_ENDPOINT: SimpleNamespace(
                id="replacement-id",
                creator="foreign-owner",
            ),
        },
        [],
    )

    with pytest.raises(RuntimeError, match="preserved Gateway tuple"):
        _inventory(
            client,
            gateway_pins=(
                inventory.GatewayPin(
                    DEFAULT_GATEWAY_ENDPOINT,
                    "signed-gateway-id",
                    _RUNTIME,
                ),
            ),
        )


@pytest.mark.parametrize(
    ("signed_pin", "live_supervisor"),
    [
        (
            inventory.SupervisorPin(
                "supervisor-id",
                "signed-endpoint",
                "signed-endpoint-id",
                _RUNTIME,
            ),
            _supervisor(supervisor_id="supervisor-id", endpoint="replacement-endpoint"),
        ),
        (
            inventory.SupervisorPin(
                "retired-supervisor-id",
                "retired-endpoint",
                "shared-endpoint-id",
                _RUNTIME,
            ),
            _supervisor(
                supervisor_id="replacement-supervisor-id",
                endpoint="replacement-endpoint",
            ),
        ),
    ],
)
def test_inventory_rejects_preserved_supervisor_agent_or_endpoint_id_reuse(
    signed_pin: inventory.SupervisorPin,
    live_supervisor: dict[str, str],
) -> None:
    endpoint_id = (
        "shared-endpoint-id"
        if signed_pin.endpoint_id == "shared-endpoint-id"
        else "replacement-endpoint-id"
    )
    client = _Client(
        {
            live_supervisor["endpoint_name"]: SimpleNamespace(
                id=endpoint_id,
                creator=_RUNTIME,
            ),
        },
        [live_supervisor],
    )

    with pytest.raises(RuntimeError, match="preserved Supervisor tuple"):
        _inventory(client, supervisor_pins=(signed_pin,))


def test_inventory_rejects_preserved_gateway_without_signed_upstream_supervisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = _supervisor()
    contract = _contract(
        gateway=DEFAULT_GATEWAY_ENDPOINT,
        gateway_id="gateway-id",
        supervisor=supervisor,
        supervisor_endpoint_id="supervisor-endpoint-id",
    )
    client = _Client(
        {
            DEFAULT_GATEWAY_ENDPOINT: _gateway_details(contract),
            supervisor["endpoint_name"]: SimpleNamespace(
                id="supervisor-endpoint-id",
                creator=_RUNTIME,
            ),
        },
        [supervisor],
    )
    monkeypatch.setattr(
        inventory,
        "gateway_endpoint_configuration_matches",
        lambda _details, _deployment: True,
    )
    monkeypatch.setattr(
        inventory,
        "verify_gateway_responses_agent",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="signed upstream Supervisor"):
        _inventory(
            client,
            gateway_pins=(
                inventory.GatewayPin(
                    DEFAULT_GATEWAY_ENDPOINT,
                    "gateway-id",
                    _RUNTIME,
                ),
            ),
        )


def test_cleanup_scim_resolution_binds_expected_immutable_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        creation_retirement,
        "exact_service_principal_scim_id",
        lambda _workspace, *, application_id: f"scim-{application_id}",
    )

    assert (
        creation_retirement.resolved_scim_id(
            object(),
            application_id="proxy-client",
            expected_scim_id="scim-proxy-client",
        )
        == "scim-proxy-client"
    )
    with pytest.raises(RuntimeError, match="SCIM identity drifted"):
        creation_retirement.resolved_scim_id(
            object(),
            application_id="proxy-client",
            expected_scim_id="different-scim",
        )


def test_inventory_allows_signed_replacement_to_be_renamed_canonical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _supervisor()
    signed = {
        **current,
        "display_name": (f"{_SUPERVISOR_NAME} [mip-agent-runtime-deadbeef0000]-mq1"),
    }
    contract = _contract(
        gateway=DEFAULT_GATEWAY_ENDPOINT,
        gateway_id="gateway-id",
        supervisor=signed,
        supervisor_endpoint_id="supervisor-endpoint-id",
    )
    client = _Client(
        {
            DEFAULT_GATEWAY_ENDPOINT: _gateway_details(contract),
            current["endpoint_name"]: SimpleNamespace(
                id="supervisor-endpoint-id",
                creator=_RUNTIME,
            ),
        },
        [current],
    )
    monkeypatch.setattr(
        inventory,
        "gateway_endpoint_configuration_matches",
        lambda _details, _deployment: True,
    )
    monkeypatch.setattr(
        inventory,
        "verify_gateway_responses_agent",
        lambda *_args, **_kwargs: None,
    )

    result = _inventory(client)

    assert result.supervisors[0].display_name == _SUPERVISOR_NAME


def test_cleanup_deletes_only_unpreserved_exact_resources_and_rechecks_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = _supervisor(
        supervisor_id="old-supervisor",
        display_name=f"{_SUPERVISOR_NAME} [mip-agent-runtime-deadbeef0000]",
        endpoint="old-supervisor-endpoint",
    )
    gateway_name = f"{DEFAULT_GATEWAY_ENDPOINT}-deadbeef0000"
    contract = _contract(
        gateway=gateway_name,
        gateway_id="old-gateway-id",
        supervisor=supervisor,
        supervisor_endpoint_id="old-supervisor-endpoint-id",
    )
    client = _Client(
        {
            gateway_name: _gateway_details(contract),
            supervisor["endpoint_name"]: SimpleNamespace(
                id="old-supervisor-endpoint-id",
                creator=_RUNTIME,
            ),
        },
        [supervisor],
    )
    monkeypatch.setattr(
        inventory,
        "gateway_endpoint_configuration_matches",
        lambda _details, _deployment: True,
    )
    monkeypatch.setattr(
        inventory,
        "verify_gateway_responses_agent",
        lambda *_args, **_kwargs: None,
    )
    initial = _inventory(client)
    empty = inventory.RuntimeEndpointInventory(1, _RUNTIME, (), ())
    after_gateway = inventory.RuntimeEndpointInventory(
        1,
        _RUNTIME,
        (),
        initial.supervisors,
    )
    reads = iter((initial, initial, after_gateway, empty))
    lease_checks: list[str] = []

    final = inventory.cleanup_runtime_endpoints(
        client,
        initial,
        assert_single_writer=lambda: lease_checks.append("lease"),
        query_principals=inventory.QueryGroupPrincipals(
            "app-client",
            "app-scim",
            "verifier-client",
            "verifier-scim",
            "proxy-client",
            "proxy-scim",
        ),
        timeout_s=1,
        sleep=lambda _seconds: None,
        inventory_again=lambda: next(reads),
        cleanup_journal=_MemoryCleanupJournal(),
    )

    assert final == empty
    assert client.serving_endpoints.deleted == [gateway_name]
    assert client.api_client.deleted == ["old-supervisor"]
    assert lease_checks == ["lease", "lease"]


def test_cleanup_aborts_when_inventory_changes_before_first_mutation() -> None:
    empty = inventory.RuntimeEndpointInventory(1, _RUNTIME, (), ())
    changed = inventory.RuntimeEndpointInventory(1, "other-runtime", (), ())
    client = _Client({}, [])

    with pytest.raises(RuntimeError, match="changed before cleanup"):
        inventory.cleanup_runtime_endpoints(
            client,
            empty,
            assert_single_writer=lambda: None,
            query_principals=inventory.QueryGroupPrincipals(
                "app-client",
                "app-scim",
                "verifier-client",
                "verifier-scim",
                "proxy-client",
                "proxy-scim",
            ),
            timeout_s=1,
            inventory_again=lambda: changed,
            cleanup_journal=_MemoryCleanupJournal(),
        )

    assert client.serving_endpoints.deleted == []
    assert client.api_client.deleted == []


def _query_permission(group_name: str) -> SimpleNamespace:
    return SimpleNamespace(
        group_name=group_name,
        all_permissions=[
            SimpleNamespace(permission_level="CAN_QUERY", inherited=False),
        ],
    )


def test_cleanup_retires_exact_group_before_its_endpoint() -> None:
    endpoint_name = "historical-endpoint"
    endpoint_id = "historical-endpoint-id"
    creator = _RUNTIME
    client = _Client(
        {
            endpoint_name: SimpleNamespace(
                id=endpoint_id,
                creator=creator,
            ),
        },
        [],
    )
    application_id = "app-client"
    scim_id = "app-scim"
    group_id = "exact-group-id"
    group_name = managed_query_group_name(
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    client.groups.details[group_id] = SimpleNamespace(
        id=group_id,
        display_name=group_name,
        external_id=managed_query_group_external_id(
            endpoint_id=endpoint_id,
            application_id=application_id,
        ),
        members=[SimpleNamespace(value=scim_id)],
    )
    client.serving_endpoints.permissions[endpoint_id] = SimpleNamespace(
        access_control_list=[_query_permission(group_name)]
    )
    lease_checks: list[str] = []

    cleanup._retire_live_endpoint_query_groups(
        client,
        endpoint_name=endpoint_name,
        endpoint_id=endpoint_id,
        endpoint_creator=creator,
        principals=(
            (application_id, scim_id),
            ("verifier-client", "verifier-scim"),
        ),
        assert_single_writer=lambda: lease_checks.append("lease"),
    )

    assert client.groups.deleted == [group_id]
    assert client.serving_endpoints.get(endpoint_name).id == endpoint_id
    assert lease_checks == ["lease"]

    # A process can die here. Retry observes the exact endpoint and absent group,
    # performs no hash/prefix sweep, and remains idempotent.
    cleanup._retire_live_endpoint_query_groups(
        client,
        endpoint_name=endpoint_name,
        endpoint_id=endpoint_id,
        endpoint_creator=creator,
        principals=((application_id, scim_id),),
        assert_single_writer=lambda: lease_checks.append("unexpected"),
    )
    assert client.groups.deleted == [group_id]
    assert lease_checks == ["lease"]


def test_cleanup_refuses_unattached_colliding_group_without_mutation() -> None:
    endpoint_name = "historical-endpoint"
    endpoint_id = "historical-endpoint-id"
    application_id = "app-client"
    scim_id = "app-scim"
    group_id = "foreign-group-id"
    client = _Client(
        {
            endpoint_name: SimpleNamespace(
                id=endpoint_id,
                creator=_RUNTIME,
            ),
        },
        [],
    )
    client.groups.details[group_id] = SimpleNamespace(
        id=group_id,
        display_name=managed_query_group_name(
            endpoint_id=endpoint_id,
            application_id=application_id,
        ),
        external_id=managed_query_group_external_id(
            endpoint_id=endpoint_id,
            application_id=application_id,
        ),
        members=[SimpleNamespace(value=scim_id)],
    )

    with pytest.raises(RuntimeError, match="not bound to the exact live endpoint ACL"):
        cleanup._retire_live_endpoint_query_groups(
            client,
            endpoint_name=endpoint_name,
            endpoint_id=endpoint_id,
            endpoint_creator=_RUNTIME,
            principals=((application_id, scim_id),),
            assert_single_writer=lambda: pytest.fail("lease reached"),
        )

    assert client.groups.deleted == []


def test_cleanup_rechecks_exact_acl_after_lease_before_group_mutation() -> None:
    endpoint_name = "historical-endpoint"
    endpoint_id = "historical-endpoint-id"
    application_id = "app-client"
    scim_id = "app-scim"
    group_id = "exact-group-id"
    group_name = managed_query_group_name(
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    client = _Client(
        {
            endpoint_name: SimpleNamespace(
                id=endpoint_id,
                creator=_RUNTIME,
            ),
        },
        [],
    )
    client.groups.details[group_id] = SimpleNamespace(
        id=group_id,
        display_name=group_name,
        external_id=managed_query_group_external_id(
            endpoint_id=endpoint_id,
            application_id=application_id,
        ),
        members=[SimpleNamespace(value=scim_id)],
    )
    client.serving_endpoints.permissions[endpoint_id] = SimpleNamespace(
        access_control_list=[_query_permission(group_name)]
    )

    def lose_binding() -> None:
        client.serving_endpoints.permissions[endpoint_id] = SimpleNamespace(access_control_list=[])

    with pytest.raises(RuntimeError, match="ACL changed at deletion boundary"):
        cleanup._retire_live_endpoint_query_groups(
            client,
            endpoint_name=endpoint_name,
            endpoint_id=endpoint_id,
            endpoint_creator=_RUNTIME,
            principals=((application_id, scim_id),),
            assert_single_writer=lose_binding,
        )

    assert client.groups.deleted == []


def test_cleanup_never_sweeps_hash_shaped_group_without_a_live_endpoint() -> None:
    client = _Client({}, [])
    endpoint_id = "deleted-endpoint-id"
    application_id = "app-client"
    group_id = "foreign-orphan-id"
    client.groups.details[group_id] = SimpleNamespace(
        id=group_id,
        display_name=managed_query_group_name(
            endpoint_id=endpoint_id,
            application_id=application_id,
        ),
        external_id=managed_query_group_external_id(
            endpoint_id=endpoint_id,
            application_id=application_id,
        ),
        members=[SimpleNamespace(value="app-scim")],
    )
    empty = inventory.RuntimeEndpointInventory(1, _RUNTIME, (), ())

    result = inventory.cleanup_runtime_endpoints(
        client,
        empty,
        assert_single_writer=lambda: pytest.fail("lease reached"),
        query_principals=inventory.QueryGroupPrincipals(
            "app-client",
            "app-scim",
            "verifier-client",
            "verifier-scim",
            "proxy-client",
            "proxy-scim",
        ),
        timeout_s=1,
        inventory_again=lambda: empty,
        cleanup_journal=_MemoryCleanupJournal(),
    )

    assert result == empty
    assert client.groups.deleted == []


def test_cleanup_recovers_when_supervisor_delete_commits_then_times_out_and_leaves_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = _supervisor(
        supervisor_id="orphaned-supervisor",
        display_name=f"{_SUPERVISOR_NAME} [mip-agent-runtime-deadbeef0000]",
        endpoint="orphaned-supervisor-endpoint",
    )
    client = _Client(
        {
            supervisor["endpoint_name"]: SimpleNamespace(
                id="orphaned-endpoint-id",
                creator=_RUNTIME,
            ),
        },
        [supervisor],
    )
    initial = _inventory(client)
    empty = inventory.RuntimeEndpointInventory(1, _RUNTIME, (), ())
    reads = iter((initial, initial, empty))
    client.api_client.delete_endpoint_with_agent = False
    original_do = client.api_client.do

    def timeout_after_commit(
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
    ) -> Any:
        result = original_do(method, path, query=query)
        if method == "DELETE":
            raise TimeoutError("response lost after committed delete")
        return result

    client.api_client.do = timeout_after_commit  # type: ignore[method-assign]
    retired: list[str] = []
    monkeypatch.setattr(
        cleanup,
        "_retire_live_endpoint_query_groups",
        lambda _client, *, endpoint_name, **_kwargs: retired.append(endpoint_name),
    )

    result = inventory.cleanup_runtime_endpoints(
        client,
        initial,
        assert_single_writer=lambda: None,
        query_principals=inventory.QueryGroupPrincipals(
            "app-client",
            "app-scim",
            "verifier-client",
            "verifier-scim",
            "proxy-client",
            "proxy-scim",
        ),
        timeout_s=1,
        sleep=lambda _seconds: None,
        inventory_again=lambda: next(reads),
        cleanup_journal=_MemoryCleanupJournal(),
    )

    assert result == empty
    assert client.serving_endpoints.deleted == [supervisor["endpoint_name"]]
    assert retired == [supervisor["endpoint_name"]]


def test_cli_pin_parsers_require_complete_exact_json() -> None:
    parser = inventory.build_parser()
    common = [
        "inventory",
        "--runtime-application-id",
        _RUNTIME,
        "--catalog",
        _CATALOG,
        "--genie-space-id",
        _GENIE,
        "--app-name",
        "mip-app",
        "--rollback-scope",
        "mip-app-rollback",
        "--deployment-lease-id",
        "lease-id",
        "--deployment-source-git-sha",
        "a" * 40,
        "--out-json",
        "inventory.json",
    ]
    parsed = parser.parse_args(
        [
            *common,
            "--preserve-gateway-json",
            json.dumps(
                {
                    "name": DEFAULT_GATEWAY_ENDPOINT,
                    "endpoint_id": "endpoint-id",
                    "creator": _RUNTIME,
                }
            ),
        ]
    )
    assert parsed.preserve_gateway_json == [
        inventory.GatewayPin(
            DEFAULT_GATEWAY_ENDPOINT,
            "endpoint-id",
            _RUNTIME,
        )
    ]
    parsed = parser.parse_args(
        [
            *common,
            "--preserve-supervisor-json",
            json.dumps(
                {
                    "supervisor_id": "supervisor-id",
                    "endpoint": "supervisor-endpoint",
                    "endpoint_id": "supervisor-endpoint-id",
                    "creator": _RUNTIME,
                }
            ),
        ]
    )
    assert parsed.preserve_supervisor_json == [
        inventory.SupervisorPin(
            "supervisor-id",
            "supervisor-endpoint",
            "supervisor-endpoint-id",
            _RUNTIME,
        )
    ]

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                *common,
                "--preserve-gateway-json",
                '{"name":"mip-growth-agent-gateway"}',
            ]
        )
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                *common,
                "--preserve-gateway-name",
                DEFAULT_GATEWAY_ENDPOINT,
            ]
        )

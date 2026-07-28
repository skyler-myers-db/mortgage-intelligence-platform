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
    GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
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
from tools.databricks import historical_gateway_runtime_attestation as gateway_attestation
from tools.databricks import historical_supervisor_creation_retirement as creation_retirement
from tools.databricks import (
    historical_supervisor_retirement_attestation as supervisor_retirement_attestation,
)
from tools.databricks import reconcile_historical_agent_endpoints as inventory
from tools.databricks.gateway_legacy_rollback import (
    LEGACY_GATEWAY_RESOURCE_FIELDS,
    PRIOR_GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
    legacy_gateway_resource_digest,
    prior_v2_gateway_resource_digest,
)
from tools.databricks.serving_query_group_access import (
    managed_query_group_external_id,
    managed_query_group_name,
)
from tools.databricks.supervisor_agent_contract import supervisor_replacement_name

_RUNTIME = "runtime-client"
_CATALOG = "mip"
_GENIE = "genie-space"
_SUPERVISOR_NAME = "Mortgage Growth Agent"
_WORKSPACE_HOST = "https://adb-1234567890123456.7.azuredatabricks.net"


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
        self.examples = {row["supervisor_agent_id"]: [] for row in supervisors}
        self.omit_empty_examples = False
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
            examples = list(self.examples[path.split("/")[-2]])
            if self.omit_empty_examples and not examples:
                return {}
            return {"examples": examples}
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
        self.stale_reads_after_delete = 0
        self._pending_deletes: dict[str, int] = {}

    def list(self, *, filter: str | None = None) -> list[Any]:
        for group_id, remaining in tuple(self._pending_deletes.items()):
            if remaining == 0:
                self.details.pop(group_id, None)
                del self._pending_deletes[group_id]
            else:
                self._pending_deletes[group_id] = remaining - 1
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
        if self.stale_reads_after_delete:
            self._pending_deletes[group_id] = self.stale_reads_after_delete
        else:
            del self.details[group_id]


class _Client:
    def __init__(self, details: dict[str, Any], supervisors: list[dict[str, str]]) -> None:
        self.config = SimpleNamespace(host=_WORKSPACE_HOST)
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
    retirement_gateway_pins: tuple[inventory.GatewayPin, ...] = (),
    supervisor_pins: tuple[inventory.SupervisorPin, ...] = (),
    retirement_supervisor_pins: tuple[inventory.SupervisorPin, ...] = (),
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
        retirement_gateway_pins=retirement_gateway_pins,
        supervisor_pins=supervisor_pins,
        retirement_supervisor_pins=retirement_supervisor_pins,
        pending_supervisor_cleanup=pending_cleanup,
        pending_supervisor_creation=pending_creation,
        assert_single_writer=lambda: None,
        assert_supervisor_contract=lambda _supervisor_id, **kwargs: (
            contracts.append(kwargs["expected_contract"]) if contracts is not None else None
        ),
    )


def _trust_signed_historical(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gateway_attestation,
        "assert_live_historical_gateway_runtime_resources",
        lambda _workspace, *, environment: json.loads(
            environment["MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON"]
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
    _trust_signed_historical(monkeypatch)
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
        gateway_attestation,
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


def test_exact_signed_v2_resource_envelope_uses_narrow_legacy_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = DEFAULT_GATEWAY_ENDPOINT
    supervisor = _supervisor()
    current = _contract(
        gateway=candidate,
        gateway_id="runtime-gateway-id",
        supervisor=supervisor,
        supervisor_endpoint_id="supervisor-endpoint-id",
    )
    legacy = {key: value for key, value in current.items() if key in LEGACY_GATEWAY_RESOURCE_FIELDS}
    legacy["proof_version"] = GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION
    contract_json = json.dumps(legacy, sort_keys=True, separators=(",", ":"))
    binding = {
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON": contract_json,
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256": legacy_gateway_resource_digest(legacy),
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SIGNATURE": "signed-v2",
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY": TEST_GATEWAY_VERIFY_KEY,
    }
    details = SimpleNamespace(
        id=legacy["gateway_endpoint_id"],
        creator=_RUNTIME,
        pending_config=None,
        config=SimpleNamespace(served_entities=[SimpleNamespace(environment_vars=binding)]),
    )
    verified: list[dict[str, str]] = []

    def _verify_legacy(
        _workspace: object,
        *,
        expected: dict[str, str],
    ) -> dict[str, str]:
        verified.append(expected)
        return expected

    monkeypatch.setattr(
        gateway_attestation,
        "assert_live_legacy_gateway_resources",
        _verify_legacy,
    )
    monkeypatch.setattr(
        gateway_attestation,
        "assert_live_historical_gateway_runtime_resources",
        lambda *_args, **_kwargs: pytest.fail(
            "proxyless v2 must use the exact legacy live verifier"
        ),
    )

    result = inventory._live_gateway_contract(
        _Client({candidate: details}, [supervisor]),
        details,
        name=candidate,
        gateway_prefixes=(DEFAULT_GATEWAY_ENDPOINT,),
        runtime_application_id=_RUNTIME,
        supervisor_name=_SUPERVISOR_NAME,
        catalog=_CATALOG,
        genie_space_id=_GENIE,
        assert_single_writer=lambda: None,
    )

    assert result == legacy
    assert verified == [
        {
            **legacy,
            "resource_digest": legacy_gateway_resource_digest(legacy),
        }
    ]


def test_proxy_aware_v2_resource_envelope_uses_historical_current_schema_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = f"{DEFAULT_GATEWAY_ENDPOINT}-aa68b2596e3c"
    supervisor = _supervisor()
    contract = _contract(
        gateway=candidate,
        gateway_id="runtime-gateway-id",
        supervisor=supervisor,
        supervisor_endpoint_id="supervisor-endpoint-id",
    )
    details = _gateway_details(contract)
    verified: list[dict[str, str]] = []

    def _verify_current(
        _workspace: object,
        *,
        environment: dict[str, str],
    ) -> dict[str, str]:
        decoded = json.loads(environment["MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON"])
        verified.append(decoded)
        return decoded

    monkeypatch.setattr(
        gateway_attestation,
        "assert_live_historical_gateway_runtime_resources",
        _verify_current,
    )
    monkeypatch.setattr(
        gateway_attestation,
        "assert_live_legacy_gateway_resources",
        lambda *_args, **_kwargs: pytest.fail(
            "proxy-aware v2 must not enter the proxyless verifier"
        ),
    )

    result = inventory._live_gateway_contract(
        _Client({candidate: details}, [supervisor]),
        details,
        name=candidate,
        gateway_prefixes=(DEFAULT_GATEWAY_ENDPOINT,),
        runtime_application_id=_RUNTIME,
        supervisor_name=_SUPERVISOR_NAME,
        catalog=_CATALOG,
        genie_space_id=_GENIE,
        assert_single_writer=lambda: None,
    )

    assert result == contract
    assert verified == [contract]


def test_exact_prior_v2_proxy_envelope_uses_transition_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = DEFAULT_GATEWAY_ENDPOINT
    supervisor = _supervisor()
    current = _contract(
        gateway=candidate,
        gateway_id="runtime-gateway-id",
        supervisor=supervisor,
        supervisor_endpoint_id="supervisor-endpoint-id",
    )
    prior = {key: value for key, value in current.items() if key != "workspace_host"}
    prior["proof_version"] = PRIOR_GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION
    contract_json = json.dumps(prior, sort_keys=True, separators=(",", ":"))
    binding = {
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON": contract_json,
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256": prior_v2_gateway_resource_digest(prior),
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SIGNATURE": "signed-prior-v2",
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY": TEST_GATEWAY_VERIFY_KEY,
    }
    details = SimpleNamespace(
        id=prior["gateway_endpoint_id"],
        creator=_RUNTIME,
        pending_config=None,
        config=SimpleNamespace(served_entities=[SimpleNamespace(environment_vars=binding)]),
    )
    verified: list[dict[str, str]] = []
    monkeypatch.setattr(
        gateway_attestation,
        "assert_live_prior_v2_gateway_resources",
        lambda _workspace, *, expected: verified.append(expected) or expected,
    )
    monkeypatch.setattr(
        gateway_attestation,
        "assert_live_historical_gateway_runtime_resources",
        lambda *_args, **_kwargs: pytest.fail("prior v2 must not be accepted as current"),
    )

    result = inventory._live_gateway_contract(
        _Client({candidate: details}, [supervisor]),
        details,
        name=candidate,
        gateway_prefixes=(DEFAULT_GATEWAY_ENDPOINT,),
        runtime_application_id=_RUNTIME,
        supervisor_name=_SUPERVISOR_NAME,
        catalog=_CATALOG,
        genie_space_id=_GENIE,
        assert_single_writer=lambda: None,
    )

    assert result == prior
    assert verified == [
        {
            **prior,
            "resource_digest": prior_v2_gateway_resource_digest(prior),
        }
    ]


def test_prior_v2_transition_rejects_signed_scope_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = DEFAULT_GATEWAY_ENDPOINT
    supervisor = _supervisor()
    current = _contract(
        gateway=candidate,
        gateway_id="runtime-gateway-id",
        supervisor=supervisor,
        supervisor_endpoint_id="supervisor-endpoint-id",
    )
    prior = {key: value for key, value in current.items() if key != "workspace_host"}
    prior.update(
        proof_version=PRIOR_GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
        catalog="different_catalog",
    )
    binding = {
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON": json.dumps(
            prior, sort_keys=True, separators=(",", ":")
        ),
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256": prior_v2_gateway_resource_digest(prior),
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SIGNATURE": "signed-prior-v2",
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY": TEST_GATEWAY_VERIFY_KEY,
    }
    details = SimpleNamespace(
        id=prior["gateway_endpoint_id"],
        creator=_RUNTIME,
        pending_config=None,
        config=SimpleNamespace(served_entities=[SimpleNamespace(environment_vars=binding)]),
    )
    monkeypatch.setattr(
        gateway_attestation,
        "assert_live_prior_v2_gateway_resources",
        lambda _workspace, *, expected: expected,
    )

    with pytest.raises(RuntimeError, match="signed identity or scope drifted"):
        inventory._live_gateway_contract(
            _Client({candidate: details}, [supervisor]),
            details,
            name=candidate,
            gateway_prefixes=(DEFAULT_GATEWAY_ENDPOINT,),
            runtime_application_id=_RUNTIME,
            supervisor_name=_SUPERVISOR_NAME,
            catalog=_CATALOG,
            genie_space_id=_GENIE,
            assert_single_writer=lambda: None,
        )


def test_incomplete_v2_resource_envelope_never_uses_legacy_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = DEFAULT_GATEWAY_ENDPOINT
    supervisor = _supervisor()
    current = _contract(
        gateway=candidate,
        gateway_id="runtime-gateway-id",
        supervisor=supervisor,
        supervisor_endpoint_id="supervisor-endpoint-id",
    )
    legacy = {key: value for key, value in current.items() if key in LEGACY_GATEWAY_RESOURCE_FIELDS}
    legacy["proof_version"] = GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION
    legacy.pop("gateway_experiment_owner")
    binding = {
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON": json.dumps(
            legacy,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256": "digest",
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SIGNATURE": "signed-v2",
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY": TEST_GATEWAY_VERIFY_KEY,
    }
    details = SimpleNamespace(
        id="runtime-gateway-id",
        creator=_RUNTIME,
        pending_config=None,
        config=SimpleNamespace(served_entities=[SimpleNamespace(environment_vars=binding)]),
    )
    monkeypatch.setattr(
        gateway_attestation,
        "assert_live_legacy_gateway_resources",
        lambda *_args, **_kwargs: pytest.fail("partial v2 must remain fail-closed"),
    )

    with pytest.raises(RuntimeError, match="invalid runtime-resource proof"):
        inventory._live_gateway_contract(
            _Client({candidate: details}, [supervisor]),
            details,
            name=candidate,
            gateway_prefixes=(DEFAULT_GATEWAY_ENDPOINT,),
            runtime_application_id=_RUNTIME,
            supervisor_name=_SUPERVISOR_NAME,
            catalog=_CATALOG,
            genie_space_id=_GENIE,
            assert_single_writer=lambda: None,
        )


def _mixed_gateway_inventory_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_Client, dict[str, str], tuple[str, ...]]:
    supervisor = _supervisor()
    supervisor_endpoint_id = "supervisor-endpoint-id"
    proxyless_name = DEFAULT_GATEWAY_ENDPOINT
    proxy_aware_names = (
        f"{DEFAULT_GATEWAY_ENDPOINT}-aa68b2596e3c",
        f"{DEFAULT_GATEWAY_ENDPOINT}-f5bb6383fe3d",
    )
    unsigned_name = f"{DEFAULT_GATEWAY_ENDPOINT}-14609944fa02"
    names = (proxyless_name, unsigned_name, *proxy_aware_names)
    contracts = {
        name: _contract(
            gateway=name,
            gateway_id=f"{name}-id",
            supervisor=supervisor,
            supervisor_endpoint_id=supervisor_endpoint_id,
        )
        for name in names
    }
    proxyless = {
        key: value
        for key, value in contracts[proxyless_name].items()
        if key in LEGACY_GATEWAY_RESOURCE_FIELDS
    }
    proxyless["proof_version"] = GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION
    proxyless_binding = {
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON": json.dumps(
            proxyless,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256": legacy_gateway_resource_digest(proxyless),
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SIGNATURE": "signed-v2",
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY": TEST_GATEWAY_VERIFY_KEY,
    }
    client = _Client(
        {
            proxyless_name: SimpleNamespace(
                id=proxyless["gateway_endpoint_id"],
                creator=_RUNTIME,
                pending_config=None,
                config=SimpleNamespace(
                    served_entities=[SimpleNamespace(environment_vars=proxyless_binding)]
                ),
            ),
            **{name: _gateway_details(contracts[name]) for name in proxy_aware_names},
            unsigned_name: SimpleNamespace(
                id=contracts[unsigned_name]["gateway_endpoint_id"],
                creator=_RUNTIME,
                pending_config=None,
                config=SimpleNamespace(served_entities=[SimpleNamespace(environment_vars={})]),
            ),
            supervisor["endpoint_name"]: SimpleNamespace(
                id=supervisor_endpoint_id,
                creator=_RUNTIME,
            ),
        },
        [supervisor],
    )
    monkeypatch.setattr(
        gateway_attestation,
        "assert_live_legacy_gateway_resources",
        lambda _workspace, *, expected: expected,
    )
    _trust_signed_historical(monkeypatch)
    monkeypatch.setattr(
        gateway_attestation,
        "attest_legacy_gateway",
        lambda _workspace, _details, *, endpoint_name, **_kwargs: contracts[endpoint_name],
    )
    return client, supervisor, names


def test_mixed_proxyless_proxy_aware_and_unsigned_inventory_is_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, supervisor, names = _mixed_gateway_inventory_fixture(monkeypatch)

    result = _inventory(client)

    assert [(item.name, item.preserved) for item in result.gateways] == [
        (name, False) for name in sorted(names)
    ]
    assert [item.supervisor_id for item in result.supervisors] == [
        supervisor["supervisor_agent_id"]
    ]


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
        "DATABRICKS_HOST": _WORKSPACE_HOST,
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
        config=SimpleNamespace(host=_WORKSPACE_HOST),
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
    _trust_signed_historical(monkeypatch)

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


def test_live_gateway_pin_cannot_escape_attestation_outside_governed_family() -> None:
    name = "renamed-runtime-gateway"
    pin = inventory.GatewayPin(name, "renamed-gateway-id", _RUNTIME)
    client = _Client(
        {name: SimpleNamespace(id=pin.endpoint_id, creator=pin.creator)},
        [],
    )

    with pytest.raises(RuntimeError, match="Gateway remains live but was not attested"):
        _inventory(client, gateway_pins=(pin,))


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
    _trust_signed_historical(monkeypatch)

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


def _trust_retirement_journal(
    monkeypatch: pytest.MonkeyPatch,
    *,
    supervisor_pin: inventory.SupervisorPin,
    supervisor_create_time: str,
    gateway_pin: inventory.GatewayPin | None = None,
) -> None:
    journal = {
        "canonical_name": _SUPERVISOR_NAME,
        "old_id": supervisor_pin.supervisor_id,
        "old_endpoint": supervisor_pin.endpoint,
        "old_endpoint_id": supervisor_pin.endpoint_id,
        "old_creator": supervisor_pin.creator,
        "old_create_time": supervisor_create_time,
    }
    if gateway_pin is not None:
        journal.update(
            old_gateway_endpoint=gateway_pin.name,
            old_gateway_endpoint_id=gateway_pin.endpoint_id,
            old_gateway_creator=gateway_pin.creator,
            old_gateway_delete_allowed="1",
        )

    def read(_workspace: object, *, runtime_application_id: str) -> dict[str, str] | None:
        return journal if runtime_application_id == _RUNTIME else None

    monkeypatch.setattr(
        supervisor_retirement_attestation,
        "read_cutover_journal",
        read,
    )
    monkeypatch.setattr(
        gateway_attestation,
        "read_cutover_journal",
        read,
    )


def _retirement_supervisor_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    _Client,
    inventory.SupervisorPin,
    dict[str, str],
]:
    contract = json.loads(
        canonical_supervisor_contract_json(
            genie_space_id=_GENIE,
            catalog=_CATALOG,
        )
    )
    supervisor = {
        **_supervisor(
            supervisor_id="retirement-supervisor",
            endpoint="retirement-supervisor-endpoint",
        ),
        "description": contract["description"],
        "instructions": contract["instructions"],
    }
    endpoint_id = "retirement-supervisor-endpoint-id"
    client = _Client(
        {
            supervisor["endpoint_name"]: SimpleNamespace(
                id=endpoint_id,
                creator=_RUNTIME,
                pending_config=None,
                task="agent/v1/responses",
                state=SimpleNamespace(
                    ready="READY",
                    config_update="NOT_UPDATING",
                ),
            )
        },
        [supervisor],
    )
    reviewed_tool = contract["tools"][0]
    client.api_client.tools[supervisor["supervisor_agent_id"]] = [
        {
            **reviewed_tool,
            "name": (
                f"supervisor-agents/{supervisor['supervisor_agent_id']}/tools/"
                f"{reviewed_tool['tool_id']}"
            ),
            "genie_space": {
                **reviewed_tool["genie_space"],
                "space_id": reviewed_tool["genie_space"]["id"],
            },
        }
    ]
    client.api_client.omit_empty_examples = True
    pin = inventory.SupervisorPin(
        supervisor_id=supervisor["supervisor_agent_id"],
        endpoint=supervisor["endpoint_name"],
        endpoint_id=endpoint_id,
        creator=_RUNTIME,
    )
    _trust_retirement_journal(
        monkeypatch,
        supervisor_pin=pin,
        supervisor_create_time=supervisor["create_time"],
    )
    return client, pin, supervisor


def test_retirement_only_supervisor_pin_accepts_exact_reviewed_historical_subset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, pin, _supervisor_row = _retirement_supervisor_fixture(monkeypatch)

    result = _inventory(
        client,
        retirement_supervisor_pins=(pin,),
    )

    assert len(result.supervisors) == 1
    reviewed = result.supervisors[0]
    assert reviewed.preserved is True
    assert reviewed.supervisor_id == pin.supervisor_id
    evidence = json.loads(reviewed.contract_json)
    assert evidence["kind"] == "signed-cutover-retirement-predecessor"
    assert [tool["tool_id"] for tool in evidence["tools"]] == ["mortgage_data_analyst"]
    assert (
        reviewed.contract_sha256
        == __import__("hashlib").sha256(reviewed.contract_json.encode()).hexdigest()
    )


def test_retirement_supervisor_name_drift_cannot_escape_attestation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, pin, _supervisor_row = _retirement_supervisor_fixture(monkeypatch)
    client.api_client.supervisors[pin.supervisor_id]["display_name"] = "Renamed Agent"

    with pytest.raises(RuntimeError, match="Supervisor remains live but was not attested"):
        _inventory(client, retirement_supervisor_pins=(pin,))


def test_retirement_only_supervisor_pin_requires_signed_cutover_journal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, pin, _supervisor_row = _retirement_supervisor_fixture(monkeypatch)
    monkeypatch.setattr(
        supervisor_retirement_attestation,
        "read_cutover_journal",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="not bound to the signed cutover journal"):
        _inventory(
            client,
            retirement_supervisor_pins=(pin,),
        )


def test_retirement_only_supervisor_pin_rejects_signed_create_time_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, pin, _supervisor_row = _retirement_supervisor_fixture(monkeypatch)
    monkeypatch.setattr(
        supervisor_retirement_attestation,
        "read_cutover_journal",
        lambda *_args, **_kwargs: {
            "canonical_name": _SUPERVISOR_NAME,
            "old_id": pin.supervisor_id,
            "old_endpoint": pin.endpoint,
            "old_endpoint_id": pin.endpoint_id,
            "old_creator": pin.creator,
            "old_create_time": "2026-07-23T00:00:00Z",
        },
    )

    with pytest.raises(RuntimeError, match="not bound to the signed cutover journal"):
        _inventory(
            client,
            retirement_supervisor_pins=(pin,),
        )


def test_retirement_only_supervisor_pin_rejects_unreviewed_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, pin, _supervisor_row = _retirement_supervisor_fixture(monkeypatch)
    client.api_client.tools[pin.supervisor_id].append(
        {
            "tool_id": "unreviewed",
            "tool_type": "uc_function",
            "description": "outside policy",
            "uc_function": {"name": "other.schema.function"},
        }
    )

    with pytest.raises(RuntimeError, match="outside the reviewed contract"):
        _inventory(
            client,
            retirement_supervisor_pins=(pin,),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("name", "supervisor-agents/other/tools/mortgage_data_analyst", "provider identity"),
        ("unexpected", "value", "unexpected fields"),
    ),
)
def test_retirement_only_supervisor_pin_rejects_provider_tool_metadata_drift(
    field: str,
    value: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, pin, _supervisor_row = _retirement_supervisor_fixture(monkeypatch)
    client.api_client.tools[pin.supervisor_id][0][field] = value

    with pytest.raises(RuntimeError, match=message):
        _inventory(
            client,
            retirement_supervisor_pins=(pin,),
        )


def test_retirement_only_supervisor_pin_rejects_provider_space_id_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, pin, _supervisor_row = _retirement_supervisor_fixture(monkeypatch)
    client.api_client.tools[pin.supervisor_id][0]["genie_space"]["space_id"] = "other-space"

    with pytest.raises(RuntimeError, match="reviewed body drifted"):
        _inventory(
            client,
            retirement_supervisor_pins=(pin,),
        )


def test_retirement_only_supervisor_pin_requires_reviewed_tool_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, pin, _supervisor_row = _retirement_supervisor_fixture(monkeypatch)
    client.api_client.tools[pin.supervisor_id] = []

    with pytest.raises(RuntimeError, match="no reviewed tool evidence"):
        _inventory(
            client,
            retirement_supervisor_pins=(pin,),
        )


def test_retirement_only_supervisor_pin_rejects_examples_and_unstable_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, pin, _supervisor_row = _retirement_supervisor_fixture(monkeypatch)
    client.api_client.examples[pin.supervisor_id] = [{"example_id": "unreviewed"}]

    with pytest.raises(RuntimeError, match="unreviewed examples"):
        _inventory(
            client,
            retirement_supervisor_pins=(pin,),
        )

    client.api_client.examples[pin.supervisor_id] = []
    client.serving_endpoints.details[pin.endpoint].state.config_update = "UPDATING"
    with pytest.raises(RuntimeError, match="endpoint is not stable"):
        _inventory(
            client,
            retirement_supervisor_pins=(pin,),
        )


def test_retirement_only_supervisor_pin_cannot_cover_active_gateway_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, pin, supervisor = _retirement_supervisor_fixture(monkeypatch)
    gateway = _contract(
        gateway=DEFAULT_GATEWAY_ENDPOINT,
        gateway_id="gateway-id",
        supervisor=supervisor,
        supervisor_endpoint_id=pin.endpoint_id,
    )
    client.serving_endpoints.details[DEFAULT_GATEWAY_ENDPOINT] = _gateway_details(gateway)
    _trust_signed_historical(monkeypatch)
    gateway_pin = inventory.GatewayPin(
        DEFAULT_GATEWAY_ENDPOINT,
        "gateway-id",
        _RUNTIME,
    )

    with pytest.raises(
        RuntimeError,
        match="still referenced by an active preserved Gateway",
    ):
        _inventory(
            client,
            gateway_pins=(gateway_pin,),
            retirement_supervisor_pins=(pin,),
        )


def test_signed_journal_gateway_and_supervisor_pair_are_preserved_for_retirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, supervisor_pin, supervisor = _retirement_supervisor_fixture(monkeypatch)
    gateway_name = f"{DEFAULT_GATEWAY_ENDPOINT}-deadbeef0000"
    gateway_id = "retirement-gateway-id"
    gateway = _contract(
        gateway=gateway_name,
        gateway_id=gateway_id,
        supervisor=supervisor,
        supervisor_endpoint_id=supervisor_pin.endpoint_id,
    )
    client.serving_endpoints.details[gateway_name] = _gateway_details(gateway)
    gateway_pin = inventory.GatewayPin(gateway_name, gateway_id, _RUNTIME)
    _trust_signed_historical(monkeypatch)
    _trust_retirement_journal(
        monkeypatch,
        supervisor_pin=supervisor_pin,
        supervisor_create_time=supervisor["create_time"],
        gateway_pin=gateway_pin,
    )

    result = _inventory(
        client,
        retirement_gateway_pins=(gateway_pin,),
        retirement_supervisor_pins=(supervisor_pin,),
    )

    assert [(item.name, item.preserved) for item in result.gateways] == [(gateway_name, True)]
    assert [(item.supervisor_id, item.preserved) for item in result.supervisors] == [
        (supervisor_pin.supervisor_id, True)
    ]


def test_signed_journal_gateway_preserves_its_strict_current_supervisor_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = _supervisor()
    gateway_name = f"{DEFAULT_GATEWAY_ENDPOINT}-deadbeef0000"
    gateway_id = "retirement-gateway-id"
    gateway = _contract(
        gateway=gateway_name,
        gateway_id=gateway_id,
        supervisor=supervisor,
        supervisor_endpoint_id="supervisor-endpoint-id",
    )
    client = _Client(
        {
            gateway_name: _gateway_details(gateway),
            supervisor["endpoint_name"]: SimpleNamespace(
                id="supervisor-endpoint-id",
                creator=_RUNTIME,
            ),
        },
        [supervisor],
    )
    gateway_pin = inventory.GatewayPin(gateway_name, gateway_id, _RUNTIME)
    _trust_signed_historical(monkeypatch)
    monkeypatch.setattr(
        gateway_attestation,
        "read_cutover_journal",
        lambda _workspace, *, runtime_application_id: {
            "canonical_name": _SUPERVISOR_NAME,
            "old_gateway_endpoint": gateway_pin.name,
            "old_gateway_endpoint_id": gateway_pin.endpoint_id,
            "old_gateway_creator": gateway_pin.creator,
            "old_gateway_delete_allowed": "1",
        }
        if runtime_application_id == _RUNTIME
        else None,
    )

    result = _inventory(
        client,
        retirement_gateway_pins=(gateway_pin,),
    )

    assert result.gateways[0].preserved is True
    assert result.supervisors[0].preserved is True
    assert json.loads(result.supervisors[0].contract_json)["tools"][0]["tool_id"] == (
        "mortgage_data_analyst"
    )


def test_retirement_only_gateway_requires_signed_cutover_journal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = _supervisor()
    gateway_name = f"{DEFAULT_GATEWAY_ENDPOINT}-deadbeef0000"
    gateway = _contract(
        gateway=gateway_name,
        gateway_id="retirement-gateway-id",
        supervisor=supervisor,
        supervisor_endpoint_id="supervisor-endpoint-id",
    )
    client = _Client(
        {
            gateway_name: _gateway_details(gateway),
            supervisor["endpoint_name"]: SimpleNamespace(
                id="supervisor-endpoint-id",
                creator=_RUNTIME,
            ),
        },
        [supervisor],
    )
    _trust_signed_historical(monkeypatch)
    monkeypatch.setattr(
        gateway_attestation,
        "read_cutover_journal",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="not bound to the signed cutover journal"):
        _inventory(
            client,
            retirement_gateway_pins=(
                inventory.GatewayPin(
                    gateway_name,
                    "retirement-gateway-id",
                    _RUNTIME,
                ),
            ),
        )


def test_active_and_retirement_only_supervisor_pins_must_be_disjoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, pin, _supervisor_row = _retirement_supervisor_fixture(monkeypatch)

    with pytest.raises(ValueError, match="must be disjoint"):
        _inventory(
            client,
            supervisor_pins=(pin,),
            retirement_supervisor_pins=(pin,),
        )


def test_active_and_retirement_only_gateway_pins_must_be_disjoint() -> None:
    client = _Client({}, [])
    pin = inventory.GatewayPin(DEFAULT_GATEWAY_ENDPOINT, "gateway-id", _RUNTIME)

    with pytest.raises(ValueError, match="Gateway preservation pins must be disjoint"):
        _inventory(
            client,
            gateway_pins=(pin,),
            retirement_gateway_pins=(pin,),
        )


def test_retirement_only_supervisor_preservation_rejects_more_than_one_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, pin, _supervisor_row = _retirement_supervisor_fixture(monkeypatch)

    with pytest.raises(ValueError, match="one signed journal tuple"):
        _inventory(
            client,
            retirement_supervisor_pins=(pin, pin),
        )


def test_retirement_only_gateway_preservation_rejects_more_than_one_tuple() -> None:
    client = _Client({}, [])
    pin = inventory.GatewayPin(DEFAULT_GATEWAY_ENDPOINT, "gateway-id", _RUNTIME)

    with pytest.raises(ValueError, match="one signed journal tuple"):
        _inventory(
            client,
            retirement_gateway_pins=(pin, pin),
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
    _trust_signed_historical(monkeypatch)

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
    _trust_signed_historical(monkeypatch)
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


def test_cleanup_retires_exact_signed_prior_v2_before_green_provisioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = _supervisor(
        supervisor_id="prior-v2-supervisor",
        display_name=f"{_SUPERVISOR_NAME} [mip-agent-runtime-deadbeef0000]",
        endpoint="prior-v2-supervisor-endpoint",
    )
    gateway_name = f"{DEFAULT_GATEWAY_ENDPOINT}-deadbeef0000"
    current = _contract(
        gateway=gateway_name,
        gateway_id="prior-v2-gateway-id",
        supervisor=supervisor,
        supervisor_endpoint_id="prior-v2-supervisor-endpoint-id",
    )
    prior = {key: value for key, value in current.items() if key != "workspace_host"}
    prior["proof_version"] = PRIOR_GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION
    binding = {
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON": json.dumps(
            prior, sort_keys=True, separators=(",", ":")
        ),
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256": prior_v2_gateway_resource_digest(prior),
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SIGNATURE": "signed-prior-v2",
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY": TEST_GATEWAY_VERIFY_KEY,
    }
    details = SimpleNamespace(
        id=prior["gateway_endpoint_id"],
        creator=_RUNTIME,
        pending_config=None,
        config=SimpleNamespace(served_entities=[SimpleNamespace(environment_vars=binding)]),
    )
    client = _Client(
        {
            gateway_name: details,
            supervisor["endpoint_name"]: SimpleNamespace(
                id="prior-v2-supervisor-endpoint-id",
                creator=_RUNTIME,
            ),
        },
        [supervisor],
    )
    verified: list[dict[str, str]] = []
    monkeypatch.setattr(
        gateway_attestation,
        "assert_live_prior_v2_gateway_resources",
        lambda _workspace, *, expected: verified.append(expected) or expected,
    )

    initial = _inventory(client)
    empty = inventory.RuntimeEndpointInventory(1, _RUNTIME, (), ())
    after_gateway = inventory.RuntimeEndpointInventory(1, _RUNTIME, (), initial.supervisors)
    reads = iter((initial, initial, after_gateway, empty))
    final = inventory.cleanup_runtime_endpoints(
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

    assert final == empty
    assert client.serving_endpoints.deleted == [gateway_name]
    assert client.api_client.deleted == [supervisor["supervisor_agent_id"]]
    assert verified == [
        {**prior, "resource_digest": prior_v2_gateway_resource_digest(prior)}
    ]


def test_mixed_inventory_cleanup_recovers_interruption_and_preserves_signed_retirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, active_supervisor, gateway_names = _mixed_gateway_inventory_fixture(monkeypatch)
    retirement_client, retirement_pin, retirement_supervisor = _retirement_supervisor_fixture(
        monkeypatch
    )
    client.serving_endpoints.details[retirement_pin.endpoint] = (
        retirement_client.serving_endpoints.details[retirement_pin.endpoint]
    )
    client.api_client.supervisors[retirement_pin.supervisor_id] = dict(retirement_supervisor)
    client.api_client.tools[retirement_pin.supervisor_id] = list(
        retirement_client.api_client.tools[retirement_pin.supervisor_id]
    )
    client.api_client.examples[retirement_pin.supervisor_id] = []
    client.api_client.omit_empty_examples = True

    def read_inventory() -> inventory.RuntimeEndpointInventory:
        return _inventory(
            client,
            retirement_supervisor_pins=(retirement_pin,),
        )

    initial = read_inventory()
    assert len(initial.gateways) == 4
    assert len(initial.supervisors) == 2

    # A previous process committed one exact Gateway deletion and died before
    # recording completion. The retry starts from a freshly attested inventory.
    interrupted_gateway = sorted(gateway_names)[0]
    client.serving_endpoints.delete(interrupted_gateway)
    retry_inventory = read_inventory()
    assert len(retry_inventory.gateways) == 3

    original_delete = client.serving_endpoints.delete
    timeout_injected = False

    def timeout_once_after_commit(name: str) -> None:
        nonlocal timeout_injected
        original_delete(name)
        if not timeout_injected:
            timeout_injected = True
            raise TimeoutError("response lost after committed Gateway delete")

    client.serving_endpoints.delete = timeout_once_after_commit  # type: ignore[method-assign]
    final = inventory.cleanup_runtime_endpoints(
        client,
        retry_inventory,
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
        inventory_again=read_inventory,
        cleanup_journal=_MemoryCleanupJournal(),
    )

    assert timeout_injected is True
    assert sorted(client.serving_endpoints.deleted) == sorted(gateway_names)
    assert client.api_client.deleted == [active_supervisor["supervisor_agent_id"]]
    assert [item.supervisor_id for item in final.supervisors] == [retirement_pin.supervisor_id]
    assert final.supervisors[0].preserved is True
    assert creation_retirement.cleanup_postflight_is_complete(final) is True


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
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
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
    assert lease_checks == ["lease", "lease"]

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
    assert lease_checks == ["lease", "lease"]


@pytest.mark.parametrize("response_lost_after_commit", [False, True])
def test_cleanup_waits_for_delayed_scim_group_deletion(
    monkeypatch: pytest.MonkeyPatch,
    response_lost_after_commit: bool,
) -> None:
    endpoint_name = "historical-endpoint"
    endpoint_id = "historical-endpoint-id"
    application_id = "app-client"
    scim_id = "app-scim"
    group_id = "exact-group-id"
    client = _Client(
        {
            endpoint_name: SimpleNamespace(
                id=endpoint_id,
                creator=_RUNTIME,
            ),
        },
        [],
    )
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
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
    )
    client.groups.stale_reads_after_delete = 2
    client.serving_endpoints.permissions[endpoint_id] = SimpleNamespace(
        access_control_list=[_query_permission(group_name)]
    )
    if response_lost_after_commit:
        committed_delete = client.groups.delete

        def lose_delete_response(target_group_id: str) -> None:
            committed_delete(target_group_id)
            raise TimeoutError("SCIM response was lost after commit")

        monkeypatch.setattr(client.groups, "delete", lose_delete_response)
    sleeps: list[float] = []

    cleanup._retire_live_endpoint_query_groups(
        client,
        endpoint_name=endpoint_name,
        endpoint_id=endpoint_id,
        endpoint_creator=_RUNTIME,
        principals=((application_id, scim_id),),
        assert_single_writer=lambda: None,
        timeout_s=10,
        sleep=sleeps.append,
    )

    assert client.groups.deleted == [group_id]
    assert group_id not in client.groups.details
    assert sleeps == [2, 2, 2, 2, 2]
    assert client.serving_endpoints.get(endpoint_name).id == endpoint_id


def test_cleanup_rechecks_lease_immediately_before_group_delete() -> None:
    endpoint_name = "historical-endpoint"
    endpoint_id = "historical-endpoint-id"
    application_id = "app-client"
    scim_id = "app-scim"
    group_id = "exact-group-id"
    client = _Client(
        {
            endpoint_name: SimpleNamespace(
                id=endpoint_id,
                creator=_RUNTIME,
            ),
        },
        [],
    )
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
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
    )
    client.serving_endpoints.permissions[endpoint_id] = SimpleNamespace(
        access_control_list=[_query_permission(group_name)]
    )
    lease_checks = 0

    def lose_lease_at_mutation_boundary() -> None:
        nonlocal lease_checks
        lease_checks += 1
        if lease_checks == 2:
            raise RuntimeError("deployment lease lost")

    with pytest.raises(RuntimeError, match="deployment lease lost"):
        cleanup._retire_live_endpoint_query_groups(
            client,
            endpoint_name=endpoint_name,
            endpoint_id=endpoint_id,
            endpoint_creator=_RUNTIME,
            principals=((application_id, scim_id),),
            assert_single_writer=lose_lease_at_mutation_boundary,
        )

    assert lease_checks == 2
    assert client.groups.deleted == []
    assert group_id in client.groups.details


def test_cleanup_rejects_same_name_replacement_during_group_postflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint_name = "historical-endpoint"
    endpoint_id = "historical-endpoint-id"
    application_id = "app-client"
    scim_id = "app-scim"
    group_id = "exact-group-id"
    replacement_id = "replacement-group-id"
    client = _Client(
        {
            endpoint_name: SimpleNamespace(
                id=endpoint_id,
                creator=_RUNTIME,
            ),
        },
        [],
    )
    group_name = managed_query_group_name(
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    external_id = managed_query_group_external_id(
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    client.groups.details[group_id] = SimpleNamespace(
        id=group_id,
        display_name=group_name,
        external_id=external_id,
        members=[SimpleNamespace(value=scim_id)],
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
    )
    client.serving_endpoints.permissions[endpoint_id] = SimpleNamespace(
        access_control_list=[_query_permission(group_name)]
    )

    def replace_after_delete(target_group_id: str) -> None:
        assert target_group_id == group_id
        client.groups.deleted.append(target_group_id)
        del client.groups.details[target_group_id]
        client.groups.details[replacement_id] = SimpleNamespace(
            id=replacement_id,
            display_name=group_name,
            external_id=external_id,
            members=[SimpleNamespace(value=scim_id)],
            meta=SimpleNamespace(resource_type="WorkspaceGroup"),
        )

    monkeypatch.setattr(client.groups, "delete", replace_after_delete)

    with pytest.raises(RuntimeError, match="deterministic binding changed"):
        cleanup._retire_live_endpoint_query_groups(
            client,
            endpoint_name=endpoint_name,
            endpoint_id=endpoint_id,
            endpoint_creator=_RUNTIME,
            principals=((application_id, scim_id),),
            assert_single_writer=lambda: None,
            sleep=lambda _seconds: None,
        )

    assert client.groups.deleted == [group_id]
    assert replacement_id in client.groups.details
    assert client.serving_endpoints.get(endpoint_name).id == endpoint_id


def test_cleanup_does_not_accept_transient_false_group_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint_name = "historical-endpoint"
    endpoint_id = "historical-endpoint-id"
    application_id = "app-client"
    scim_id = "app-scim"
    group_id = "exact-group-id"
    client = _Client(
        {
            endpoint_name: SimpleNamespace(
                id=endpoint_id,
                creator=_RUNTIME,
            ),
        },
        [],
    )
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
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
    )
    client.serving_endpoints.permissions[endpoint_id] = SimpleNamespace(
        access_control_list=[_query_permission(group_name)]
    )
    deletion_attempted = False
    exact_reads = 0
    bound_reads = 0
    original_get = client.groups.get
    original_list = client.groups.list

    def no_op_delete(target_group_id: str) -> None:
        nonlocal deletion_attempted
        assert target_group_id == group_id
        deletion_attempted = True
        client.groups.deleted.append(target_group_id)

    def transient_get(target_group_id: str) -> Any:
        nonlocal exact_reads
        if deletion_attempted and target_group_id == group_id:
            exact_reads += 1
            if exact_reads == 1:
                raise NotFound("transient exact-ID false absence")
        return original_get(target_group_id)

    def transient_list(*, filter: str | None = None) -> list[Any]:
        nonlocal bound_reads
        if deletion_attempted:
            bound_reads += 1
            if bound_reads == 1:
                return []
        return original_list(filter=filter)

    monkeypatch.setattr(client.groups, "delete", no_op_delete)
    monkeypatch.setattr(client.groups, "get", transient_get)
    monkeypatch.setattr(client.groups, "list", transient_list)
    now = [0.0]

    def advance(seconds: float) -> None:
        now[0] += seconds

    with pytest.raises(RuntimeError, match="retirement did not converge"):
        cleanup._retire_live_endpoint_query_groups(
            client,
            endpoint_name=endpoint_name,
            endpoint_id=endpoint_id,
            endpoint_creator=_RUNTIME,
            principals=((application_id, scim_id),),
            assert_single_writer=lambda: None,
            timeout_s=3,
            sleep=advance,
            clock=lambda: now[0],
        )

    assert client.groups.deleted == [group_id]
    assert group_id in client.groups.details
    assert client.serving_endpoints.get(endpoint_name).id == endpoint_id


def test_cleanup_bounds_uncommitted_scim_group_deletion_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint_name = "historical-endpoint"
    endpoint_id = "historical-endpoint-id"
    application_id = "app-client"
    scim_id = "app-scim"
    group_id = "exact-group-id"
    client = _Client(
        {
            endpoint_name: SimpleNamespace(
                id=endpoint_id,
                creator=_RUNTIME,
            ),
        },
        [],
    )
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
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
    )
    client.serving_endpoints.permissions[endpoint_id] = SimpleNamespace(
        access_control_list=[_query_permission(group_name)]
    )
    delete_attempts: list[str] = []

    def fail_before_commit(target_group_id: str) -> None:
        delete_attempts.append(target_group_id)
        raise TimeoutError("SCIM delete did not commit")

    monkeypatch.setattr(client.groups, "delete", fail_before_commit)
    now = [0.0]
    sleeps: list[float] = []

    def advance(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    with pytest.raises(
        RuntimeError,
        match="historical managed query group delete failed and absence is unproven",
    ):
        cleanup._retire_live_endpoint_query_groups(
            client,
            endpoint_name=endpoint_name,
            endpoint_id=endpoint_id,
            endpoint_creator=_RUNTIME,
            principals=((application_id, scim_id),),
            assert_single_writer=lambda: None,
            timeout_s=3,
            sleep=advance,
            clock=lambda: now[0],
        )

    assert delete_attempts == [group_id]
    assert group_id in client.groups.details
    assert sleeps == [2, 2]


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
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
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
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
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
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
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
            "--preserve-retirement-gateway-json",
            json.dumps(
                {
                    "name": f"{DEFAULT_GATEWAY_ENDPOINT}-deadbeef0000",
                    "endpoint_id": "retirement-endpoint-id",
                    "creator": _RUNTIME,
                }
            ),
        ]
    )
    assert parsed.preserve_retirement_gateway_json == [
        inventory.GatewayPin(
            f"{DEFAULT_GATEWAY_ENDPOINT}-deadbeef0000",
            "retirement-endpoint-id",
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
    parsed = parser.parse_args(
        [
            *common,
            "--preserve-retirement-supervisor-json",
            json.dumps(
                {
                    "supervisor_id": "retirement-supervisor-id",
                    "endpoint": "retirement-supervisor-endpoint",
                    "endpoint_id": "retirement-supervisor-endpoint-id",
                    "creator": _RUNTIME,
                }
            ),
        ]
    )
    assert parsed.preserve_retirement_supervisor_json == [
        inventory.SupervisorPin(
            "retirement-supervisor-id",
            "retirement-supervisor-endpoint",
            "retirement-supervisor-endpoint-id",
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

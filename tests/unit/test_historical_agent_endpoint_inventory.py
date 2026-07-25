from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.agents.gateway_contract import DEFAULT_GATEWAY_ENDPOINT
from tests.fixtures.gateway_runtime_resources import TEST_GATEWAY_VERIFY_KEY
from tests.unit.test_reconcile_historical_agent_endpoints import (
    _RUNTIME,
    _Client,
    _contract,
    _gateway_details,
    _inventory,
    _supervisor,
)
from tools.databricks import reconcile_historical_agent_endpoints as inventory


def _foundation_endpoint(model: str = "system.ai.meta_llama_v3_3_70b_instruct") -> object:
    return SimpleNamespace(
        id=None,
        creator=None,
        config=SimpleNamespace(
            served_entities=[
                SimpleNamespace(
                    foundation_model=SimpleNamespace(name=model),
                )
            ]
        ),
    )


@pytest.mark.parametrize(
    "rows",
    [
        [{"name": ""}],
        [{"name": "duplicate"}, {"name": "duplicate"}],
    ],
    ids=["blank-name", "duplicate-name"],
)
def test_inventory_rejects_malformed_global_endpoint_list_before_attestation(
    rows: list[dict[str, str]],
) -> None:
    client = _Client({}, [])
    client.serving_endpoints.list = lambda: rows  # type: ignore[method-assign]

    with pytest.raises(
        RuntimeError,
        match="serving endpoint inventory has a duplicate or missing name",
    ):
        _inventory(client)

    assert client.serving_endpoints.deleted == []
    assert client.api_client.deleted == []


def test_inventory_excludes_valid_platform_foundation_endpoint() -> None:
    client = _Client({"databricks-llama": _foundation_endpoint()}, [])

    result = _inventory(client)

    assert result.gateways == ()
    assert result.supervisors == ()


def test_inventory_mixes_foundation_and_customer_endpoints_without_weakening_pins() -> None:
    client = _Client(
        {
            "databricks-llama": _foundation_endpoint(),
            "unrelated-customer-endpoint": SimpleNamespace(
                id="customer-endpoint-id",
                creator="customer@example.com",
            ),
        },
        [],
    )

    result = _inventory(client)

    assert result.gateways == ()
    assert result.supervisors == ()


def test_foundation_endpoint_cannot_impersonate_preserved_gateway() -> None:
    client = _Client({DEFAULT_GATEWAY_ENDPOINT: _foundation_endpoint()}, [])

    with pytest.raises(
        RuntimeError,
        match="foundation endpoint collides with a preserved or pending runtime tuple",
    ):
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


def test_foundation_endpoint_cannot_consume_pending_supervisor_cleanup_proof() -> None:
    supervisor = _supervisor()
    client = _Client(
        {supervisor["endpoint_name"]: _foundation_endpoint()},
        [],
    )

    with pytest.raises(
        RuntimeError,
        match="foundation endpoint collides with a preserved or pending runtime tuple",
    ):
        _inventory(
            client,
            pending_cleanup=inventory.SupervisorCleanupProof(
                app_name="mip-app",
                lease_id="4f29fc88-85fd-4c6e-bffd-42841e54b50e",
                source_git_sha="a" * 40,
                runtime_application_id=_RUNTIME,
                supervisor_id=supervisor["supervisor_agent_id"],
                endpoint=supervisor["endpoint_name"],
                endpoint_id="retired-supervisor-endpoint-id",
                creator=_RUNTIME,
            ),
        )


def test_inventory_rejects_duplicate_global_endpoint_ids() -> None:
    client = _Client(
        {
            "first-endpoint": SimpleNamespace(id="duplicate-id", creator="human-1"),
            "second-endpoint": SimpleNamespace(id="duplicate-id", creator="human-2"),
        },
        [],
    )

    with pytest.raises(RuntimeError, match="immutable ID"):
        _inventory(client)

    assert client.serving_endpoints.deleted == []
    assert client.api_client.deleted == []


def test_inventory_attests_non_hash_legacy_name_in_platform_reserved_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY",
        TEST_GATEWAY_VERIFY_KEY,
    )
    supervisor = _supervisor()
    gateway_name = f"{DEFAULT_GATEWAY_ENDPOINT}-legacy-blue"
    contract = _contract(
        gateway=gateway_name,
        gateway_id="legacy-blue-id",
        supervisor=supervisor,
        supervisor_endpoint_id="supervisor-endpoint-id",
    )
    client = _Client(
        {
            gateway_name: _gateway_details(contract),
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

    result = _inventory(client)

    assert [item.name for item in result.gateways] == [gateway_name]
